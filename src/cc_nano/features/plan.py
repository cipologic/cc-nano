"""计划模式 —— 先探索再实现的工作流程。

对应 TS 中的：
  TS: utils/plans.ts          （计划文件 I/O、slug 生成）
  TS: bootstrap/state.ts      （计划模式状态）
  TS: utils/permissions/permissionSetup.ts  （权限剥离）
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Callable

from cc_nano.core.engine import Engine
from cc_nano.core.permissions import PermissionChecker
from cc_nano.core.project import get_project_root
from cc_nano.core.tool import Tool
from cc_nano.tools import (AskUserQuestionTool, EnterPlanModeTool,
                           ExitPlanModeTool, FileEditTool, FileReadTool,
                           FileWriteTool, GlobTool, GrepTool)

# 计划模式下允许使用的工具（只读 + 计划文件写入 + Agent 工具）
PLAN_MODE_ALLOWED_TOOLS = {
    "Read",
    "Glob",
    "Grep",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "Agent",
    "SendMessage",
    "TaskStop",
}
PLAN_MODE_WRITE_TOOLS = {"Edit", "Write"}

# ---------------------------------------------------------------------------
# 单词 slug 生成（简化自 utils/words.ts）
# ---------------------------------------------------------------------------

_ADJECTIVES = [
    "amber",
    "azure",
    "bold",
    "bright",
    "calm",
    "clear",
    "cool",
    "crisp",
    "dark",
    "deep",
    "eager",
    "fair",
    "fast",
    "fierce",
    "gentle",
    "golden",
    "green",
    "happy",
    "keen",
    "kind",
    "light",
    "lucky",
    "merry",
    "noble",
    "pale",
    "proud",
    "quick",
    "quiet",
    "rapid",
    "sharp",
    "silent",
    "sleek",
    "snoopy",
    "soft",
    "steady",
    "still",
    "swift",
    "tall",
    "tidy",
    "vivid",
    "warm",
    "wild",
    "wise",
    "young",
    "brave",
    "clever",
    "daring",
    "fresh",
]

_NOUNS = [
    "arrow",
    "badge",
    "blade",
    "brook",
    "castle",
    "cloud",
    "comet",
    "coral",
    "crane",
    "creek",
    "crown",
    "dawn",
    "delta",
    "dove",
    "dream",
    "eagle",
    "ember",
    "falcon",
    "fern",
    "flame",
    "forge",
    "frost",
    "garden",
    "grove",
    "harbor",
    "hawk",
    "heron",
    "hill",
    "island",
    "jewel",
    "lake",
    "leaf",
    "lotus",
    "maple",
    "marsh",
    "meadow",
    "moon",
    "ocean",
    "orchid",
    "peak",
    "pine",
    "planet",
    "pond",
    "rain",
    "river",
    "sage",
    "shore",
    "spark",
    "stone",
    "storm",
    "summit",
    "tiger",
    "trail",
    "valley",
    "wave",
    "willow",
]


def _generate_slug() -> str:
    """生成一个由形容词-名词-名词组成的 slug。"""
    return (
        f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{random.choice(_NOUNS)}"
    )


def _get_plans_dir() -> Path:
    """获取计划文件存放目录，若不存在则创建。"""
    plans_dir = get_project_root() / ".config" / "cc-nano" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    return plans_dir


# ---------------------------------------------------------------------------
# PlanModeManager 计划模式管理器
# ---------------------------------------------------------------------------


class PlanModeManager:
    """管理计划模式的完整生命周期：进入、退出、文件管理、提示词注入。

    在启动时构造一次，在 engine 创建后绑定到 engine。
    通过构造函数注入的方式传递给 EnterPlanModeTool / ExitPlanModeTool
    （与 AgentTool 持有 WorkerManager 的模式相同）。
    """

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._permissions: PermissionChecker | None = None
        self._build_explore_engine: Callable[[], object] | None = None
        self._plan_worker_manager: object | None = None
        self._active: bool = False
        self._plan_file: Path | None = None
        self._saved_tools: list[Tool] | None = None
        self._saved_prompt: str | None = None

    def bind_engine(
        self,
        engine: Engine,
        build_explore_engine: Callable[[], object] | None = None,
    ) -> None:
        """绑定引擎及可选的探索引擎构建器。"""
        self._engine = engine
        self._build_explore_engine = build_explore_engine

    def set_permissions(self, permissions: PermissionChecker) -> None:
        """设置权限检查器。"""
        self._permissions = permissions

    @property
    def is_active(self) -> bool:
        """是否处于计划模式。"""
        return self._active

    @property
    def plan_file_path(self) -> str | None:
        """当前计划文件的路径字符串，若无则为 None。"""
        return str(self._plan_file) if self._plan_file else None

    def get_plan_file(self) -> Path | None:
        """返回计划文件的 Path 对象。"""
        return self._plan_file

    @property
    def worker_manager(self) -> object | None:
        """计划模式下的 WorkerManager（若已激活且子代理已启用）。"""
        return self._plan_worker_manager if self._active else None

    def get_plan_content(self) -> str | None:
        """读取当前计划文件的内容，若文件不存在或读取失败则返回 None。"""
        if self._plan_file and self._plan_file.exists():
            try:
                return self._plan_file.read_text(encoding="utf-8")
            except OSError:
                return None
        return None

    def delete_plan_file(self) -> bool:
        """删除当前计划文件（如果存在）。返回是否成功删除。"""
        if self._plan_file and self._plan_file.exists():
            try:
                self._plan_file.unlink()
                return True
            except OSError:
                return False
        return False

    # -- 进入 / 退出 -------------------------------------------------------

    def enter(self) -> str:
        """进入计划模式：创建计划文件，切换为只读工具，注入提示词。"""
        assert self._engine is not None, "PlanModeManager 尚未绑定到 engine"

        if self._active:
            return f"已处于计划模式。计划文件：{self._plan_file}"

        # 生成计划文件
        plans_dir = _get_plans_dir()
        path = None
        for _ in range(10):
            slug = _generate_slug()
            candidate = plans_dir / f"{slug}.md"
            if not candidate.exists():
                path = candidate
                break
        if path is None:
            # 10 次都冲突，使用带时间戳或随机后缀的 fallback
            import uuid

            fallback_slug = f"{_generate_slug()}-{uuid.uuid4().hex[:4]}"
            path = plans_dir / f"{fallback_slug}.md"
        self._plan_file = path

        # 保存当前状态
        self._saved_tools = list(self._engine._tools.values())
        self._saved_prompt = self._engine.system_prompt

        # 切换为只读工具 + 计划工具 + AskUserQuestion
        plan_tools: list[Tool] = [
            FileReadTool(),
            GlobTool(),
            GrepTool(),
            FileEditTool(),  # 仅允许操作计划文件（由权限系统检查）
            FileWriteTool(),  # 仅允许操作计划文件（由权限系统检查）
            AskUserQuestionTool(),
            EnterPlanModeTool(self),
            ExitPlanModeTool(self),
        ]

        # 如果探索引擎构建器可用，则添加并行子代理工具
        self._plan_worker_manager = None
        if self._build_explore_engine is not None:
            from cc_nano.features.agents.worker_manager import WorkerManager
            from cc_nano.tools.agent import (AgentTool, SendMessageTool,
                                             TaskStopTool)

            self._plan_worker_manager = WorkerManager(
                {"Explore": self._build_explore_engine}
            )
            plan_tools.extend(
                [
                    AgentTool(
                        self._plan_worker_manager
                    ),  # 注意：AgentTool 需要传入 plan_manager
                    SendMessageTool(self._plan_worker_manager),
                    TaskStopTool(self._plan_worker_manager),
                ]
            )

        self._engine.set_tools(plan_tools)

        # 将计划模式指令注入系统提示词
        from cc_nano.core.context import get_plan_mode_section

        plan_section = get_plan_mode_section(str(self._plan_file))
        self._engine.system_prompt = self._saved_prompt + "\n\n" + plan_section

        self._active = True

        # 将权限上下文切换到计划模式
        if self._permissions is not None:
            # self._permissions.enter_plan_mode()
            self._permissions.push_mode("plan")

        # 简短确认消息 —— 详细指令已在系统提示词中
        # 与 TS 中的 EnterPlanModeTool 行为一致，仅返回简要确认
        return f"已进入计划模式。计划文件：{self._plan_file}"

    def exit(self) -> tuple[str, str | None]:
        """退出计划模式：恢复原有工具和提示词，返回（消息，计划内容）。"""
        assert self._engine is not None, "PlanModeManager 尚未绑定到 engine"

        if not self._active:
            return ("未处于计划模式。", None)

        plan_content = self.get_plan_content()

        # 先恢复权限上下文，再恢复工具
        if self._permissions is not None:
            # self._permissions.exit_plan_mode()
            self._permissions.pop_mode()

        # 恢复原始状态
        if self._saved_tools is not None:
            self._engine.set_tools(self._saved_tools)
        if self._saved_prompt is not None:
            self._engine.system_prompt = self._saved_prompt

        self._active = False
        self._saved_tools = None
        self._saved_prompt = None
        self._plan_worker_manager = None

        plan_path = str(self._plan_file) if self._plan_file else "unknown"

        if plan_content:
            msg = (
                f"用户已批准你的计划。现在可以开始编写代码了。\n\n"
                f"你的计划已保存至：{plan_path}\n"
                f"实现过程中如果需要，可以随时参考该计划。\n\n"
                f"## 已批准的计划：\n{plan_content}"
            )
        else:
            msg = (
                "已退出计划模式。未写入任何计划文件。\n"
                "现在可以进行编辑、运行工具和执行操作。"
            )

        return (msg, plan_content)
