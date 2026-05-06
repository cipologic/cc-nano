from __future__ import annotations

import glob as glob_module
import re
import subprocess
from pathlib import Path

from cc_nano.core.tool import Tool, ToolResult


class GrepTool(Tool):
    name = "Grep"
    description = (
        "基于 ripgrep 的强大搜索工具\n\n"
        "  使用方法：\n"
        "  - 对于搜索任务，务必使用 Grep 工具。绝对不要通过 Bash 命令调用 `grep` 或 `rg`。"
        "Grep 工具已针对正确的权限和访问进行优化。\n"
        '  - 支持完整的正则表达式语法（例如 "log.*Error"、"function\\s+\\w+"）\n'
        '  - 使用 glob 参数（例如 "*.js"、"**/*.tsx"）或 type 参数（例如 "js"、"py"、"rust"）过滤文件\n'
        '  - 输出模式："content" 显示匹配行，"files_with_matches" 仅显示文件路径（默认），'
        '"count" 显示匹配计数\n'
        "  - 对于需要多轮搜索的开放式搜索，请使用 Agent 工具\n"
        "  - 模式语法：使用 ripgrep（而非 grep）—— 字面大括号需要转义（例如在 Go 代码中搜索 `interface{}` "
        "应使用 `interface\\{\\}`）\n"
        "  - 多行匹配：默认情况下模式仅匹配单行。对于跨行模式，如 `struct \\{[\\s\\S]*?field`，"
        "请使用 `multiline: true`"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式模式"},
            "path": {"type": "string", "description": "要搜索的目录或文件"},
            "glob": {"type": "string", "description": "文件 glob 过滤器，例如 '*.py'"},
            "type": {
                "type": "string",
                "description": "文件类型过滤器（例如 'py', 'js', 'rust')",
            },
            "output_mode": {
                "type": "string",
                "enum": ["files_with_matches", "content", "count"],
                "default": "files_with_matches",
            },
            "-i": {"type": "boolean", "description": "忽略大小写", "default": False},
            "-n": {"type": "boolean", "description": "显示行号", "default": True},
            "-A": {"type": "integer", "description": "每个匹配项后显示的行数"},
            "-B": {"type": "integer", "description": "每个匹配项前显示的行数"},
            "-C": {"type": "integer", "description": "每个匹配项上下文的行数"},
            "multiline": {
                "type": "boolean",
                "description": "启用多行模式",
                "default": False,
            },
            "head_limit": {
                "type": "integer",
                "description": "将输出限制为前 N 行/条目",
                "default": 250,
            },
            "offset": {
                "type": "integer",
                "description": "跳过前 N 行/条目",
                "default": 0,
            },
        },
        "required": ["pattern"],
    }

    def get_activity_description(self, **kwargs) -> str | None:
        pattern = kwargs.get("pattern", "")
        return f"正在搜索 {pattern}" if pattern else None

    def is_read_only(self) -> bool:
        return True

    def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        output_mode: str = "files_with_matches",
        **kwargs,
    ) -> ToolResult:
        cmd = ["rg", "--no-heading"]
        if kwargs.get("-i"):
            cmd.append("-i")
        if kwargs.get("multiline"):
            cmd.extend(["-U", "--multiline-dotall"])
        # 上下文行数
        after = kwargs.get("-A")
        before = kwargs.get("-B")
        context = kwargs.get("-C")
        if after and output_mode == "content":
            cmd.extend(["-A", str(after)])
        if before and output_mode == "content":
            cmd.extend(["-B", str(before)])
        if context and output_mode == "content":
            cmd.extend(["-C", str(context)])
        # 输出模式标志
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        else:
            show_line_numbers = kwargs.get("-n", True)
            if show_line_numbers:
                cmd.append("-n")
        # 过滤器
        if glob:
            cmd.extend(["-g", glob])
        file_type = kwargs.get("type")
        if file_type:
            cmd.extend(["--type", file_type])
        cmd.extend([pattern, path])

        head_limit = kwargs.get("head_limit", 250) or 250
        offset = kwargs.get("offset", 0) or 0

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            output = result.stdout.strip()
            if not output:
                return ToolResult(content="未找到匹配项。")
            # 应用 offset 和 head_limit
            lines = output.split("\n")
            if offset > 0:
                lines = lines[offset:]
            if head_limit > 0:
                truncated = len(lines) > head_limit
                lines = lines[:head_limit]
                result_text = "\n".join(lines)
                if truncated:
                    result_text += f"\n\n...（结果已截断，显示 {head_limit} 条，共 {len(output.split(chr(10)))} 条）"
                return ToolResult(content=result_text)
            return ToolResult(content="\n".join(lines))
        except FileNotFoundError:
            return self._python_grep(
                pattern, path, glob, kwargs.get("-i", False), output_mode
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="错误：搜索超时。", is_error=True)

    def _python_grep(
        self,
        pattern: str,
        path: str,
        glob_filter: str | None,
        case_insensitive: bool,
        output_mode: str = "files_with_matches",
    ) -> ToolResult:
        base = Path(path)
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)

        if base.is_file():
            files = [base]
        else:
            pat = glob_filter or "**/*"
            files = [
                base / p
                for p in glob_module.glob(pat, root_dir=str(base), recursive=True)
            ]

        matched = []
        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                if output_mode == "content":
                    for lineno, line in enumerate(text.splitlines(), 1):
                        if regex.search(line):
                            matched.append(f"{f}:{lineno}:{line}")
                else:
                    if regex.search(text):
                        matched.append(str(f))
            except OSError:
                pass

        return ToolResult(content="\n".join(matched) if matched else "未找到匹配项。")
