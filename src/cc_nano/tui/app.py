"""cc-nano 入口点 —— argparse、引擎设置和交互式 REPL。"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from prompt_toolkit.history import FileHistory
from rich.console import Console

from cc_nano.buddy.companion import get_companion
from cc_nano.buddy.observer import _is_addressed, fire_companion_observer
from cc_nano.buddy.storage import load_companion_muted
from cc_nano.commands import CommandContext, handle_command, parse_command
from cc_nano.commands.project_commands import (handle_change, handle_delete,
                                               handle_list, handle_new,
                                               handle_status)
from cc_nano.core.config import AppConfig, load_app_config
from cc_nano.core.context import build_system_prompt
from cc_nano.core.engine import Engine
from cc_nano.core.permissions import PermissionChecker
from cc_nano.core.project import (find_project_root,
                                  get_global_current_project, get_project_root,
                                  list_all_projects, set_project_root)
from cc_nano.core.session import SessionStore
from cc_nano.features.agents import EXPLORE_SYSTEM_PROMPT, WorkerManager
from cc_nano.features.compact import (CompactService, estimate_tokens,
                                      should_compact)
from cc_nano.features.coordinator import (current_session_mode,
                                          get_worker_system_prompt,
                                          is_coordinator_mode,
                                          set_coordinator_mode,
                                          set_teamwork_mode)
from cc_nano.features.cost_tracker import CostTracker
from cc_nano.features.memory import (append_to_daily_log, build_dream_prompt,
                                     ensure_memory_dir, extract_memory_tags,
                                     read_last_consolidated_at,
                                     record_consolidation, release_lock,
                                     should_auto_dream, try_acquire_lock)
from cc_nano.features.sandbox.config import load_sandbox_config
from cc_nano.features.sandbox.manager import SandboxManager
from cc_nano.features.skills import (build_skills_prompt_section,
                                     discover_skills)
from cc_nano.features.skills_bundled import register_bundled_skills
from cc_nano.features.todo import TodoManager
from cc_nano.tools import (AgentTool, AskUserQuestionTool, BashTool,
                           FileEditTool, FileReadTool, FileWriteTool, GlobTool,
                           GrepTool, SendMessageTool, SkillTool, TaskStopTool,
                           TodoUpdateTool, TodoWriteTool)
from cc_nano.tui.input_parser import parse_input
from cc_nano.tui.prompt import bordered_prompt, slash_completer
from cc_nano.tui.query import run_query
from cc_nano.tui.shell import handle_sandbox_command, run_shell

console = Console()

# 使用双击超时 DOUBLE_PRESS_TIMEOUT_MS = 800
_DOUBLE_PRESS_TIMEOUT_MS = 0.8


def _run_dream(
    engine: Engine,
    memory_dir: Path,
    permissions: PermissionChecker,
    quiet: bool = False,
    transcript_dir: str = "",
    session_ids: list[str] | None = None,
    model: str = "",
) -> None:
    """运行梦境整合：快照消息、提交梦境提示、恢复状态。

    对应 TS 的 autoDream.ts —— 自动梦境（quiet=True）会隔离权限；
    手动 /dream 使用正常权限（与 TS 行为一致）。
    """
    if not quiet:
        console.print("[dim]开始梦境整合…[/dim]")

    # 为梦境创建独立的 Engine 和 PermissionChecker
    from cc_nano.core.engine import Engine
    from cc_nano.core.permissions import PermissionChecker
    from cc_nano.tools import (FileEditTool, FileReadTool, FileWriteTool,
                               GlobTool, GrepTool)

    # 梦境专用工具集（只读 + 受限写入）
    dream_tools = [
        FileReadTool(),
        GlobTool(),
        GrepTool(),
        FileEditTool(),
        FileWriteTool(),
    ]

    # 独立权限检查器，推入 dream 模式，限制写入到 memory_dir 内
    dream_permissions = PermissionChecker(auto_approve=False, interactive=False)
    dream_permissions.push_mode("dream", extra={"memory_dir": str(memory_dir)})

    # 从主引擎获取配置
    dream_engine = Engine(
        tools=dream_tools,
        system_prompt="",  # 临时，稍后设置
        permission_checker=dream_permissions,
        provider=engine._provider,
        api_key=engine._client._api_key,
        base_url=engine._client._base_url,
        model=model or engine._model,
        max_tokens=engine._max_tokens,
        effort=engine._effort,
        session_store=None,  # 梦境不持久化到会话存储
    )

    # 构建梦境提示和系统提示
    dream_prompt = build_dream_prompt(
        memory_dir,
        transcript_dir=transcript_dir,
        session_ids=session_ids,
    )
    dream_engine.system_prompt = build_system_prompt(
        model=model or engine._model, memory_dir=memory_dir
    )

    try:
        # 在独立引擎上运行梦境
        run_query(
            dream_engine,
            dream_prompt,
            print_mode=False,
            permissions=dream_permissions,
            quiet=quiet,
        )
    finally:
        # 确保推出 dream 模式
        dream_permissions.pop_mode()

    # 安全更新主引擎的系统提示（仅赋值，无并发修改消息的风险）
    if engine is not None:
        engine.system_prompt = build_system_prompt(
            model=model or engine._model, memory_dir=memory_dir
        )

    record_consolidation(memory_dir)
    if not quiet:
        console.print("[dim]梦境整合完成。记忆索引已更新。[/dim]")


# 热重载辅助函数（模块级，供 main 和 _reload_environment 使用）
def _load_app_config_with_fallback(
    args: argparse.Namespace, project_root: Path
) -> AppConfig:
    """加载配置，失败时返回占位配置（允许后续修复）"""
    try:
        return load_app_config(args, project_root=project_root)
    except ValueError as e:
        console.print(f"[red]配置错误：{e}[/red]")
        console.print(
            "[dim]将使用最小配置启动（无法调用 API）。请使用项目管理命令修复。[/dim]"
        )
        # 占位配置：API key 为 None，模型使用最小可用
        return AppConfig(
            provider="openai",
            api_key=None,
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            max_tokens=384000,
            effort=None,
            buddy_model=None,
            memory_dir=Path.cwd() / ".config" / "cc-nano" / "memory",
        )


def _build_tools_for_mode(
    coordinator_enabled: bool,
    worker_manager: WorkerManager,
    plan_manager,
    todo_manager,
    sandbox_mgr,
) -> list:
    """根据模式构建工具列表（原函数从 main 中提取，参数化）"""
    from cc_nano.tools.plan_tools import EnterPlanModeTool, ExitPlanModeTool

    tools = [
        FileReadTool(),
        GlobTool(),
        GrepTool(),
        FileEditTool(),
        FileWriteTool(),
        BashTool(sandbox_manager=sandbox_mgr),
        AskUserQuestionTool(),
    ]
    tools.extend(
        [
            EnterPlanModeTool(plan_manager),
            ExitPlanModeTool(plan_manager),
            TodoWriteTool(todo_manager),
            TodoUpdateTool(todo_manager),
        ]
    )
    if coordinator_enabled:
        tools.extend(
            [
                AgentTool(worker_manager),
                SendMessageTool(worker_manager),
                TaskStopTool(worker_manager),
            ]
        )
    return tools


def _build_system_prompt_for_mode(
    coordinator_enabled: bool,
    cwd: str,
    model: str,
    memory_dir: Path,
    skills_section: str,
    project_root: Path,
) -> str:
    """根据模式构建系统提示（原函数从 main 中提取，参数化）"""
    prompt = build_system_prompt(cwd=cwd, model=model, memory_dir=memory_dir)
    if skills_section:
        prompt += "\n\n" + skills_section
    if is_coordinator_mode():
        # 团队模式优先
        from cc_nano.features.coordinator import (get_teamwork_system_prompt,
                                                  is_teamwork_mode)

        if is_teamwork_mode():
            prompt += "\n\n" + get_teamwork_system_prompt(project_root)
        elif coordinator_enabled:
            from cc_nano.features.coordinator import (
                get_coordinator_system_prompt, get_coordinator_user_context)

            worker_tool_names = [
                "FileReadTool",
                "GlobTool",
                "GrepTool",
                "FileEditTool",
                "FileWriteTool",
                "BashTool",
            ]
            extra = get_coordinator_user_context(worker_tool_names)
            worker_context = extra.get("workerToolsContext")
            if worker_context:
                prompt += "\n\n# 协调者上下文\n" + worker_context
            prompt += "\n\n" + get_coordinator_system_prompt()
    return prompt


def _build_worker_engine(
    sandbox_manager,
    cwd,
    model,
    provider,
    memory_dir,
    skills_section,
    allowed_skills,
    api_key=None,
    base_url=None,
    max_tokens=None,
    effort=None,
):
    """构建 Worker 引擎（原函数从 main 中提取，参数化）"""
    from cc_nano.core.engine import Engine
    from cc_nano.core.permissions import PermissionChecker

    worker_permissions = PermissionChecker(
        auto_approve=True, sandbox_manager=sandbox_manager
    )
    base_prompt = build_system_prompt(cwd=cwd, model=model, memory_dir=memory_dir)
    if skills_section:
        base_prompt += "\n\n" + skills_section

    worker_prompt = base_prompt + "\n\n" + get_worker_system_prompt(allowed_skills)
    base_tools = [
        FileReadTool(),
        GlobTool(),
        GrepTool(),
        FileEditTool(),
        FileWriteTool(),
        BashTool(sandbox_manager=sandbox_manager),
    ]
    skill_tool = SkillTool(allowed_skills=allowed_skills)
    tools = base_tools + [skill_tool]
    engine = Engine(
        tools=tools,
        system_prompt=worker_prompt,
        permission_checker=worker_permissions,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        effort=effort,
    )
    skill_tool.set_engine(engine)
    return engine


def _build_explore_engine(
    sandbox_manager,
    cwd,
    model,
    provider,
    memory_dir,
    allowed_skills,
    api_key=None,
    base_url=None,
    max_tokens=None,
    effort=None,
):
    """构建 Explore 引擎（原函数从 main 中提取，参数化）"""
    from cc_nano.core.engine import Engine
    from cc_nano.core.permissions import PermissionChecker

    explore_permissions = PermissionChecker(
        auto_approve=True, sandbox_manager=sandbox_manager
    )
    explore_prompt = EXPLORE_SYSTEM_PROMPT
    if allowed_skills:
        skill_desc = "\n".join(f"- {s}" for s in allowed_skills)
        explore_prompt += (
            f"\n\n## 可用技能\n\n你可以使用 Skill 工具执行以下技能：\n{skill_desc}\n"
        )
    tools = [
        FileReadTool(),
        GlobTool(),
        GrepTool(),
        BashTool(sandbox_manager=sandbox_manager),
    ]
    skill_tool = SkillTool(allowed_skills=allowed_skills)
    tools.append(skill_tool)
    engine = Engine(
        tools=tools,
        system_prompt=explore_prompt,
        permission_checker=explore_permissions,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        effort=effort,
    )
    skill_tool.set_engine(engine)
    return engine


def _build_engine_from_config(
    app_config: AppConfig,
    cwd: str,
    permissions: PermissionChecker,
    session_store: SessionStore | None,
    cost_tracker: object,
    todo_manager: object,
    worker_manager: object,
    plan_manager: object,
    is_coordinator: bool,
    skills_section: str,
    sandbox_mgr: object,
    project_root: Path,
) -> Engine | None:
    """根据 AppConfig 构建 Engine，若 API key 缺失则返回 None"""
    try:
        tools = _build_tools_for_mode(
            is_coordinator, worker_manager, plan_manager, todo_manager, sandbox_mgr
        )
        system_prompt = _build_system_prompt_for_mode(
            is_coordinator,
            cwd,
            app_config.model,
            app_config.memory_dir,
            skills_section,
            project_root,
        )
        engine = Engine(
            tools=tools,
            system_prompt=system_prompt,
            permission_checker=permissions,
            provider=app_config.provider,
            api_key=app_config.api_key,
            base_url=app_config.base_url,
            model=app_config.model,
            max_tokens=app_config.max_tokens,
            effort=app_config.effort,
            session_store=session_store,
            cost_tracker=cost_tracker,
        )
        return engine
    except ValueError as e:
        console.print(f"[red]引擎初始化失败：{e}[/red]")
        return None


def _reload_environment(
    state: dict,
    args: argparse.Namespace,
    project_root: Path,
    cwd: str,
    permissions: PermissionChecker,
    todo_manager: TodoManager,
    plan_manager,
    sandbox_mgr: SandboxManager,
    skills_section: str,
    is_coordinator: bool,
) -> None:
    """热重载配置、引擎、会话存储和工作管理器"""
    # 重新确定项目根（开始）=========================================
    # 优先级：1) 全局活动项目 → 2) 向上查找 .cc-nano.toml → 3) 当前目录
    global_root = get_global_current_project()
    if global_root and Path(global_root).exists():
        new_root = Path(global_root).resolve()
    else:
        new_root = find_project_root(Path.cwd())
        if new_root is None:
            new_root = Path.cwd()
    # 更新运行时项目根
    set_project_root(new_root)

    # 1. 重新加载配置
    new_config = _load_app_config_with_fallback(args, project_root)
    old_config = state.get("app_config")

    # 2. 检查是否需要创建新会话（模型或模式改变）
    need_new_session = old_config is None or new_config.model != old_config.model
    if need_new_session:
        new_session_store = SessionStore(
            cwd=cwd,
            model=new_config.model,
            mode=current_session_mode(),
        )
        if state.get("session_store") and state["session_store"]._message_count > 0:
            console.print(
                "[yellow]注意：由于模型或模式变更，之前的对话已被清空。[/yellow]"
            )
    else:
        new_session_store = state.get("session_store")

    # 3. 重建 worker_manager（工厂函数捕获新配置）
    def _worker_factory():
        return _build_worker_engine(
            sandbox_mgr,
            cwd,
            new_config.model,
            new_config.provider,
            new_config.memory_dir,
            skills_section,
            allowed_skills=["simplify", "test", "commit"],
            api_key=new_config.api_key,
            base_url=new_config.base_url,
            max_tokens=new_config.max_tokens,
            effort=new_config.effort,
        )

    def _explore_factory():
        return _build_explore_engine(
            sandbox_mgr,
            cwd,
            new_config.model,
            new_config.provider,
            new_config.memory_dir,
            allowed_skills=["review"],
            api_key=new_config.api_key,
            base_url=new_config.base_url,
            max_tokens=new_config.max_tokens,
            effort=new_config.effort,
        )

    new_worker_manager = WorkerManager(
        {
            "worker": _worker_factory,
            "Explore": _explore_factory,
        }
    )

    # 4. 重建 compact_service（暂用旧 client，稍后更新）
    old_engine = state.get("engine")
    new_compact_service = CompactService(
        client=old_engine._client if old_engine else None,
        model=new_config.model,
        effort=new_config.effort,
    )

    # 5. 重建 engine
    new_engine = _build_engine_from_config(
        app_config=new_config,
        cwd=cwd,
        permissions=permissions,
        session_store=new_session_store,
        cost_tracker=state["cost_tracker"],
        todo_manager=todo_manager,
        worker_manager=new_worker_manager,
        plan_manager=plan_manager,
        is_coordinator=is_coordinator,
        skills_section=skills_section,
        sandbox_mgr=sandbox_mgr,
        project_root=new_root,
    )
    if new_engine is not None:
        new_compact_service._client = new_engine._client
        # 将 plan_manager 绑定到新 engine
        plan_manager.bind_engine(
            new_engine,
            build_explore_engine=lambda: _build_explore_engine(
                sandbox_mgr,
                cwd,
                new_config.model,
                new_config.provider,
                new_config.memory_dir,
                True,
                ["review"],
            ),
        )
    else:
        console.print("[red]引擎初始化失败，请检查项目配置（API key、模型等）。[/red]")
        sys.exit(1)

    # 6. 更新状态
    state["app_config"] = new_config
    state["engine"] = new_engine
    state["session_store"] = new_session_store
    state["compact_service"] = new_compact_service
    state["worker_manager"] = new_worker_manager


def main() -> None:
    parser = argparse.ArgumentParser(prog="cc-nano", description="极简 AI 编码助手")
    parser.add_argument("prompt", nargs="?", help="要发送的提示（可选）")
    parser.add_argument(
        "-p", "--print", action="store_true", help="非交互模式：打印响应后退出"
    )
    parser.add_argument(
        "--auto-approve", action="store_true", help="自动批准所有工具权限（危险）"
    )
    parser.add_argument("--config", help="TOML 配置文件的路径")
    parser.add_argument(
        "--provider", choices=("anthropic", "openai"), help="API 提供商 / 线缆格式"
    )
    parser.add_argument("--api-key", help="所选提供商的 API 密钥")
    parser.add_argument("--base-url", help="所选提供商的自定义 API 基础 URL")
    parser.add_argument("--model", help="模型名称，例如 deepseek-v4-flash")
    parser.add_argument(
        "--max-tokens", type=int, help="每次模型响应的最大输出 token 数"
    )
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high"),
        help="支持的 OpenAI 模型的可选推理强度",
    )
    parser.add_argument("--buddy-model", help="覆盖 buddy / 陪伴侧功能使用的模型")
    parser.add_argument(
        "--resume", metavar="SESSION", help="恢复之前的会话（id 或索引）"
    )
    parser.add_argument("--memory-dir", help="覆盖记忆目录路径")
    parser.add_argument("--no-auto-dream", action="store_true", help="禁用自动梦境整合")
    parser.add_argument(
        "--dream-interval", type=float, help="自动梦境运行间隔（小时，默认：24）"
    )
    parser.add_argument(
        "--dream-min-sessions",
        type=int,
        help="触发自动梦境所需的最小新会话数（默认：5）",
    )
    parser.add_argument(
        "--coordinator", action="store_true", help="启用协调者模式，支持后台工作器"
    )
    parser.add_argument(
        "--teamwork",
        action="store_true",
        help="启用团队协作模式（Architect/TechLead/Implementer/Reviewer/QA）",
    )
    args = parser.parse_args()

    # === 项目根检测 =========================================================
    global_root = get_global_current_project()
    if global_root and Path(global_root).exists():
        project_root = Path(global_root).resolve()
        console.print(f"[dim]使用全局活动项目：{project_root}[/dim]")
    else:
        console.print("[yellow]未设置活动项目。[/yellow]")
        # 没有设置全局项目，列出所有项目或提示创建
        all_projs = list_all_projects()
        if all_projs:
            console.print("[yellow]找到以下项目：[/yellow]")
            for i, p in enumerate(all_projs, 1):
                console.print(f"  {i}. {p}")
            console.print(
                "\n请使用 [cyan]/change <项目根路径或编号>[/cyan] 选择项目，或使用 [cyan]/new[/cyan] 创建新项目。"
            )
        else:
            console.print(
                "[yellow]未找到任何项目。请使用 [cyan]/new[/cyan] 在当前目录创建项目。[/yellow]"
            )
        console.print("[dim]进入项目管理模式。输入 /help 查看命令，/exit 退出。[/dim]")

        # 引导模式：只处理项目管理命令
        # 临时历史文件（放在用户家目录）
        bootstrap_history_dir = Path.home() / ".config" / "cc-nano"
        bootstrap_history_dir.mkdir(parents=True, exist_ok=True)
        bootstrap_history = FileHistory(
            str(bootstrap_history_dir / "bootstrap_history")
        )

        while True:
            try:
                user_input = bordered_prompt(
                    console, history=bootstrap_history, completer=None
                ).strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]退出。[/dim]")
                sys.exit(0)

            if not user_input:
                continue
            if user_input.lower() in ("exit", "/exit", "quit", "/quit"):
                console.print("[dim]退出。[/dim]")
                sys.exit(0)

            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0][1:].lower()
                arg = parts[1] if len(parts) > 1 else ""

                temp_ctx = CommandContext(
                    engine=None,
                    session_store=None,
                    compact_service=None,
                    console=console,
                    app_config=None,  # 引导模式不需要完整配置
                    memory_dir=None,
                    permissions=None,
                    run_dream=None,
                    cost_tracker=None,
                    new_session_store=None,
                    reconfigure_mode=None,
                    plan_manager=None,
                )

                # 只处理项目管理相关命令（已在模块顶部导入）
                if cmd == "new":
                    handle_new(temp_ctx, arg)
                elif cmd == "list":
                    handle_list(temp_ctx, arg)
                elif cmd == "change":
                    handle_change(temp_ctx, arg)
                elif cmd == "delete":
                    handle_delete(temp_ctx, arg)
                elif cmd == "status":
                    handle_status(temp_ctx, arg)
                elif cmd == "help":
                    console.print(
                        "项目管理命令：/new, /list, /change, /delete, /status, /exit"
                    )
                else:
                    console.print(f"[dim]未知命令：/{cmd}。请使用项目管理命令。[/dim]")

                # 检查是否已设置活动项目
                new_global = get_global_current_project()
                if new_global and Path(new_global).exists():
                    console.print(f"[green]已选择项目：{new_global}[/green]")
                    project_root = Path(new_global).resolve()
                    break  # 退出引导模式，继续正常初始化
            else:
                console.print(
                    "[dim]请输入项目管理命令（以 / 开头）。使用 /help 查看。[/dim]"
                )

    # 设置运行时项目根
    set_project_root(project_root)

    # 历史文件路径（基于项目根）
    _HISTORY_FILE = get_project_root() / ".config" / "cc-nano" / "history"
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 沙箱初始化
    config_path = project_root / ".cc-nano.toml"
    sandbox_config = load_sandbox_config((config_path,))
    sandbox_mgr = SandboxManager(config=sandbox_config)

    # 记忆设置
    app_config = _load_app_config_with_fallback(args, project_root)
    memory_dir = app_config.memory_dir
    ensure_memory_dir(memory_dir)
    session_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    # 技能设置 —— 注册内置技能 + 发现项目/用户技能
    register_bundled_skills()
    cwd = str(Path.cwd())
    discover_skills(str(project_root))
    skills_section = build_skills_prompt_section()

    if args.coordinator:
        set_coordinator_mode(True)

    if args.teamwork:
        set_teamwork_mode(True)
        # 团队模式下默认启用协调者的 Agent 工具（但提示词不同）
        if not args.coordinator:
            set_coordinator_mode(True)  # 确保 Agent 工具可用

    # 权限检查器
    permissions = PermissionChecker(
        auto_approve=args.auto_approve,
        sandbox_manager=sandbox_mgr,
        interactive=not args.print,
    )

    # 计划模式管理器
    from cc_nano.features.plan import PlanModeManager

    plan_manager = PlanModeManager()

    # 待办管理器
    todo_manager = TodoManager()

    # 成本追踪器
    cost_tracker = CostTracker()

    # 初始化状态容器并首次热重载 ==========
    state = {
        "app_config": None,
        "engine": None,
        "session_store": None,
        "cost_tracker": cost_tracker,
        "compact_service": None,
        "worker_manager": None,
        "permissions": permissions,
        "todo_manager": todo_manager,
        "plan_manager": plan_manager,
        "sandbox_mgr": sandbox_mgr,
        "skills_section": skills_section,
    }

    coordinator_enabled = is_coordinator_mode()
    _reload_environment(
        state,
        args,
        project_root,
        cwd,
        permissions,
        todo_manager,
        plan_manager,
        sandbox_mgr,
        skills_section,
        coordinator_enabled,
    )
    engine = state["engine"]
    session_store = state["session_store"]
    app_config = state["app_config"]
    compact_service = state["compact_service"]
    worker_manager = state["worker_manager"]

    # # 将 plan_manager 绑定到 engine 并设置权限
    if engine:
        plan_manager.bind_engine(
            engine,
            build_explore_engine=lambda: _build_explore_engine(
                sandbox_mgr,
                cwd,
                app_config.model,
                app_config.provider,
                app_config.memory_dir,
                True,
                ["review"],
            ),
        )
        plan_manager.set_permissions(permissions)
        permissions.set_plan_manager(plan_manager)

    def _apply_session_mode(session_mode: str | None) -> str | None:
        """根据会话模式重配当前引擎（用于 /resume）"""
        warning = match_session_mode(session_mode)
        enabled = is_coordinator_mode()

        # 从 state 中获取最新对象（支持热重载后更新）
        current_engine = state.get("engine")
        current_session_store = state.get("session_store")
        if current_engine is None:
            return warning  # 无法配置，直接返回

        current_worker_manager = state["worker_manager"]
        current_plan_manager = state["plan_manager"]
        current_todo_manager = state["todo_manager"]
        current_sandbox_mgr = state.get("sandbox_mgr")
        current_app_config = state.get("app_config")
        current_skills_section = state.get("skills_section")

        # 重新构建工具集和系统提示
        if current_sandbox_mgr is not None:
            new_tools = _build_tools_for_mode(
                enabled,
                current_worker_manager,
                current_plan_manager,
                current_todo_manager,
                current_sandbox_mgr,
            )
            current_engine.set_tools(new_tools)

        if current_app_config:
            new_system_prompt = _build_system_prompt_for_mode(
                enabled,
                cwd,
                current_app_config.model,
                current_app_config.memory_dir,
                current_skills_section,
            )
            current_engine.system_prompt = new_system_prompt

        if current_session_store:
            current_session_store.mode = current_session_mode()

        return warning

    # 处理 --resume
    if args.resume and session_store is not None:
        sessions = SessionStore.list_sessions(cwd)
        target = None
        try:
            idx = int(args.resume) - 1
            if 0 <= idx < len(sessions):
                target = sessions[idx]
        except ValueError:
            needle = args.resume.lower()
            for m in sessions:
                if m.session_id.lower().startswith(needle):
                    target = m
                    break
        if target:
            meta, msgs = SessionStore.load_session(target.session_id, cwd)
            if msgs:
                # 恢复会话时匹配模式
                warning = None
                if meta and meta.mode:
                    from cc_nano.features.coordinator import match_session_mode

                    warning = match_session_mode(meta.mode)
                # 设置消息
                engine.set_messages(msgs)
                # 创建新的会话存储对象，指向已恢复的会话 ID
                new_store = SessionStore(
                    cwd=cwd,
                    model=app_config.model,
                    session_id=target.session_id,
                    mode=current_session_mode(),
                )
                engine.set_session_store(new_store)
                session_store = new_store
                state["session_store"] = new_store  # 更新状态容器
                console.print(
                    f"[green]✓[/green] 已恢复：{target.title[:50]}  "
                    f"（{len(msgs)} 条消息）"
                )
                if warning:
                    console.print(f"[yellow]{warning}[/yellow]")
            else:
                console.print(f"[red]会话 {target.session_id} 中没有消息。[/red]")
        else:
            console.print(f"[red]未找到会话：{args.resume}[/red]")

    # 自动执行 /status 命令 ==========
    if not args.print:
        temp_ctx = CommandContext(
            engine=engine,
            session_store=session_store,
            compact_service=compact_service,
            console=console,
            app_config=app_config,
            memory_dir=app_config.memory_dir if app_config else None,
            permissions=permissions,
            run_dream=lambda: (
                _run_dream(
                    engine, app_config.memory_dir, permissions, model=app_config.model
                )
                if engine
                else None
            ),
            cost_tracker=cost_tracker,
            new_session_store=lambda: (
                SessionStore(
                    cwd=cwd, model=app_config.model, mode=current_session_mode()
                )
                if app_config
                else None
            ),
            reconfigure_mode=None,  # 暂不实现动态模式切换
            plan_manager=plan_manager,
        )
        handle_command("status", "", temp_ctx)

    # 非交互模式 / 管道输入
    if args.print or args.prompt:
        prompt_text = args.prompt or sys.stdin.read()
        run_query(
            engine,
            parse_input(prompt_text),
            print_mode=args.print,
            permissions=permissions,
            todo_manager=todo_manager,
        )
        if worker_manager.has_running_tasks():
            console.print(
                "\n[dim]后台工作器仍在运行。请使用交互模式接收协调者任务通知。[/dim]"
            )
        if cost_tracker.total_cost_usd > 0:
            console.print(f"\n[dim]{cost_tracker.format_cost()}[/dim]")
        return

    # 交互式 REPL
    config_note = (
        f"[dim]{app_config.provider}:{app_config.model} · "
        f"max_tokens={app_config.max_tokens}[/dim]"
    )
    # 获取当前会话模式并显示对应的友好名称
    mode = current_session_mode()
    if mode == "coordinator":
        config_note += " [dim yellow]· 协调者模式[/dim yellow]"
    elif mode == "teamwork":
        config_note += " [dim yellow]· 团队协作模式[/dim yellow]"
    else:
        config_note += " [dim yellow]· 普通交互模式[/dim yellow]"

    session_note = (
        f"[dim]会话 {session_store.session_id[:8]}[/dim]" if session_store else ""
    )
    console.print("[bold cyan]cc-nano[/bold cyan]  " f"{config_note}  {session_note}")

    _file_history = FileHistory(str(_HISTORY_FILE))

    # 记录上次 Ctrl+C 的时间，用于双击退出（与 useDoublePress 一致）
    last_ctrlc_time = 0.0

    # 终端模式状态 —— 通过 "!" 键绑定切换的共享可变引用
    _terminal_mode_ref = [False]

    # 陪伴角色动画器 —— 驱动底部工具栏中的实时空闲动画
    # 对应 CompanionSprite.tsx 基于 tick 的动画系统
    animator = None
    try:
        from cc_nano.buddy.prompt import safe_get_animator

        animator = safe_get_animator()
    except Exception:
        pass

    def _set_reaction(text: str, print_to_terminal: bool = False) -> None:
        """观察者回调 —— 将反应传递给动画器的工具栏气泡。

        普通模式（对 CC-NANO 的反应）：仅显示在工具栏气泡中。
        直接对话模式：同时打印到终端滚动历史。
        """
        if animator:
            animator.set_reaction(text)
        if print_to_terminal:
            try:
                from cc_nano.buddy.companion import get_companion
                from cc_nano.buddy.sprites import render_face
                from cc_nano.buddy.types import RARITY_COLORS, CompanionBones

                comp = get_companion()
                if comp:
                    color = RARITY_COLORS.get(comp.rarity, "dim")
                    bones = CompanionBones(
                        rarity=comp.rarity,
                        species=comp.species,
                        eye=comp.eye,
                        hat=comp.hat,
                        shiny=comp.shiny,
                        stats=comp.stats,
                    )
                    face = render_face(bones)
                    console.print(
                        f"\n[{color}]{face} {comp.name}:[/{color}] [{color} italic]{text}[/{color} italic]"
                    )
            except Exception:
                pass

    _exiting = False

    def _drain_worker_notifications() -> None:
        if _exiting:
            return
        # 收集需要排空的管理器：协调者 + 计划模式工作器
        managers_to_drain = []
        if is_coordinator_mode():
            managers_to_drain.append(worker_manager)
        plan_wm = plan_manager.worker_manager
        if plan_wm is not None:
            managers_to_drain.append(plan_wm)
        if not managers_to_drain:
            return
        for mgr in managers_to_drain:
            while True:
                notifications = mgr.drain_notifications()
                if not notifications:
                    break
                for notification in notifications:
                    # 从 XML 通知中提取摘要信息
                    _desc = re.search(r"<summary>(.*?)</summary>", notification)
                    _uses = re.search(r"<tool_uses>(\d+)</tool_uses>", notification)
                    _dur = re.search(r"<duration_ms>(\d+)</duration_ms>", notification)
                    _status = re.search(r"<status>(.*?)</status>", notification)
                    desc = _desc.group(1) if _desc else "工作器更新"
                    uses = _uses.group(1) if _uses else "?"
                    dur_s = f"{int(_dur.group(1)) / 1000:.1f}" if _dur else "?"
                    status = _status.group(1) if _status else "completed"
                    icon = (
                        "[green]●[/green]" if status == "completed" else "[red]●[/red]"
                    )
                    console.print(
                        f"\n{icon} [dim]{desc}（{uses} 次工具调用，{dur_s} 秒）[/dim]"
                    )
                    try:
                        run_query(
                            engine,
                            notification,
                            print_mode=False,
                            permissions=permissions,
                            todo_manager=todo_manager,
                        )
                    except (KeyboardInterrupt, Exception):
                        return

    def _show_worker_status() -> None:
        """在提示符前显示运行中的工作器状态。"""
        # 收集协调者 + 计划模式工作器的状态
        all_statuses = []
        if is_coordinator_mode():
            all_statuses.extend(worker_manager.get_running_status())
        plan_wm = plan_manager.worker_manager
        if plan_wm is not None:
            all_statuses.extend(plan_wm.get_running_status())
        for s in all_statuses:
            uses = s["tool_uses"]
            activity = s["activity"] or "工作中"
            console.print(
                f"[dim]  ● {s['description']} — "
                f"{uses} 次工具调用 · {activity}[/dim]"
            )

    while True:
        _drain_worker_notifications()
        _show_worker_status()

        # 每次提示前启动/重启动画器（会捕获新孵化的陪伴角色）
        if animator is None:
            try:
                from cc_nano.buddy.prompt import safe_get_animator

                animator = safe_get_animator()
            except Exception:
                pass

        try:
            if animator:
                animator.start()
            console.print()
            _terminal_mode_ref[0] = False  # 总是从聊天模式开始
            user_input = bordered_prompt(
                console,
                history=_file_history,
                completer=slash_completer,
                animator_toolbar=animator.toolbar_text if animator else None,
                refresh_interval=0.5 if animator else None,
                terminal_mode_ref=_terminal_mode_ref,
            ).strip()
            temp_ctx = CommandContext(
                engine=None,
                session_store=None,
                compact_service=None,
                console=console,
                app_config=None,
                memory_dir=None,
                permissions=None,
                run_dream=None,
                cost_tracker=None,
                new_session_store=None,
                reconfigure_mode=None,
                plan_manager=None,
            )
            # 项目管理命令处理
            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0][1:].lower()
                arg = parts[1] if len(parts) > 1 else ""
                if cmd in ("new", "list", "change", "delete", "status"):
                    if cmd == "new":
                        handle_new(temp_ctx, arg)
                    elif cmd == "list":
                        handle_list(temp_ctx, arg)
                    elif cmd == "change":
                        handle_change(temp_ctx, arg)
                    elif cmd == "delete":
                        handle_delete(temp_ctx, arg)
                    elif cmd == "status":
                        handle_status(temp_ctx, arg)

                    # 对于会修改配置的命令，执行热重载
                    if cmd in ("new", "change", "delete"):
                        new_root = find_project_root(Path.cwd())
                        if new_root is None:
                            global_root = get_global_current_project()
                            if global_root and Path(global_root).exists():
                                new_root = Path(global_root).resolve()
                            else:
                                new_root = Path.cwd()
                        set_project_root(new_root)
                        _reload_environment(
                            state,
                            args,
                            new_root,
                            cwd,
                            permissions,
                            todo_manager,
                            plan_manager,
                            sandbox_mgr,
                            skills_section,
                            is_coordinator_mode(),
                        )
                        engine = state["engine"]
                        session_store = state["session_store"]
                        app_config = state["app_config"]
                        compact_service = state["compact_service"]
                        worker_manager = state["worker_manager"]
                        if engine:
                            plan_manager.bind_engine(
                                engine,
                                build_explore_engine=lambda: _build_explore_engine(
                                    sandbox_mgr,
                                    cwd,
                                    app_config.model,
                                    app_config.provider,
                                    app_config.memory_dir,
                                    True,
                                    ["review"],
                                ),
                            )
                    continue  # 跳过正常查询
        except KeyboardInterrupt:
            now = time.monotonic()
            if now - last_ctrlc_time <= _DOUBLE_PRESS_TIMEOUT_MS:
                _exiting = True
                if animator:
                    animator.stop()
                console.print("\n[dim]再见。[/dim]")
                break
            last_ctrlc_time = now
            console.print("\n[dim yellow]再次按 Ctrl+C 退出[/dim yellow]")
            continue
        except EOFError:
            if animator:
                animator.stop()
            console.print("\n[dim]再见。[/dim]")
            break
        finally:
            if animator:
                animator.stop()

        # 任何正常输入都重置双击计时器
        last_ctrlc_time = 0.0

        if not user_input:
            continue

        # ---------------------------------------------------------------------------
        # 终端模式 —— 按 "!" 键原地切换模式（无需提交）。
        # 在终端模式下，每次提交的输入都是 shell 命令。
        # 不在终端模式时，"!cmd" 执行单次 shell 命令。
        # ---------------------------------------------------------------------------
        if _terminal_mode_ref[0]:
            run_shell(user_input, console)
            continue

        if user_input.startswith("!") and len(user_input) > 1:
            run_shell(user_input[1:].lstrip(), console)
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            console.print("[dim]再见。[/dim]")
            break
        if user_input.startswith("/sandbox"):
            handle_sandbox_command(user_input, sandbox_mgr, console)
            continue

        # 斜杠命令（会话、压缩、帮助等）
        cmd = parse_command(user_input)
        if cmd is not None:
            cmd_name, cmd_args = cmd
            if cmd_name in ("exit", "quit"):
                console.print("[dim]再见。[/dim]")
                break
            # /buddy 单独处理（陪伴宠物）
            if cmd_name == "buddy":
                from cc_nano.buddy.commands import handle_buddy_command

                handle_buddy_command(
                    cmd_args,
                    engine._client,
                    console,
                    app_config.buddy_model or app_config.model,
                )
                # 刷新动画器，以防刚孵化了陪伴角色
                try:
                    from cc_nano.buddy.prompt import safe_get_animator

                    animator = safe_get_animator()
                except Exception:
                    pass
                continue
            cmd_ctx = CommandContext(
                engine=engine,
                session_store=session_store,
                compact_service=compact_service,
                console=console,
                app_config=app_config,
                memory_dir=memory_dir,
                permissions=permissions,
                run_dream=lambda: _run_dream(
                    engine, memory_dir, permissions, model=app_config.model
                ),
                cost_tracker=cost_tracker,
                new_session_store=lambda: SessionStore(
                    cwd=cwd,
                    model=app_config.model,
                    mode=current_session_mode(),
                ),
                reconfigure_mode=_apply_session_mode,
                plan_manager=plan_manager,
            )
            handle_command(cmd_name, cmd_args, cmd_ctx)
            session_store = cmd_ctx.session_store
            # 如果命令设置了待处理的查询（例如 /plan <描述>），
            # 则将其提交给模型，而不是继续到下一个提示。
            if cmd_ctx.pending_query:
                user_input = cmd_ctx.pending_query
                cmd_ctx.pending_query = None
                # 落到下面的正常查询处理
            else:
                continue

        # 接近 token 限制时自动压缩（仅在 engine 有效时）
        if engine is not None and should_compact(
            engine.get_messages(),
            model=app_config.model,
            last_input_tokens=cost_tracker.last_input_tokens,
        ):
            console.print("[dim]自动压缩对话…[/dim]")
            try:
                new_msgs, _ = compact_service.compact(
                    engine.get_messages(), engine.system_prompt
                )
                engine.set_messages(new_msgs)
                console.print(
                    f"[dim]上下文已压缩至 {estimate_tokens(new_msgs):,} 个 token。[/dim]"
                )
            except Exception as e:
                console.print(f"[dim red]自动压缩失败：{e}[/dim red]")

        # 检查用户是否在直接与陪伴角色对话，
        # 让陪伴角色通过观察者直接回复（无需尴尬的 "." 响应）
        _companion_addressed = False
        try:
            if not load_companion_muted():
                comp = get_companion()
                if comp and _is_addressed(user_input, comp.name):
                    _companion_addressed = True
                    reply_event = threading.Event()

                    def _direct_reply(text: str) -> None:
                        _set_reaction(text, print_to_terminal=True)
                        reply_event.set()

                    fire_companion_observer(
                        "",
                        comp,
                        engine._client,
                        _direct_reply,
                        model=app_config.buddy_model or app_config.model,
                        user_msg=user_input,
                    )
                    reply_event.wait(timeout=10)
        except Exception:
            pass

        if _companion_addressed:
            continue

        # 在 run_query 前检查 engine 有效性 ==========
        if engine is None or engine._client is None:
            console.print("[red]无法执行查询：缺少 API Key 或项目配置无效。[/red]")
            console.print(
                "请使用 [cyan]/status[/cyan] 查看详情，并用 [cyan]/new[/cyan] 或 [cyan]/change[/cyan] 修复。"
            )
            continue

        run_query(
            engine,
            parse_input(user_input),
            print_mode=False,
            permissions=permissions,
            todo_manager=todo_manager,
        )
        _drain_worker_notifications()

        # 每轮对话后，在后台触发陪伴角色观察者
        if engine is not None:
            try:
                if not load_companion_muted():
                    comp = get_companion()
                    if comp and engine._messages:
                        last_msg = engine._messages[-1]
                        if last_msg.get("role") == "assistant":
                            content = last_msg.get("content", "")
                            if isinstance(content, str):
                                assistant_text = content
                            elif isinstance(content, list):
                                parts = []
                                for block in content:
                                    if (
                                        isinstance(block, dict)
                                        and block.get("type") == "text"
                                    ):
                                        parts.append(block.get("text", ""))
                                    elif hasattr(block, "text"):
                                        parts.append(block.text)
                                assistant_text = " ".join(parts)
                            else:
                                assistant_text = str(content)
                            if assistant_text.strip():
                                # 根据本轮对话更新陪伴角色心情
                                try:
                                    import time as _time

                                    from cc_nano.buddy.mood import (
                                        apply_decay, apply_events,
                                        classify_events)
                                    from cc_nano.buddy.storage import (
                                        load_active_mood, save_active_mood)

                                    now_ms = int(_time.time() * 1000)
                                    current_mood = load_active_mood()
                                    current_mood = apply_decay(current_mood, now_ms)
                                    events = classify_events(assistant_text, user_input)
                                    if events:
                                        current_mood = apply_events(
                                            current_mood, events
                                        )
                                    save_active_mood(current_mood)
                                    # 刷新陪伴角色以更新心情
                                    comp = get_companion()
                                    if animator and comp:
                                        animator.update_companion(comp)
                                except Exception:
                                    pass
                                fire_companion_observer(
                                    assistant_text,
                                    comp,
                                    engine._client,
                                    _set_reaction,
                                    model=app_config.buddy_model or app_config.model,
                                    user_msg=user_input,
                                )
            except Exception:
                pass  # 非关键

        # 回合结束后：提取 <memory> 标签
        # 获取消息快照以避免与后台线程（如梦境）的竞态条件
        messages_snapshot = engine.get_messages()  # 返回列表副本
        text = ""
        for msg in reversed(messages_snapshot):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif hasattr(block, "text"):
                            parts.append(block.text)
                    text = " ".join(parts)
                break
        if text:
            for mem in extract_memory_tags(text):
                append_to_daily_log(memory_dir, mem)

        # 自动梦境门控检查 —— 在后台运行，避免阻塞 REPL
        if engine is not None:
            current_sid = session_store.session_id if session_store else session_id
            sessions_path = session_store._dir if session_store else None
            if app_config.auto_dream and should_auto_dream(
                memory_dir,
                min_hours=app_config.dream_interval_hours,
                min_sessions=app_config.dream_min_sessions,
                current_session_id=current_sid,
                sessions_dir=sessions_path,
            ):
                prior_mtime = read_last_consolidated_at(memory_dir)
                if try_acquire_lock(memory_dir):
                    # 收集用于梦境提示的会话 ID
                    from cc_nano.features.memory import list_sessions_since

                    sids = list_sessions_since(
                        prior_mtime,
                        sessions_dir=sessions_path,
                        current_session_id=current_sid,
                    )
                    transcript_dir = str(sessions_path) if sessions_path else ""

                    def _bg_dream(
                        _prior_mtime=prior_mtime,
                        _transcript_dir=transcript_dir,
                        _sids=sids,
                    ):
                        try:
                            _run_dream(
                                engine,  # 主引擎（仅用于读取配置和最后更新 system_prompt）
                                memory_dir,
                                permissions,
                                quiet=True,
                                transcript_dir=_transcript_dir,
                                session_ids=_sids,
                                model=app_config.model,
                            )
                            release_lock(memory_dir)
                        except Exception:
                            # 记录完整堆栈到 stderr 和日志文件
                            error_msg = traceback.format_exc()
                            sys.stderr.write(f"[Dream Error] {error_msg}\n")
                            sys.stderr.flush()

                            # 尝试将错误写入项目日志目录（便于持久化）
                            try:
                                log_dir = memory_dir / "logs"
                                log_dir.mkdir(parents=True, exist_ok=True)
                                log_file = log_dir / "dream_errors.log"
                                from datetime import datetime

                                with open(log_file, "a", encoding="utf-8") as f:
                                    f.write(
                                        f"[{datetime.now().isoformat()}] {error_msg}\n"
                                    )
                            except Exception:
                                pass
                            from cc_nano.features.memory import get_lock_path

                            try:
                                lp = get_lock_path(memory_dir)
                                if lp.exists():
                                    os.utime(lp, (_prior_mtime, _prior_mtime))
                            except OSError:
                                pass

                    threading.Thread(target=_bg_dream, daemon=True).start()

    # 退出时打印费用汇总
    if cost_tracker.total_cost_usd > 0:
        console.print(f"\n[dim]{cost_tracker.format_cost()}[/dim]")


if __name__ == "__main__":
    main()
