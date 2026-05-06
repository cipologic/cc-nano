"""沙箱管理器：统一的外部接口。

对应 sandbox-adapter.ts 中的 ISandboxManager 接口（第880-922行）。
协调 config/checker/wrapper/command_matcher 子模块。
"""

from __future__ import annotations

from pathlib import Path

from .checker import DependencyCheck, check_dependencies
from .command_matcher import contains_excluded_command
from .config import SandboxConfig, save_sandbox_config
from .wrapper import build_bwrap_args, wrap_command


class SandboxManager:
    """沙箱管理器。

    生命周期：在 main.py 中创建一次，存在于整个 REPL 会话期间。
    """

    def __init__(self, config: SandboxConfig | None = None):
        self._config = config or SandboxConfig()
        self._dep_check: DependencyCheck | None = None

    @property
    def config(self) -> SandboxConfig:
        return self._config

    # === 状态查询 ===

    def is_enabled(self) -> bool:
        """沙箱是否实际可用。

        对应 isSandboxingEnabled（sandbox-adapter.ts 第532-547行）。
        """
        if not self._config.enabled:
            return False
        return self.check_dependencies().ok

    def is_auto_allow(self) -> bool:
        """是否处于自动允许模式。

        对应 isAutoAllowBashIfSandboxedEnabled()。
        """
        return self._config.enabled and self._config.auto_allow_bash

    def check_dependencies(self) -> DependencyCheck:
        """检查依赖（按会话缓存）。

        对应原版中的记忆化 checkDependencies。
        """
        if self._dep_check is None:
            self._dep_check = check_dependencies()
        return self._dep_check

    # === 命令决策 ===

    def should_sandbox(self, command: str, dangerously_disable: bool = False) -> bool:
        """判断命令是否应在沙箱中运行。

        对应 shouldUseSandbox（shouldUseSandbox.ts 第130-153行）。
        """
        if not self.is_enabled():
            return False
        if dangerously_disable and self._config.allow_unsandboxed:
            return False
        if not command:
            return False
        if contains_excluded_command(command, self._config.excluded_commands):
            return False
        return True

    # === 命令包装 ===

    def wrap(self, command: str, cwd: str | None = None) -> str:
        """为沙箱执行包装命令。

        对应 wrapWithSandbox（sandbox-adapter.ts 第704-725行）。
        """
        return wrap_command(command, self._config, cwd)

    def build_args(self, command: str, cwd: str | None = None) -> list[str]:
        """构建 bwrap 参数列表（用于 shell=False 执行）。"""
        return build_bwrap_args(command, self._config, cwd)

    # === 设置修改 ===

    def set_mode(self, mode: str) -> str:
        """设置沙箱模式。

        对应 SandboxSettings.tsx 中的 handleSelect。
        mode: "auto-allow" | "regular" | "disabled"
        """
        if mode == "auto-allow":
            self._config.enabled = True
            self._config.auto_allow_bash = True
            return "沙箱已启用，并自动允许 bash 命令"
        elif mode == "regular":
            self._config.enabled = True
            self._config.auto_allow_bash = False
            return "沙箱已启用，常规 bash 权限"
        elif mode == "disabled":
            self._config.enabled = False
            self._config.auto_allow_bash = False
            return "沙箱已禁用"
        else:
            return f"未知模式: {mode}"

    def add_excluded_command(self, pattern: str) -> str:
        """添加一个排除的命令模式。

        对应 /sandbox exclude 子命令。
        """
        if pattern not in self._config.excluded_commands:
            self._config.excluded_commands.append(pattern)
        return f"已添加排除模式: {pattern}"

    def save(self, path: Path | None = None) -> None:
        """将当前配置持久化到 TOML 文件。"""
        target = path or (Path.cwd() / ".cc-nano.toml")
        save_sandbox_config(self._config, target)
