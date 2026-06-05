from __future__ import annotations

import os
from pathlib import Path

from cc_nano.core.tool import Tool, ToolResult

from .file_edit import FileEditTool


class FileWriteTool(Tool):
    name = "Write"
    description = (
        "将文件写入本地文件系统。\n\n"
        "使用方法：\n"
        "- 如果指定路径已存在文件，此工具将覆盖该文件。\n"
        "- 如果是已存在的文件，你必须先使用读取工具读取文件内容。"
        "如果你没有先读取文件，此工具将执行失败。\n"
        "- 修改现有文件时，优先使用编辑工具 —— 它只发送差异部分。"
        "仅在创建新文件或完全重写时使用此工具。\n"
        "- 除非用户明确要求，否则绝不创建文档文件（*.md）或README文件。\n"
        "- 仅当用户明确要求时才使用表情符号。除非被要求，否则避免向文件写入表情符号。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "要写入的文件的绝对路径"},
            "content": {"type": "string", "description": "要写入文件的完整内容"},
        },
        "required": ["file_path", "content"],
    }

    def get_activity_description(self, **kwargs) -> str | None:
        file_path = kwargs.get("file_path", "")
        return f"正在写入 {file_path}" if file_path else None

    def execute(self, file_path: str, content: str) -> ToolResult:
        path = Path(file_path)

        # 强制要求：对已存在的文件必须先读取后写入
        if path.exists():
            if (
                file_path not in FileEditTool._read_files
                and str(path.resolve()) not in FileEditTool._read_files
            ):
                return ToolResult(
                    content=f"错误：在覆盖 {file_path} 之前必须先读取它。请先使用读取工具。",
                    is_error=True,
                )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 以二进制写入模式打开，确保可以调用 fileno()
            with open(path, 'wb') as f:
                f.write(content.encode('utf-8'))
                f.flush()
                os.fsync(f.fileno())          # 强制同步到磁盘
        except OSError as e:
            return ToolResult(content=f"写入文件时出错：{e}", is_error=True)

        lines = content.count("\n") + (
            1 if content and not content.endswith("\n") else 0
        )
        return ToolResult(content=f"成功将 {lines} 行写入 {file_path} 并已同步到磁盘。")
