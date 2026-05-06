from __future__ import annotations

import os
import select
import sys
import time
from typing import TYPE_CHECKING, Literal, Optional

from .tool import Tool

if TYPE_CHECKING:
    from cc_nano.features.plan import PlanModeManager
    from cc_nano.features.sandbox.manager import SandboxManager
    from cc_nano.tui.keylistener import EscListener

PermissionBehavior = Literal["allow", "deny"]


class PermissionChecker:
    """只读工具自动允许。Bash/写入操作会询问用户（y/n/always）。"""

    def __init__(
        self,
        auto_approve: bool = False,
        sandbox_manager: Optional[SandboxManager] = None,
        interactive: bool = True,
    ):
        self._interactive = interactive
        self._auto_approve = auto_approve
        self._always_allow: set[str] = set()
        self._esc_listener: EscListener | None = None
        self._sandbox = sandbox_manager
        self._plan_manager: PlanModeManager | None = None
        # 模式栈：每个元素为 (mode, always_allow_snapshot, extra_data)
        self._mode_stack: list[tuple[str, set[str], dict]] = []  # 栈中存储模式字符串
        self._mode: str = "default"  # 当前模式（兼容旧代码）
        self._extra: dict = {}
        # self._pre_plan_mode: str | None = None
        # self._pre_plan_always_allow: set[str] | None = None
        # 梦境模式：仅允许写入 memory 目录
        self._dream_mode: bool = False
        self._dream_memory_dir: str | None = None
        # 记录最后一次拒绝的原因
        self._last_deny_reason: str | None = None

    # ===== 模式栈 API =====
    def push_mode(self, mode: str, extra: dict | None = None) -> None:
        """推入新模式，保存当前状态到栈。extra 用于存储模式特有数据（如 dream 的 memory_dir）。"""
        self._mode_stack.append(
            (self._mode, set(self._always_allow), getattr(self, "_extra", {}))
        )
        self._mode = mode
        if mode == "plan":
            # 计划模式下移除危险的 always-allow 规则
            self._always_allow -= {"Bash", "Edit", "Write", "Agent"}
        elif mode == "dream":
            # 梦境模式：清空 always_allow，只保留只读工具自动允许
            self._always_allow.clear()
        self._extra = extra or {}

    def pop_mode(self) -> None:
        """恢复上一个模式和 always_allow 快照。"""
        if self._mode_stack:
            self._mode, self._always_allow, self._extra = self._mode_stack.pop()
        else:
            # 栈为空时回退到 default
            self._mode = "default"
            self._extra = {}

    def set_plan_manager(self, plan_manager: PlanModeManager) -> None:
        self._plan_manager = plan_manager

    def enter_dream_mode(self, memory_dir: str) -> None:
        """启用梦境权限隔离 —— 推入 dream 模式。"""
        self.push_mode("dream", extra={"memory_dir": memory_dir})

    def exit_dream_mode(self) -> None:
        """退出梦境模式。"""
        self.pop_mode()

    def set_esc_listener(self, listener: EscListener | None):
        self._esc_listener = listener

    def get_last_deny_reason(self) -> str | None:
        """返回最近一次权限拒绝的详细原因（若有）。"""
        return self._last_deny_reason

    @property
    def mode(self) -> str:
        return self._mode

    def check(self, tool: Tool, inputs: dict) -> PermissionBehavior:
        # 梦境模式：严格隔离 —— 只读 + 仅允许写入 memory 目录
        if self._mode == "dream":
            return self._check_dream(tool, inputs)

        # 计划模式限制：仅允许只读工具 + 计划文件写入
        if self._mode == "plan":
            return self._check_plan(tool, inputs)

        if tool.is_read_only():
            return "allow"
        if self._auto_approve:
            return "allow"
        if tool.name in self._always_allow:
            return "allow"

        # 沙箱自动允许：沙箱化后的 Bash 命令无需确认
        if tool.name == "Bash" and self._sandbox is not None:
            try:
                if self._sandbox.is_auto_allow() and self._sandbox.should_sandbox(
                    inputs.get("command", "")
                ):
                    return "allow"
            except Exception:
                # 沙箱检查出现异常（如配置错误、bwrap 不可用等），视为不能自动允许，
                # 继续后续普通权限流程。
                pass
        # 非交互模式：无用户可交互，直接拒绝
        if not self._interactive:
            return "deny"

        return self._prompt_user(tool, inputs)

    def _check_plan(self, tool: Tool, inputs: dict) -> PermissionBehavior:
        """计划模式：只读工具 + 计划文件写入 + Agent 工具。"""
        from cc_nano.features.plan import (PLAN_MODE_ALLOWED_TOOLS,
                                           PLAN_MODE_WRITE_TOOLS)

        if tool.name in PLAN_MODE_ALLOWED_TOOLS:
            return "allow"
        if tool.name in PLAN_MODE_WRITE_TOOLS:
            file_path = inputs.get("file_path", "")
            plan_path = (
                self._plan_manager.plan_file_path if self._plan_manager else None
            )
            if plan_path and file_path == plan_path:
                return "allow"
            self._last_deny_reason = (
                f"计划模式下只能编辑计划文件 ({plan_path})。"
                f"请使用 Write 或 Edit 工具写入该文件。"
            )
            return "deny"
        self._last_deny_reason = (
            f"计划模式下不允许使用 {tool.name}。"
            f"仅允许以下工具：{', '.join(PLAN_MODE_ALLOWED_TOOLS)} "
            f"以及对计划文件的写入操作。"
        )
        return "deny"

    def _check_dream(self, tool: Tool, inputs: dict) -> PermissionBehavior:
        """梦境模式：只读工具 + Edit/Write 仅限 memory 目录内。"""
        if tool.is_read_only():
            return "allow"
        if tool.name in ("Edit", "Write"):
            file_path = inputs.get("file_path", "")
            memory_dir = self._extra.get("memory_dir")
            if memory_dir and isinstance(file_path, str):
                base = os.path.realpath(memory_dir)
                target = os.path.realpath(file_path)
                # 检查 target 是否严格位于 base 目录下（包括相等）
                if target == base or target.startswith(base + os.sep):
                    return "allow"
            return "deny"
        # Bash 及其他工具：梦境模式下均拒绝
        return "deny"

    def _prompt_user(self, tool: Tool, inputs: dict) -> PermissionBehavior:
        from rich.console import Console

        console = Console()
        console.print(
            f"\n[bold yellow]需要权限：[/bold yellow][bold]{tool.name}[/bold]"
        )
        for k, v in inputs.items():
            val = str(v)[:200] + ("..." if len(str(v)) > 200 else "")
            console.print(f"  [dim]{k}:[/dim] {val}")

        console.print("\n  允许？\\[y]是 / \\[n]否 / \\[a]总是允许：", end="")

        # 暂停 ESC 监听器，避免干扰按键捕获
        if self._esc_listener:
            self._esc_listener.pause()

        fd = sys.stdin.fileno()
        start_time = time.monotonic()
        TIMEOUT_SECONDS = 60  # 总超时时间（秒），可根据需要调整

        try:
            while True:
                # 检查总超时
                if time.monotonic() - start_time > TIMEOUT_SECONDS:
                    console.print("\n[dim]权限请求超时，自动拒绝。[/dim]")
                    return "deny"

                # 非阻塞检查：等待最多 0.1 秒，避免永久阻塞
                rlist, _, _ = select.select([fd], [], [], 0.1)
                if not rlist:
                    # 无输入，继续循环（保持响应，但不会卡死）
                    continue

                # cbreak 模式下：单字节无缓冲读取，无需按回车
                b = os.read(fd, 1)

                # 检测 ESC 键 —— 区分单独的 ESC 和以 \x1b 开头的转义序列（如方向键）
                if b == b"\x1b":
                    # 检查是否为转义序列的开始
                    if select.select([fd], [], [], 0.05)[0]:
                        # 转义序列 —— 清空并忽略
                        while select.select([fd], [], [], 0.01)[0]:
                            os.read(fd, 64)
                        continue
                    # 真正的 ESC 按下
                    console.print()
                    # 外部 EscListener 已暂停，无需设置 pressed 标志
                    # 直接返回拒绝，由上层 engine 处理中止（SIGINT 会由外部监听器在恢复后发送）
                    return "deny"

                choice = b.decode("utf-8", errors="ignore").lower()
                console.print(choice)  # 回显按下的字符

                if choice == "y":
                    return "allow"
                if choice == "n":
                    return "deny"
                if choice == "a":
                    self._always_allow.add(tool.name)
                    return "allow"
                console.print("  请输入 y、n 或 a：", end="")
        finally:
            # 恢复 ESC 监听器
            if self._esc_listener:
                self._esc_listener.resume()
