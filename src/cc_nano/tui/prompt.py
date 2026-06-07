"""带边框的输入提示。

使用自定义的 prompt_toolkit Application，使得底部边框紧贴输入内容下方（而非屏幕底部）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from prompt_toolkit.application import Application as PTApp
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (Float, FloatContainer, HSplit,
                                              Window)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from rich.console import Console

from cc_nano.commands import _COMMAND_TABLE

class SlashCommandCompleter(Completer):
    """斜杠命令的自动补全。当输入以 "/" 开头时触发。"""

    # 不在 _COMMAND_TABLE 中的额外命令（在 REPL 中单独处理）
    _EXTRA_COMMANDS: list[tuple[str, str]] = [
        ("buddy", "陪伴宠物 — 孵化、抚摸、状态、静音/取消静音、ia"),
        ("buddy pet", "抚摸你的陪伴"),
        ("buddy stats", "显示陪伴状态"),
        ("buddy new", "孵化一只新的随机陪伴"),
        ("buddy list", "查看所有陪伴"),
        ("buddy select", "切换当前陪伴（例如 /buddy select 2）"),
        ("buddy mute", "静音陪伴反应"),
        ("buddy unmute", "取消静音陪伴反应"),
        ("buddy ia", "挂机冒险 — 肉鸽式世界探索游戏"),
        ("exit", "退出 REPL"),
    ]

    def _all_commands(self) -> list[tuple[str, str]]:
        """将 _COMMAND_TABLE 中的条目与额外命令合并，作为唯一真实来源。"""
        cmds: list[tuple[str, str]] = [(name, desc) for name, desc, _ in _COMMAND_TABLE]
        cmds.extend(self._EXTRA_COMMANDS)
        return cmds

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            return

        query = text[1:].lower()
        all_commands = self._all_commands()

        # 内置命令
        for name, desc in all_commands:
            if not query or name.startswith(query):
                yield Completion(
                    f"/{name}",
                    start_position=-len(text),
                    display=f"/{name}",
                    display_meta=desc,
                )

        # 动态技能命令
        try:
            from cc_nano.features.skills import list_skills

            seen = {name for name, _ in all_commands}
            for skill in list_skills(user_invocable_only=True):
                # 如果已被内置命令覆盖则跳过
                if skill.name in seen:
                    continue
                if not query or skill.name.startswith(query):
                    yield Completion(
                        f"/{skill.name}",
                        start_position=-len(text),
                        display=f"/{skill.name}",
                        display_meta=(
                            skill.description[:40] if skill.description else "技能"
                        ),
                    )
        except Exception:
            pass


slash_completer = SlashCommandCompleter()


def bordered_prompt(
    con: Console,
    history: FileHistory | None = None,
    completer: Completer | None = None,
    animator_toolbar=None,
    refresh_interval: float | None = None,
    terminal_mode_ref: list | None = None,
) -> str:
    """带边框输入框的提示，可根据内容高度自适应。

    terminal_mode_ref 是一个可变的 [bool] 列表，使得 '!' 可以就地切换终端模式。

    当按下 Ctrl+C 时抛出 KeyboardInterrupt，当空缓冲区时按下 Ctrl+D 抛出 EOFError。
    """
    import os

    if terminal_mode_ref is None:
        terminal_mode_ref = [False]

    def _is_terminal():
        return terminal_mode_ref[0]

    def _accept(b):
        get_app().exit(result=b.text)
        return True  # 保留缓冲区文本，以便最终渲染时保留输入内容

    buf = Buffer(
        history=history,
        completer=completer,
        complete_while_typing=False,
        accept_handler=_accept,
    )

    def _trigger_completion_next_tick():
        """在下一个事件循环周期调度 start_completion。

        这避免了与 prompt_toolkit 内部在文本插入时同步发生的补全重置产生竞态条件。
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            loop.call_soon(lambda: buf.start_completion(select_first=False))
        except RuntimeError:
            pass

    def _on_text_changed(_buf):
        """当输入以 '/' 开头时触发补全弹出窗口。"""
        if _buf.text.lstrip().startswith("/"):
            _trigger_completion_next_tick()

    buf.on_text_changed += _on_text_changed

    def _top():
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        fill = "\u2500" * max(0, w - 1)
        if _is_terminal():
            return [("bold fg:ansiyellow", f"\u256d{fill}")]
        return [("bold fg:ansicyan", f"\u256d{fill}")]

    def _bot():
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        if _is_terminal():
            hints = "\u2500 终端模式 · ! 退出 · 回车运行 "
            fill = "\u2500" * max(0, w - 1 - len(hints))
            parts: list[tuple[str, str]] = [("fg:ansiyellow", f"\u2570{hints}{fill}")]
        else:
            hints = "\u2500 回车发送 · Alt+Enter 换行 · ! shell · / 命令 "
            fill = "\u2500" * max(0, w - 1 - len(hints))
            parts: list[tuple[str, str]] = [("fg:ansicyan", f"\u2570{hints}{fill}")]

        if animator_toolbar:
            extra = animator_toolbar()
            if extra:
                parts.append(("", "\n"))
                parts.extend(extra)
        return parts

    def _line_prefix(lineno, wrap_count):
        """首行视觉行显示 '> ' 或 '$ '，其余行显示两个空格作为填充。"""
        if lineno == 0 and wrap_count == 0:
            if _is_terminal():
                return [("bold fg:ansiyellow", "$ ")]
            return [("bold fg:ansicyan", "> ")]
        return [("", "  ")]

    body = HSplit(
        [
            Window(FormattedTextControl(_top), height=1, dont_extend_height=True),
            Window(
                BufferControl(buffer=buf),
                get_line_prefix=_line_prefix,
                height=Dimension(min=1),
                dont_extend_height=True,
                wrap_lines=True,
            ),
            Window(FormattedTextControl(_bot), dont_extend_height=True),
        ]
    )

    root = FloatContainer(
        content=body,
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                content=CompletionsMenu(max_height=8, scroll_offset=1),
            ),
        ],
    )

    kb = KeyBindings()

    @kb.add("!")
    def _(event):
        if not buf.text:
            # 就地切换终端模式，不提交
            terminal_mode_ref[0] = not terminal_mode_ref[0]
            event.app.invalidate()  # 强制刷新界面以改变颜色
        else:
            buf.insert_text("!")

    @kb.add("enter")
    def _(event):
        # 特性：反斜杠 + 回车 = 换行续行
        # 在按键绑定层面检查，避免 buffer.reset() 清空文本
        if buf.text.endswith("\\"):
            buf.delete_before_cursor(1)
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()

    @kb.add("escape", "enter")
    def _(event):
        buf.insert_text("\n")

    @kb.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt())

    @kb.add("c-d")
    def _(event):
        if not buf.text:
            event.app.exit(exception=EOFError())

    app = PTApp(
        layout=Layout(root),
        key_bindings=kb,
        full_screen=False,
        refresh_interval=refresh_interval,
    )
    app.layout.focus(buf)
    return app.run()



