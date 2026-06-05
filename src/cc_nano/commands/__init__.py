"""斜杠命令系统 —— 解析与分发。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from cc_nano.features.coordinator import (current_session_mode,
                                          match_session_mode)

if TYPE_CHECKING:
    from cc_nano.features.compact import CompactService
    from cc_nano.core.config import AppConfig
    from cc_nano.features.cost_tracker import CostTracker
    from cc_nano.core.engine import Engine
    from cc_nano.core.permissions import PermissionChecker
    from cc_nano.core.session import SessionStore

from .project_commands import (handle_change, handle_delete, handle_list,
                               handle_new, handle_status)

# ---------------------------------------------------------------------------
# 传递给每个命令处理器的上下文包
# ---------------------------------------------------------------------------


@dataclass
class CommandContext:
    engine: Engine
    session_store: SessionStore | None
    compact_service: CompactService
    console: Console
    app_config: AppConfig
    memory_dir: Path | None = None
    permissions: PermissionChecker | None = None
    run_dream: object = None
    cost_tracker: CostTracker | None = None
    new_session_store: object = None
    reconfigure_mode: object = None
    plan_manager: object = None
    pending_query: str | None = None  # 由需要后续模型查询的命令设置


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_command(text: str) -> tuple[str, str] | None:
    """如果 *text* 以 ``/`` 开头，返回 ``(命令名, 参数)``。"""
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(None, 1)
    name = parts[0][1:].lower()  # 去掉开头的 /
    args = parts[1] if len(parts) > 1 else ""
    return name, args


# ---------------------------------------------------------------------------
# 处理器
# ---------------------------------------------------------------------------


def _cmd_help(ctx: CommandContext, args: str) -> None:
    table = Table(title="可用命令", show_header=True, header_style="bold cyan")
    table.add_column("命令", style="green")
    table.add_column("描述")
    for name, desc, _ in _COMMAND_TABLE:
        table.add_row(f"/{name}", desc)
    ctx.console.print(table)


def _cmd_compact(ctx: CommandContext, args: str) -> None:
    from cc_nano.features.compact import estimate_tokens

    # 计划模式下跳过压缩
    if ctx.plan_manager and getattr(ctx.plan_manager, "is_active", False):
        ctx.console.print(
            "[yellow]计划模式下无法压缩对话。请退出计划模式后再试。[/yellow]"
        )
        return

    messages = ctx.engine.get_messages()
    if len(messages) < 4:
        ctx.console.print("[dim]消息太少，无法压缩。[/dim]")
        return

    pre_tokens = estimate_tokens(messages)
    ctx.console.print(
        f"[dim]正在压缩 {len(messages)} 条消息（约 {pre_tokens:,} token）……[/dim]"
    )

    new_msgs, summary = ctx.compact_service.compact(
        messages,
        ctx.engine.system_prompt,
        custom_instructions=args,
    )
    ctx.engine.set_messages(new_msgs)

    # 如果可用，将压缩后的状态持久化到新的会话存储中
    if ctx.session_store is not None:
        _persist_compacted(ctx, new_msgs)

    post_tokens = estimate_tokens(new_msgs)
    ctx.console.print(
        f"[green]✓[/green] 压缩完成：{pre_tokens:,} → {post_tokens:,} token "
        f"（{len(messages)} → {len(new_msgs)} 条消息）"
    )


def _persist_compacted(ctx: CommandContext, new_msgs: list[dict]) -> None:
    """用压缩后的消息覆盖当前会话。"""
    if ctx.session_store is None:
        return
    # 创建一个指向同一会话 ID 的新会话存储，
    # 用压缩后的消息覆盖 JSONL 文件。
    import json

    from cc_nano.core.session import _now_iso, _serialize_message

    path = ctx.session_store._jsonl_path
    with open(path, "w", encoding="utf-8") as fh:
        for msg in new_msgs:
            safe = _serialize_message(msg)
            safe["_ts"] = _now_iso()
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")
    ctx.session_store._message_count = len(new_msgs)
    ctx.session_store._save_meta()


def _cmd_history(ctx: CommandContext, args: str) -> None:
    from cc_nano.core.session import SessionStore

    cwd = str(os.getcwd())
    sessions = SessionStore.list_sessions(cwd)
    if not sessions:
        ctx.console.print("[dim]当前目录下没有已保存的会话。[/dim]")
        return

    table = Table(title="会话历史", show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="dim", width=10)
    table.add_column("标题")
    table.add_column("消息数", justify="right", width=8)
    table.add_column("更新时间", width=20)

    for i, meta in enumerate(sessions, 1):
        table.add_row(
            str(i),
            meta.session_id[:8],
            meta.title[:50],
            str(meta.message_count),
            meta.updated_at[:19].replace("T", " "),
        )
    ctx.console.print(table)


def _cmd_resume(ctx: CommandContext, args: str) -> None:
    from cc_nano.core.session import SessionStore

    cwd = str(os.getcwd())
    sessions = SessionStore.list_sessions(cwd)

    if not sessions:
        ctx.console.print("[dim]没有可恢复的已保存会话。[/dim]")
        return

    if not args:
        # 显示列表并要求用户选择
        _cmd_history(ctx, "")
        ctx.console.print("\n[dim]用法：/resume <编号> 或 /resume <会话ID>[/dim]")
        return

    # 尝试作为数字索引
    target_meta = None
    try:
        idx = int(args.strip()) - 1
        if 0 <= idx < len(sessions):
            target_meta = sessions[idx]
    except ValueError:
        pass

    # 尝试作为会话 ID 前缀
    if target_meta is None:
        needle = args.strip().lower()
        for meta in sessions:
            if meta.session_id.lower().startswith(needle):
                target_meta = meta
                break

    if target_meta is None:
        ctx.console.print(f"[red]未找到会话：{args}[/red]")
        return

    # 如果要恢复的就是当前会话，则跳过
    if ctx.session_store and target_meta.session_id == ctx.session_store.session_id:
        ctx.console.print("[dim]已经在此会话中。[/dim]")
        return

    # 加载消息
    meta, messages = SessionStore.load_session(target_meta.session_id, cwd)
    if not messages:
        ctx.console.print("[red]会话中没有消息。[/red]")
        return

    warning = None
    session_mode = meta.mode if meta is not None else None
    if callable(ctx.reconfigure_mode):
        warning = ctx.reconfigure_mode(session_mode)
    else:
        warning = match_session_mode(session_mode)

    # 创建指向已恢复会话的新会话存储
    resumed_store = SessionStore(
        cwd=cwd,
        model=ctx.app_config.model,
        session_id=target_meta.session_id,
        mode=current_session_mode(),
    )

    ctx.engine.set_messages(messages)
    if resumed_store is not None:
        ctx.engine.set_session_store(resumed_store)
        ctx.session_store = resumed_store  # type: ignore[assignment]

    ctx.console.print(
        f"[green]✓[/green] 已恢复会话 [bold]{target_meta.session_id[:8]}[/bold]："
        f"{target_meta.title[:50]}  （{len(messages)} 条消息）"
    )
    if warning:
        ctx.console.print(f"[yellow]{warning}[/yellow]")


def _cmd_clear(ctx: CommandContext, args: str) -> None:
    ctx.engine.set_messages([])
    if callable(ctx.new_session_store):
        new_store = ctx.new_session_store()
        if new_store is not None:
            ctx.engine.set_session_store(new_store)
            ctx.session_store = new_store  # type: ignore[assignment]
        else:
            ctx.console.print(
                "[dim yellow]警告：无法创建新会话存储，将继续使用当前会话。[/dim yellow]"
            )
    ctx.console.print("[green]✓[/green] 对话已清空。已开始新会话。")


def _cmd_memory(ctx: CommandContext, args: str) -> None:
    from cc_nano.features.memory import load_memory_index

    if ctx.memory_dir is None:
        ctx.console.print("[dim]记忆系统未配置。[/dim]")
        return
    index = load_memory_index(ctx.memory_dir)
    if index:
        ctx.console.print(index)
    else:
        ctx.console.print("[dim]尚无记忆。使用 /dream 整理每日日志。[/dim]")


def _cmd_remember(ctx: CommandContext, args: str) -> None:
    from cc_nano.features.memory import append_to_daily_log

    if ctx.memory_dir is None:
        ctx.console.print("[dim]记忆系统未配置。[/dim]")
        return
    if not args.strip():
        ctx.console.print("[dim]用法：/remember <文本>[/dim]")
        return
    append_to_daily_log(ctx.memory_dir, args.strip())
    ctx.console.print("[dim]已保存到每日日志。[/dim]")


def _cmd_dream(ctx: CommandContext, args: str) -> None:
    if ctx.run_dream is None or not callable(ctx.run_dream):
        ctx.console.print("[dim]Dream 不可用。[/dim]")
        return
    ctx.run_dream()


def _cmd_skills(ctx: CommandContext, args: str) -> None:
    """列出所有可用的技能。"""
    from cc_nano.features.skills import list_skills

    skills = list_skills(user_invocable_only=True)
    if not skills:
        ctx.console.print("[dim]没有可用的技能。[/dim]")
        return

    table = Table(title="可用技能", show_header=True, header_style="bold cyan")
    table.add_column("命令", style="green")
    table.add_column("来源", style="dim", width=8)
    table.add_column("描述")
    for s in skills:
        hint = f" [{s.argument_hint}]" if s.argument_hint else ""
        table.add_row(f"/{s.name}{hint}", s.source, s.description)
    ctx.console.print(table)


def _cmd_projects(ctx: CommandContext, args: str) -> None:
    """显示项目管理相关命令"""
    table = Table(title="项目管理命令", show_header=True, header_style="bold cyan")
    table.add_column("命令", style="green")
    table.add_column("描述")

    # 硬编码项目管理命令及其描述（与 app.py 中实际行为保持一致）
    project_commands = [
        ("/new", "在当前目录创建新项目配置（.cc-nano.toml）"),
        ("/list", "列出所有已找到的 cc-nano 项目"),
        ("/change", "切换全局活动项目（参数：项目根路径或目录名）"),
        ("/delete", "删除指定项目的配置文件（需二次确认）"),
        ("/status", "显示当前所在项目、全局活动项目及关键配置（隐藏 api_key）"),
    ]
    for cmd, desc in project_commands:
        table.add_row(cmd, desc)

    ctx.console.print(table)


def _cmd_cost(ctx: CommandContext, args: str) -> None:
    if ctx.cost_tracker is None:
        ctx.console.print("[dim]成本跟踪不可用。[/dim]")
        return
    ctx.console.print(ctx.cost_tracker.format_cost())


def _cmd_model(ctx: CommandContext, args: str) -> None:
    from cc_nano.core.config import (DEFAULT_MODEL,
                                     default_max_tokens_for_model,
                                     resolve_model)

    provider = ctx.app_config.provider

    if args:
        ctx.engine.set_model(args.strip())
        actual = ctx.engine.get_model()
        ctx.console.print(
            f"[green]✓[/green] 已将模型设置为 [bold]{actual}[/bold]  "
            f"（max_tokens={default_max_tokens_for_model(actual, provider=provider)}）"
        )
        return

    if provider != "anthropic":
        current = ctx.engine.get_model()
        ctx.console.print(
            f"[dim]当前模型：{current}[/dim]\n"
            f"[dim]使用 /model <名称> 切换 {provider} 提供商的模型。[/dim]"
        )
        return

    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    current = ctx.engine.get_model()

    # ---------- 修改点：移除 Claude 模型，添加 DeepSeek V4 模型 ----------
    options = [
        (
            "deepseek-v4-pro",
            "Pro",
            "DeepSeek V4 Pro · 旗舰级性能，复杂推理与长文本（1M上下文）",
        ),
        (
            "deepseek-v4-flash",
            "Flash",
            "DeepSeek V4 Flash · 轻量化快速响应，成本优化（1M上下文）",
        ),
    ]
    # -----------------------------------------------------------------

    effort_levels = ["low", "medium", "high"]
    effort_sym = {"low": "◑", "medium": "◕", "high": "●"}

    cursor = [0]
    for i, (alias, _, _) in enumerate(options):
        if resolve_model(alias) == current:
            cursor[0] = i
            break

    effort_idx = [2]
    result: list[str | None] = [None]
    max_label = max(len(l) for _, l, _ in options)

    kb = KeyBindings()

    @kb.add("up")
    def _up(e):
        cursor.__setitem__(0, (cursor[0] - 1) % len(options))

    @kb.add("down")
    def _down(e):
        cursor.__setitem__(0, (cursor[0] + 1) % len(options))

    @kb.add("left")
    def _left(e):
        effort_idx.__setitem__(0, (effort_idx[0] - 1) % len(effort_levels))

    @kb.add("right")
    def _right(e):
        effort_idx.__setitem__(0, (effort_idx[0] + 1) % len(effort_levels))

    @kb.add("enter")
    def _confirm(e):
        result[0] = options[cursor[0]][0]
        e.app.exit()

    for i in range(min(len(options), 9)):

        @kb.add(str(i + 1))
        def _select(e, idx=i):
            cursor[0] = idx
            result[0] = options[idx][0]
            e.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(e):
        e.app.exit()

    def _tokens():
        t = [
            ("bold ansibrightcyan", "  选择模型\n"),
            (
                "ansigray",
                "  在模型之间切换。适用于当前会话及后续会话。\n"
                "  其他/之前的模型名称请使用 --model 指定。\n\n",
            ),
        ]
        for i, (alias, label, desc) in enumerate(options):
            is_cur = i == cursor[0]
            is_active = resolve_model(alias) == current
            ptr = "❯" if is_cur else " "
            sty = "ansibrightcyan" if is_cur else ""
            chk = " ✔" if is_active else ""
            t.append((sty, f"  {ptr} {i+1}. {(label + chk).ljust(max_label + 3)}"))
            t.append(("ansigray", desc))
            t.append(("", "\n"))

        eff = effort_levels[effort_idx[0]]
        t.append(("", "\n"))
        t.append(("ansigray", "  努力程度："))
        for lvl in effort_levels:
            s = "bold ansibrightcyan" if lvl == eff else "ansigray"
            t.append((s, f" {effort_sym[lvl]} {lvl} "))
        t.append(("", "\n"))
        t.append(("ansigray", "  ↑↓ 选择 · ←→ 调整努力程度 · ↵ 确认 · esc 取消"))
        return t

    app: Application = Application(
        layout=Layout(Window(FormattedTextControl(_tokens))),
        key_bindings=kb,
        full_screen=False,
    )

    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        pass

    if result[0] is None:
        ctx.console.print(f"[dim]模型保持为 {current}[/dim]")
        return

    ctx.engine.set_model(result[0])
    actual = ctx.engine.get_model()
    eff = effort_levels[effort_idx[0]]
    ctx.console.print(
        f"[green]✓[/green] 已将模型设置为 [bold]{actual}[/bold]  "
        f"（max_tokens={default_max_tokens_for_model(actual, provider=provider)}，努力程度={eff}）"
    )


# ---------------------------------------------------------------------------
# 命令注册表
# ---------------------------------------------------------------------------


def _cmd_plan(ctx: CommandContext, args: str) -> None:
    """进入计划模式或显示当前计划。"""
    from cc_nano.features.plan import PlanModeManager

    pm: PlanModeManager | None = ctx.plan_manager  # type: ignore[assignment]
    if pm is None:
        ctx.console.print("[red]计划模式不可用。[/red]")
        return

    # 添加 cancel 子命令
    if args.strip() == "cancel":
        if pm.is_active:
            # 退出计划模式并删除计划文件
            pm.exit()
            if pm.delete_plan_file():
                ctx.console.print("[green]已退出计划模式并删除计划文件。[/green]")
            else:
                ctx.console.print(
                    "[green]已退出计划模式，但计划文件可能无法删除。[/green]"
                )
        else:
            ctx.console.print("[dim]当前不在计划模式中。[/dim]")
        return

    if pm.is_active:
        content = pm.get_plan_content()
        if content:
            ctx.console.print(f"[bold]当前计划[/bold]（{pm.plan_file_path}）：\n")
            ctx.console.print(content)
        else:
            ctx.console.print(
                f"[dim]计划模式已激活，但尚未写入计划。文件：{pm.plan_file_path}[/dim]"
            )
    else:
        pm.enter()
        ctx.console.print("[green]已启用计划模式[/green]")
        # 如果用户提供了描述，将其作为后续查询排队
        # 匹配 TS：onDone('Enabled plan mode', { shouldQuery: true })
        description = args.strip()
        if description:
            ctx.pending_query = description


# （命令名，描述，处理器）
_COMMAND_TABLE: list[tuple[str, str, object]] = [
    ("help", "显示可用命令", _cmd_help),
    ("projects", "显示所有项目管理命令（/new, /list, ...）", _cmd_projects),
    ("status", "显示当前项目配置状态（不显示密钥）", handle_status),
    ("plan", "进入计划模式或显示当前计划或取消计划", _cmd_plan),
    ("skills", "列出所有可用技能", _cmd_skills),
    ("compact", "压缩对话上下文 [指令]", _cmd_compact),
    ("resume", "恢复之前的会话 [编号|会话ID]", _cmd_resume),
    ("history", "列出当前目录下已保存的会话", _cmd_history),
    ("clear", "清空对话，开始新会话", _cmd_clear),
    ("memory", "显示当前记忆索引", _cmd_memory),
    ("remember", "保存一条笔记到每日日志 [文本]", _cmd_remember),
    ("dream", "将每日日志整理为主题文件", _cmd_dream),
    ("cost", "显示 token 用量和费用汇总", _cmd_cost),
    ("model", "显示或切换模型 [模型名]", _cmd_model),
    ("new", "在当前目录创建新项目配置", handle_new),
    ("list", "列出所有已找到的项目", handle_list),
    ("change", "切换当前活动项目", handle_change),
    ("delete", "删除指定项目的配置文件", handle_delete),
]

_HANDLERS: dict[str, object] = {name: handler for name, _, handler in _COMMAND_TABLE}


def handle_command(name: str, args: str, ctx: CommandContext) -> bool:
    """分发斜杠命令。返回 True 表示已处理，False 表示未处理。

    如果 *name* 不是内置命令，则检查技能注册表，并以内联方式（提示注入）或分叉方式（独立轮次）执行技能。
    """
    handler = _HANDLERS.get(name)
    if handler is not None:
        handler(ctx, args)  # type: ignore[operator]
        return True

    # 尝试作为技能调用
    from cc_nano.features.skills import get_skill

    skill = get_skill(name)
    if skill is not None:
        return _execute_skill(skill, args, ctx)

    ctx.console.print(f"[red]未知命令：/{name}[/red]  （尝试 /help 或 /skills）")
    return False


def _execute_skill(skill, args: str, ctx: CommandContext) -> bool:
    """执行技能 —— 内联或分叉。

    内联（默认）：将技能提示作为用户消息注入当前对话，让引擎处理。

    分叉：在独立轮次中运行技能（保存消息，清空，运行，恢复原始消息）。
    """
    from cc_nano.tui.query import run_query

    prompt = skill.get_prompt(args)
    if not prompt:
        ctx.console.print(f"[dim]技能 /{skill.name} 未产生提示。[/dim]")
        return True

    ctx.console.print(f"[dim]正在运行技能：/{skill.name}……[/dim]")

    if skill.context == "fork":
        # 分叉执行：独立轮次
        saved = list(ctx.engine.get_messages())
        ctx.engine.set_messages([])
        try:
            permissions = ctx.permissions
            run_query(ctx.engine, prompt, print_mode=False, permissions=permissions)
        finally:
            # 恢复原始消息（分叉结果不持久化）
            ctx.engine.set_messages(saved)
    else:
        # 内联执行：将提示注入当前对话
        permissions = ctx.permissions
        run_query(ctx.engine, prompt, print_mode=False, permissions=permissions)

    return True
