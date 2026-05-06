"""TodoWrite / TodoUpdate 工具 —— 代理驱动的任务清单管理。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cc_nano.core.tool import Tool, ToolResult

if TYPE_CHECKING:
    from cc_nano.features.todo import TodoManager


class TodoWriteTool(Tool):
    """创建或替换用于跟踪多步工作的待办清单。"""

    name = "TodoWrite"
    description = (
        "创建或替换向用户显示的任务清单。"
        "开始一个多步骤任务时使用此工具来跟踪进度。"
        "每个任务项包含一个标题（简短的祈使句标题）和一个可选的状态（默认为 pending）。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "要创建的待办项列表。",
                "items": {
                    "type": "object",
                    "properties": {
                        "subject": {
                            "type": "string",
                            "description": "简短的祈使句标题，例如 '为认证模块添加单元测试'。",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "初始状态（默认：pending）。",
                        },
                    },
                    "required": ["subject"],
                },
            },
        },
        "required": ["todos"],
    }

    def __init__(self, manager: TodoManager) -> None:
        self._manager = manager

    def execute(self, todos: list) -> ToolResult:
        self._manager.clear()
        for entry in todos:
            self._manager.create(
                subject=entry["subject"],
                status=entry.get("status", "pending"),
            )
        items = self._manager.get_items()
        lines = [f"  #{it.id} [{it.status}] {it.subject}" for it in items]
        return ToolResult(
            content=f"已创建 {len(items)} 个待办项。\n" + "\n".join(lines)
        )

    def get_activity_description(self, **kwargs) -> str | None:
        return "正在创建待办清单…"


class TodoUpdateTool(Tool):
    """更新待办项的状态或标题。"""

    name = "TodoUpdate"
    description = (
        "更新待办项的状态或标题。"
        "开始处理某项时将其状态设为 in_progress，完成后设为 completed。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "待办项的 ID（例如 '1'）。",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "该项的新状态。",
            },
            "subject": {
                "type": "string",
                "description": "新的标题文本（可选）。",
            },
        },
        "required": ["id"],
    }

    def __init__(self, manager: TodoManager) -> None:
        self._manager = manager

    def execute(
        self, id: str, status: str | None = None, subject: str | None = None
    ) -> ToolResult:
        item = self._manager.update(id, status=status, subject=subject)
        if item is None:
            return ToolResult(content=f"未找到待办项 #{id}。", is_error=True)
        return ToolResult(content=f"已更新 #{item.id}：[{item.status}] {item.subject}")

    def get_activity_description(self, **kwargs) -> str | None:
        status = kwargs.get("status", "")
        item_id = kwargs.get("id", "")
        item = self._manager.get(item_id)
        if item and status == "in_progress":
            return item.subject
        return f"正在更新待办 #{item_id}…"