async def bordered_prompt_async(
    con: Console,
    history: FileHistory | None = None,
    completer: Completer | None = None,
    animator_toolbar=None,
    refresh_interval: float | None = None,
    terminal_mode_ref: list | None = None,
) -> str:
    """异步版带边框输入框的提示，可根据内容高度自适应。

    terminal_mode_ref 是一个可变的 [bool] 列表，使得 '!' 可以就地切换终端模式。

    当按下 Ctrl+C 时抛出 KeyboardInterrupt，当空缓冲区时按下 Ctrl+D 抛出 EOFError。
    """
    if terminal_mode_ref is None:
        terminal_mode_ref = [False]

    def _is_terminal():
        return terminal_mode_ref[0]

    def _accept(b):
        get_app().exit(result=b.text)
        return True  # 保留缓冲区文本，以便最终渲染时保留输入内容

    buf = Buffer(
        history=history,
        completer=completer,
        complete_while_typing=False,
        accept_handler=_accept,
    )

    def _trigger_completion_next_tick():
        """在下一个事件循环周期调度 start_completion。

        这避免了与 prompt_toolkit 内部在文本插入时同步产生的补全重置产生竞态条件。
        """
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon(lambda: buf.start_completion(select_first=False))
        except RuntimeError:
            pass

    def _on_text_changed(_buf):
        """当输入以 '/' 开头时触发补全弹出窗口。"""
        if _buf.text.lstrip().startswith("/"):
            _trigger_completion_next_tick()

    buf.on_text_changed += _on_text_changed

    def _top():
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        fill = "\u2500" * max(0, w - 1)
        if _is_terminal():
            return [("bold fg:ansiyellow", f"\u256d{fill}")]
        return [("bold fg:ansicyan", f"\u256d{fill}")]

    def _bot():
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        if _is_terminal():
            hints = "\u2500 终端模式 · ! 退出 · 回车运行 "
            fill = "\u2500" * max(0, w - 1 - len(hints))
            parts: list[tuple[str, str]] = [("fg:ansiyellow", f"\u2570{hints}{fill}")]
        else:
            hints = "\u2500 回车发送 · Alt+Enter 换行 · ! shell · / 命令 "
            fill = "\u2500" * max(0, w - 1 - len(hints))
            parts: list[tuple[str, str]] = [("fg:ansicyan", f"\u2570{hints}{fill}")]

        if animator_toolbar:
            extra = animator_toolbar()
            if extra:
                parts.append(("", "\n"))
                parts.extend(extra)
        return parts

    def _line_prefix(lineno, wrap_count):
        """首行视觉行显示 '> ' 或 '$ '，其余行显示两个空格作为填充。"""
        if lineno == 0 and wrap_count == 0:
            if _is_terminal():
                return [("bold fg:ansiyellow", "$ ")]
            return [("bold fg:ansicyan", "> ")]
        return [("", "  ")]

    body = HSplit(
        [
            Window(FormattedTextControl(_top), height=1, dont_extend_height=True),
            Window(
                BufferControl(buffer=buf),
                get_line_prefix=_line_prefix,
                height=Dimension(min=1),
                dont_extend_height=True,
                wrap_lines=True,
            ),
            Window(FormattedTextControl(_bot), dont_extend_height=True),
        ]
    )

    root = FloatContainer(
        content=body,
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                content=CompletionsMenu(max_height=8, scroll_offset=1),
            ),
        ],
    )

    kb = KeyBindings()

    @kb.add("!")
    def _(event):
        if not buf.text:
            # 就地切换终端模式，不提交
            terminal_mode_ref[0] = not terminal_mode_ref[0]
            event.app.invalidate()  # 强制刷新界面以改变颜色
        else:
            buf.insert_text("!")

    @kb.add("enter")
    def _(event):
        # 特性：反斜杠 + 回车 = 换行续行
        if buf.text.endswith("\\"):
            buf.delete_before_cursor(1)
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()

    @kb.add("escape", "enter")
    def _(event):
        buf.insert_text("\n")

    @kb.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt())

    @kb.add("c-d")
    def _(event):
        if not buf.text:
            event.app.exit(exception=EOFError())

    app = PTApp(
        layout=Layout(root),
        key_bindings=kb,
        full_screen=False,
        refresh_interval=refresh_interval,
    )
    app.layout.focus(buf)
    return await app.run_async()