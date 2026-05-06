from __future__ import annotations

import glob as glob_module
import subprocess
from pathlib import Path

from cc_nano.core.tool import Tool, ToolResult

_MAX_RESULTS = 100


class GlobTool(Tool):
    name = "Glob"
    description = (
        "- 快速的文件模式匹配工具，适用于任意大小的代码库\n"
        '- 支持 glob 模式，例如 "**/*.js" 或 "src/**/*.ts"\n'
        "- 返回按修改时间排序的匹配文件路径\n"
        "- 当你需要根据文件名模式查找文件时使用此工具\n"
        "- 当你在进行可能需要多轮 glob 和 grep 的开放式搜索时，请改用 Agent 工具"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "用于匹配文件的 glob 模式"},
            "path": {
                "type": "string",
                "description": (
                    "要搜索的目录。如果未指定，将使用当前工作目录。"
                    '重要：省略此字段即可使用默认目录。不要输入 "undefined" 或 "null"，'
                    "只需省略即可获得默认行为。如果提供，必须是有效的目录路径。"
                ),
            },
        },
        "required": ["pattern"],
    }

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs) -> str | None:
        pattern = kwargs.get("pattern", "")
        return f"正在查找 {pattern}" if pattern else None

    def execute(self, pattern: str, path: str = ".") -> ToolResult:
        base = Path(path).resolve()
        if not base.exists():
            return ToolResult(content=f"错误：目录不存在：{path}", is_error=True)
        if not base.is_dir():
            return ToolResult(content=f"错误：路径不是目录：{path}", is_error=True)

        # 优先尝试使用 ripgrep（速度快，适合大型代码库）
        try:
            matches = self._rg_glob(pattern, str(base))
        except FileNotFoundError:
            matches = self._python_glob(pattern, base)

        if not matches:
            return ToolResult(content="未找到匹配模式的文件。")

        truncated = len(matches) > _MAX_RESULTS
        matches = matches[:_MAX_RESULTS]

        # 转换为相对路径以节省 token
        rel_matches = []
        for m in matches:
            try:
                rel_matches.append(str(Path(m).relative_to(base)))
            except ValueError:
                rel_matches.append(m)

        result = "\n".join(rel_matches)
        if truncated:
            result += "\n（结果已截断。考虑使用更具体的路径或模式。）"
        return ToolResult(content=result)

    def _rg_glob(self, pattern: str, search_dir: str) -> list[str]:
        """使用 ripgrep --files --glob 进行快速文件匹配。"""
        cmd = [
            "rg",
            "--files",
            "--glob",
            pattern,
            "--sort=modified",
            "--no-ignore",
            "--hidden",
            search_dir,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if result.returncode not in (0, 1):  # 1 表示没有匹配
            raise FileNotFoundError("rg 执行失败")
        output = result.stdout.strip()
        return output.split("\n") if output else []

    def _python_glob(self, pattern: str, base: Path) -> list[str]:
        """使用 Python 的 glob 模块作为后备方案。"""
        matches = glob_module.glob(pattern, root_dir=str(base), recursive=True)
        # 按修改时间排序（最早的最前，与 rg --sort=modified 行为一致）
        matches = sorted(
            matches,
            key=lambda p: (base / p).stat().st_mtime,
            reverse=True,
        )
        return [str(base / m) for m in matches]
