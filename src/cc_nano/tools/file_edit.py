from __future__ import annotations

from pathlib import Path

from cc_nano.core.tool import Tool, ToolResult


class FileEditTool(Tool):
    name = "Edit"
    description = (
        "在文件中执行精确的字符串替换。\n\n"
        "用法：\n"
        "- 在编辑之前，你必须在本次对话中至少使用一次 `Read` 工具。"
        "如果你未读取文件就尝试编辑，此工具将报错。\n"
        "- 从 Read 工具的输出中编辑文本时，请确保保留行号前缀之后的原始缩进（制表符/空格）。"
        "行号前缀的格式为：行号 + 制表符。之后的所有内容才是要匹配的实际文件内容。"
        "切勿在 old_string 或 new_string 中包含行号前缀的任何部分。\n"
        "- 务必优先编辑代码库中已有的文件。除非明确要求，否则绝对不要新建文件。\n"
        "- 仅当用户明确要求时才使用表情符号。除非用户要求，否则避免向文件中添加表情符号。\n"
        "- 如果 `old_string` 在文件中不唯一，编辑将失败。"
        "你可以提供带有更多上下文的长字符串使其唯一，或者使用 `replace_all` 替换 `old_string` 的每个实例。\n"
        "- 使用 `replace_all` 可以在整个文件中替换或重命名字符串。"
        "例如，当你想要重命名某个变量时，此参数非常有用。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件的绝对路径"},
            "old_string": {"type": "string", "description": "要替换的精确字符串"},
            "new_string": {"type": "string", "description": "替换后的字符串"},
            "replace_all": {
                "type": "boolean",
                "description": "是否替换所有出现的位置",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    # 共享的已读文件集合 — 由 FileReadTool 填充
    _read_files: set[str] = set()

    _MAX_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1 GiB

    @classmethod
    def mark_file_read(cls, file_path: str) -> None:
        """将文件标记为已读"""
        cls._read_files.add(file_path)

    def get_activity_description(self, **kwargs) -> str | None:
        file_path = kwargs.get("file_path", "")
        return f"正在编辑 {file_path}" if file_path else None

    def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        """执行文件编辑操作"""
        path = Path(file_path)
        if not path.exists():
            return ToolResult(content=f"错误：文件不存在：{file_path}", is_error=True)

        # 强制先读后写
        if (
            file_path not in self._read_files
            and str(path.resolve()) not in self._read_files
        ):
            return ToolResult(
                content=f"错误：编辑 {file_path} 之前必须先读取它。请先使用 Read 工具。",
                is_error=True,
            )

        # 文件大小检查
        try:
            if path.stat().st_size > self._MAX_FILE_SIZE:
                return ToolResult(content="错误：文件过大，无法编辑", is_error=True)
        except OSError:
            pass
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult(content=f"读取文件时出错：{e}", is_error=True)

        count = content.count(old_string)
        if count == 0:
            return ToolResult(
                content=f"错误：在 {file_path} 中未找到 old_string", is_error=True
            )
        if count > 1 and not replace_all:
            return ToolResult(
                content=f"错误：old_string 出现了 {count} 次。请使用 replace_all=true 或增加更多上下文。",
                is_error=True,
            )

        new_content = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )
        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return ToolResult(content=f"写入文件时出错：{e}", is_error=True)

        replaced = count if replace_all else 1
        return ToolResult(content=f"成功替换了 {replaced} 处出现（位于 {file_path}）")
