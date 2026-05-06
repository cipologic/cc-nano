from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from cc_nano.core.tool import Tool, ToolResult

if TYPE_CHECKING:
    from cc_nano.features.sandbox.manager import SandboxManager

_DEFAULT_TIMEOUT = 120


class BashTool(Tool):
    """Bash 工具 —— 在沙箱或本地执行 bash 命令。"""

    name = "Bash"
    description = (
        "执行给定的 bash 命令并返回其输出。\n\n"
        "工作目录在命令之间保持不变，但 shell 状态不会保留。"
        "Shell 环境会从用户的配置文件（bash 或 zsh）中初始化。\n\n"
        "重要：除非明确指示或已确认专用工具无法完成任务，否则请避免使用此工具运行 "
        "`find`、`grep`、`cat`、`head`、`tail`、`sed`、`awk` 或 `echo` 命令。"
        "相反，应使用合适的专用工具，这将为用户带来更好的体验：\n\n"
        " - 文件搜索：使用 Glob（不要用 find 或 ls）\n"
        " - 内容搜索：使用 Grep（不要用 grep 或 rg）\n"
        " - 读取文件：使用 Read（不要用 cat/head/tail）\n"
        " - 编辑文件：使用 Edit（不要用 sed/awk）\n"
        " - 写入文件：使用 Write（不要用 echo >/cat <<EOF）\n"
        " - 输出信息：直接输出文本（不要用 echo/printf）\n"
        "虽然 Bash 工具也能做类似的事情，但使用内置工具能提供更好的用户体验，"
        "并且更容易审查工具调用和授予权限。\n\n"
        "# 使用说明\n"
        " - 如果命令将创建新目录或文件，请先使用本工具运行 `ls` 来确认父目录存在且位置正确。\n"
        " - 命令中若包含带空格的路径，务必用双引号括起来。\n"
        " - 尽量在整个会话中保持当前工作目录，使用绝对路径并避免使用 `cd`。"
        "仅在用户明确要求时才可使用 `cd`。\n"
        " - 可以指定可选的超时时间（秒），默认 120 秒。\n"
        " - 当执行多个命令时：\n"
        "   - 如果命令相互独立且可并行运行，在单条消息中发起多次 Bash 工具调用。\n"
        "   - 如果命令相互依赖且必须顺序执行，使用单个 Bash 调用并用 '&&' 串联。\n"
        "   - 仅当你需要顺序执行命令但不关心前面命令是否失败时，才使用 ';'。\n"
        "   - 不要使用换行符分隔命令（换行符在引号字符串内是可以的）。\n"
        " - 对于 git 命令：\n"
        "   - 优先创建新提交，而不是修改现有提交。\n"
        "   - 在执行破坏性操作（如 git reset --hard、git push --force、git checkout --）之前，"
        "请考虑是否有更安全的替代方案能达成相同目标。\n"
        "   - 除非用户明确要求，否则永远不要跳过钩子（--no-verify）或绕过签名。"
        "如果钩子失败，请调查并修复根本问题。\n"
        " - 避免不必要的 `sleep` 命令：\n"
        "   - 不要在可以立即执行的命令之间插入 sleep —— 直接运行它们。\n"
        "   - 不要用 sleep 循环重试失败的命令 —— 诊断根本原因。\n"
        "   - 如果必须 sleep，请保持较短时长（1-5 秒），以免阻塞用户。"
    )

    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 bash 命令"},
            "description": {
                "type": "string",
                "description": "用主动语态清晰简洁地描述该命令的作用",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒）",
                "default": 120,
            },
            "dangerously_disable_sandbox": {
                "type": "boolean",
                "description": "如果为 true 且配置允许，则在沙箱外运行",
            },
        },
        "required": ["command"],
    }

    def get_activity_description(self, **kwargs) -> str | None:
        command = kwargs.get("command", "")
        # 显示命令的截断版本
        preview = command[:60] + "…" if len(command) > 60 else command
        return f"运行 {preview}" if command else None

    def __init__(self, sandbox_manager: SandboxManager | None = None):
        self._sandbox = sandbox_manager

    def execute(
        self,
        command: str,
        description: str = "",
        timeout: int = _DEFAULT_TIMEOUT,
        dangerously_disable_sandbox: bool = False,
    ) -> ToolResult:
        # 沙箱决策
        use_sandbox = self._sandbox is not None and self._sandbox.should_sandbox(
            command, dangerously_disable_sandbox
        )

        actual_command = self._sandbox.wrap(command) if use_sandbox else command

        try:
            result = subprocess.run(
                actual_command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            parts = []
            if result.stdout:
                stdout = result.stdout.rstrip()
                # 截断过长的输出
                if len(stdout) > 10_000:
                    stdout = stdout[
                        :10_000
                    ] + "\n\n... (输出已截断，完整输出为 {0} 字符)".format(
                        len(result.stdout.rstrip())
                    )
                parts.append(stdout)
            if result.stderr:
                parts.append(f"[stderr]\n{result.stderr.rstrip()}")
            if result.returncode != 0:
                parts.append(f"[退出码: {result.returncode}]")
            return ToolResult(content="\n".join(parts) if parts else "(无输出)")
        except subprocess.TimeoutExpired:
            return ToolResult(content=f"错误：命令在 {timeout} 秒后超时", is_error=True)
        except Exception as e:
            return ToolResult(content=f"错误：{e}", is_error=True)
