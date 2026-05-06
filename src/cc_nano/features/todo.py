"""用于跟踪计划执行进度的待办事项列表。

提供一个简单的任务列表，代理可以在多步工作中创建/更新它。
TUI 会将其渲染为一个带有状态图标的实时清单。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TodoItem:
    """待办事项条目。"""

    id: str
    subject: str
    status: str = (
        "pending"  # "pending"（待处理） | "in_progress"（进行中） | "completed"（已完成）
    )

    _VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})


class TodoManager:
    """持有当前的待办事项列表状态。在工具和 TUI 之间共享。"""

    def __init__(self) -> None:
        self._items: dict[str, TodoItem] = {}
        self._next_id: int = 1

    # -- 修改操作 ------------------------------------------------------------

    def create(self, subject: str, status: str = "pending") -> TodoItem:
        item = TodoItem(id=str(self._next_id), subject=subject, status=status)
        self._items[item.id] = item
        self._next_id += 1
        return item

    def update(
        self,
        item_id: str,
        status: str | None = None,
        subject: str | None = None,
    ) -> TodoItem | None:
        item = self._items.get(item_id)
        if item is None:
            return None
        if status is not None and status in TodoItem._VALID_STATUSES:
            item.status = status
        if subject is not None:
            item.subject = subject
        return item

    def clear(self) -> None:
        self._items.clear()
        self._next_id = 1

    # -- 查询操作 ------------------------------------------------------------

    def get(self, item_id: str) -> TodoItem | None:
        return self._items.get(item_id)

    def get_items(self) -> list[TodoItem]:
        return list(self._items.values())

    def in_progress_item(self) -> TodoItem | None:
        """返回第一个状态为“进行中”的条目，如果没有则返回 None。"""
        for item in self._items.values():
            if item.status == "in_progress":
                return item
        return None
