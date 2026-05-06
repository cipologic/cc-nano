"""TUI 的流式 Markdown 渲染器和加载动画管理器。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown as RichMarkdown
from rich.spinner import Spinner
from rich.text import Text

if TYPE_CHECKING:
    from cc_nano.features.todo import TodoItem


# 用于识别顶级块边界的正则表达式：空行、标题、代码块、水平线、列表
_BLOCK_BOUNDARY_RE = re.compile(r"\n(?=\n|\#{1,6} |```|---|\* |- |\d+\. )")


class StreamingMarkdown:
    """累积流式文本并增量渲染 Markdown。

    与 TS 版 StreamingMarkdown 的实现方式一致：按块边界拆分，
    将稳定的完整块作为 Rich Markdown 打印输出，不稳定的尾部内容
    放在 Live 组件中实时更新。
    """

    def __init__(self, console: Console):
        self._console = console
        self._buf = ""
        self._stable_len = 0  # 已打印为稳定的内容长度
        self._live: Live | None = None

    def feed(self, chunk: str) -> None:
        """添加一个流式文本块并更新显示。"""
        self._buf += chunk
        self._render()

    def _render(self) -> None:
        # 在整个缓冲区中查找最后一个块边界
        text = self._buf
        boundary = self._stable_len
        for m in _BLOCK_BOUNDARY_RE.finditer(text, self._stable_len):
            boundary = m.start()

        # 打印新稳定的块
        if boundary > self._stable_len:
            # 打印稳定内容前先停止 Live 组件
            if self._live is not None:
                self._live.stop()
                self._live = None
            stable_text = text[self._stable_len : boundary]
            self._console.print(RichMarkdown(stable_text), end="")
            self._stable_len = boundary

        # 用 Live 组件显示不稳定的尾部内容
        unstable = text[self._stable_len :]
        if unstable:
            if self._live is None:
                self._live = Live(
                    RichMarkdown(unstable),
                    console=self._console,
                    refresh_per_second=8,
                    transient=True,
                )
                self._live.start()
            else:
                self._live.update(RichMarkdown(unstable))

    def flush(self) -> None:
        """结束渲染：将剩余文本作为稳定 Markdown 输出。"""
        if self._live is not None:
            self._live.stop()
            self._live = None
        remaining = self._buf[self._stable_len :]
        if remaining:
            self._console.print(RichMarkdown(remaining), end="")
        self._buf = ""
        self._stable_len = 0


class SpinnerManager:
    """管理一个 Rich Live 加载动画，在等待 API / 工具响应时显示。

    加载动画：在模型思考或工具执行时，显示一个带有上下文文本的旋转指示器。
    """

    def __init__(self, console: Console):
        self._console = console
        self._live: Live | None = None
        self._spinner_text = "思考中…"

    def start(self, text: str = "思考中…"):
        self._spinner_text = text
        # 如果已有 Live 实例在运行，先停止
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._live = Live(
            Spinner("dots", text=Text(self._spinner_text, style="dim")),
            console=self._console,
            refresh_per_second=12,
            transient=True,
        )
        self._live.start()

    def update(self, text: str):
        self._spinner_text = text
        if self._live is not None:
            self._live.update(
                Spinner("dots", text=Text(self._spinner_text, style="dim"))
            )

    def stop(self):
        if self._live is not None:
            self._live.stop()
            self._live = None


def tool_preview(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:80] + ("…" if len(cmd) > 80 else "")
    if tool_name in ("Read", "Edit", "Write"):
        fp = tool_input.get("file_path", "")
        return fp[-60:] if len(fp) > 60 else fp
    if tool_name == "Glob":
        pat = tool_input.get("pattern", "")
        p = tool_input.get("path", "")
        return f"{pat} in {p}" if p else pat
    if tool_name == "Grep":
        pat = tool_input.get("pattern", "")
        p = tool_input.get("path", "")
        return f"{pat} in {p}" if p else pat
    if tool_name == "Agent":
        return tool_input.get("description", "")[:60]
    if tool_name == "SendMessage":
        return tool_input.get("to", "")
    return ""


def collapsed_tool_summary(tool_names: list[str], done: bool = False) -> str:
    """按类型汇总工具调用，与 TS 版的 CollapsedReadSearchContent 保持一致。

    例如 active（未完成）状态下： "正在读取 5 个文件…"
    完成状态下： "已读取 5 个文件"
    """
    from collections import Counter

    counts = Counter(tool_names)
    parts = []
    _ACTIVE = {
        "Read": ("正在读取 {n} 个文件", "正在读取文件"),
        "Glob": ("正在搜索 {n} 个模式", "正在搜索"),
        "Grep": ("正在搜索 {n} 个模式", "正在搜索"),
        "Bash": ("正在运行 {n} 条命令", "正在运行命令"),
        "Edit": ("正在编辑 {n} 个文件", "正在编辑文件"),
        "Write": ("正在写入 {n} 个文件", "正在写入文件"),
    }
    _DONE = {
        "Read": ("已读取 {n} 个文件", "已读取文件"),
        "Glob": ("已搜索 {n} 个模式", "已搜索"),
        "Grep": ("已搜索 {n} 个模式", "已搜索"),
        "Bash": ("已运行 {n} 条命令", "已运行命令"),
        "Edit": ("已编辑 {n} 个文件", "已编辑文件"),
        "Write": ("已写入 {n} 个文件", "已写入文件"),
    }
    labels = _DONE if done else _ACTIVE
    for name, n in counts.items():
        plural, singular = labels.get(name, (f"{name} ×{{n}}", name))
        parts.append(plural.format(n=n) if n > 1 else singular)
    suffix = "" if done else "…"
    return " · ".join(parts) + suffix


# ---------------------------------------------------------------------------
# 待办事项列表渲染
# ---------------------------------------------------------------------------

_TODO_ICONS = {
    "pending": "[dim]◻[/dim]",
    "in_progress": "[yellow]◼[/yellow]",
    "completed": "[green]✓[/green]",
}


def render_todo_list(items: list[TodoItem], console: Console) -> None:
    """打印带状态图标的清单样式待办列表。"""
    for item in items:
        icon = _TODO_ICONS.get(item.status, "[dim]◻[/dim]")
        subject = item.subject
        if len(subject) > 72:
            subject = subject[:69] + "…"
        if item.status == "completed":
            console.print(f"  {icon} [dim]{subject}[/dim]", highlight=False)
        elif item.status == "in_progress":
            console.print(f"  {icon} [bold]{subject}[/bold]", highlight=False)
        else:
            console.print(f"  {icon} {subject}", highlight=False)
