from __future__ import annotations

import base64
from pathlib import Path

from cc_nano.core.tool import Tool, ToolResult

from .file_edit import FileEditTool

# 支持的图片扩展名
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}
_MAX_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1 GiB


def _is_binary(path: Path) -> bool:
    """通过查找空字节判断文件是否为二进制文件。"""
    try:
        with open(path, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except OSError:
        return False


class FileReadTool(Tool):
    name = "Read"
    description = (
        "从本地文件系统读取文件。你可以直接使用此工具访问任何文件。\n"
        "假设此工具能够读取机器上的所有文件。如果用户提供了文件路径，则认为该路径有效。"
        "尝试读取不存在的文件也没关系，会返回错误信息。\n\n"
        "用法：\n"
        "- file_path 参数必须是绝对路径，不能是相对路径\n"
        "- 默认从文件开头读取最多 2000 行\n"
        "- 当你已经知道需要文件的哪一部分时，只读取那一部分。这对较大的文件很重要。\n"
        "- 结果采用 cat -n 格式返回，行号从 1 开始\n"
        "- 此工具允许读取图像（如 PNG、JPG 等）。读取图像文件时，内容会以多模态输入的形式直观呈现。\n"
        "- 此工具只能读取文件，不能读取目录。要读取目录，请通过 Bash 工具使用 ls 命令。\n"
        "- 如果读取的文件存在但内容为空，你将收到一条系统提醒警告，而不是文件内容。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件的绝对路径"},
            "offset": {
                "type": "integer",
                "description": "起始行（从 0 开始计数）",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "最多返回的行数",
                "default": 2000,
            },
        },
        "required": ["file_path"],
    }

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs) -> str | None:
        file_path = kwargs.get("file_path", "")
        return f"正在读取 {file_path}" if file_path else None

    def execute(self, file_path: str, offset: int = 0, limit: int = 2000) -> ToolResult:
        path = Path(file_path)
        if not path.exists():
            return ToolResult(content=f"错误：文件不存在：{file_path}", is_error=True)
        if not path.is_file():
            return ToolResult(content=f"错误：不是一个文件：{file_path}", is_error=True)

        # 标记文件已被读取，用于编辑/写入的强制检查
        FileEditTool.mark_file_read(file_path)
        FileEditTool.mark_file_read(str(path.resolve()))

        # 图像文件 —— 返回 base64 编码的内容
        if path.suffix.lower() in _IMAGE_EXTENSIONS:
            try:
                data = path.read_bytes()
                b64 = base64.b64encode(data).decode("ascii")
                ext = path.suffix.lower().lstrip(".")
                media_type = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                return ToolResult(
                    content=f"[图像：{file_path} ({len(data)} 字节, {media_type})]\nbase64:{b64}"
                )
            except OSError as e:
                return ToolResult(content=f"读取图像时出错：{e}", is_error=True)

        # 二进制文件检测
        if _is_binary(path):
            return ToolResult(
                content=f"错误：{file_path} 似乎是二进制文件", is_error=True
            )

        # 文件大小检查
        try:
            size = path.stat().st_size
            if size > _MAX_FILE_SIZE:
                return ToolResult(
                    content=f"错误：文件过大 ({size} 字节)", is_error=True
                )
        except OSError:
            pass

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult(content=f"读取文件时出错：{e}", is_error=True)

        lines = content.splitlines(keepends=True)
        sliced = lines[offset : offset + limit]
        numbered = "".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(sliced))

        if len(lines) > offset + limit:
            numbered += f"\n... （还有 {len(lines) - offset - limit} 行未显示）"

        return ToolResult(content=numbered)
