"""AskUserQuestion 工具 —— 让模型向用户提问多项选择题。

使用 prompt_toolkit 实现正确的终端处理（避免 EscListener cbreak 冲突），
并提供一个支持箭头键导航的菜单，其中“其他”选项带有内联文本输入 ——
与官方行为一致：

- “其他”是内联文本输入，而非单独的提示
- 在“其他”的文本输入状态下，上下箭头键仍可导航
- 当焦点在“其他”上时，普通字符输入到缓冲区
- 数字键：在普通选项上 -> 快速选择；在“其他”上 -> 键入缓冲区
- 回车：在普通选项上 -> 立即选择；在“其他”上 -> 提交输入的文本
- 在“其他”上按 Esc：有文本时清除文本并向上移动；无文本时取消
"""

from __future__ import annotations

from cc_nano.core.tool import Tool, ToolResult

# 内部哨兵值 —— 永远不会暴露给模型。
_OTHER = "__other__"


# ---------------------------------------------------------------------------
# 基于 prompt_toolkit 的交互式选择器
# ---------------------------------------------------------------------------


def _select_one(
    question: str, labels: list[str], descriptions: list[str], multilines: list[bool]
) -> str | None:
    """支持箭头键导航的单选菜单。

    最后一个选项总是“其他”，当焦点在其上时显示内联文本输入。
    返回选中的标签、“其他”时输入的文本，取消时返回 ``None``。

    如果所选选项的 multiline=True，
    则弹出多行编辑器（以空行结束，或 Ctrl+D 提交）。
    """
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    other_idx = len(labels) - 1  # 最后一项总是“其他”
    cursor = [0]
    text_buf: list[str] = [""]  # “其他”文本的可变缓冲区
    result: list[str] = []

    kb = KeyBindings()

    def _on_other() -> bool:
        return cursor[0] == other_idx

    # --- 箭头导航（即使在“其他”输入状态下也始终有效）----------------

    @kb.add("up")
    def _up(event):
        cursor[0] = (cursor[0] - 1) % len(labels)

    @kb.add("down")
    def _down(event):
        cursor[0] = (cursor[0] + 1) % len(labels)

    # --- 回车：选择普通选项或提交“其他”文本 --------------------------

    @kb.add("enter")
    def _enter(event):
        idx = cursor[0]
        if _on_other():
            if text_buf[0]:
                result.append(text_buf[0])
            else:
                result.append(_OTHER)  # 空“其他” = 取消
        else:
            # 如果该选项支持多行，则弹出多行输入框
            if multilines[idx]:
                val = _multiline_input(f"请输入 {labels[idx]} 的内容（多行）：")
                if val is None:
                    return  # 取消，不退出菜单
                result.append(val)
            else:
                result.append(labels[idx])
        event.app.exit()

    # --- Esc / Ctrl-C -------------------------------------------------------

    @kb.add("c-c")
    def _cancel(event):
        event.app.exit()

    @kb.add("escape")
    def _esc(event):
        if _on_other() and text_buf[0]:
            # 清除文本并向上移动光标（与官方一致：Esc 退出输入）
            text_buf[0] = ""
            cursor[0] = max(other_idx - 1, 0)
        else:
            event.app.exit()

    # --- “其他”中的退格 -------------------------------------------------

    @kb.add("backspace")
    def _bs(event):
        if _on_other():
            text_buf[0] = text_buf[0][:-1]

    # --- 可打印字符 -------------------------------------------------------
    # 在普通选项上：数字键快速选择；其他字符跳转到“其他”。
    # 在“其他”上：所有可打印字符键入缓冲区。

    @kb.add("<any>")
    def _char(event):
        ch = event.data
        if not ch or not ch.isprintable():
            return

        if _on_other():
            # 在“其他”的文本输入中键入
            text_buf[0] += ch
            return

        # 不在“其他”上 —— 检查数字快速选择
        if ch.isdigit():
            idx = int(ch) - 1
            if 0 <= idx < len(labels):
                if idx == other_idx:
                    # 数字键落在“其他”上：仅聚焦（与官方一致）
                    cursor[0] = other_idx
                else:
                    # 数字键在普通选项上：立即选择
                    # 如果该选项支持多行，弹出多行输入
                    if multilines[idx]:
                        val = _multiline_input(f"请输入 {labels[idx]} 的内容（多行）：")
                        if val is not None:
                            result.append(val)
                            event.app.exit()
                    else:
                        result.append(labels[idx])
                        event.app.exit()
            return

        # 在普通选项上按非数字字符 -> 跳转到“其他”并开始输入
        cursor[0] = other_idx
        text_buf[0] += ch

    # --- 渲染 ----------------------------------------------------------

    def _get_tokens():
        tokens = [("bold", f"? {question}\n")]
        for i, (label, desc) in enumerate(zip(labels, descriptions)):
            is_cur = i == cursor[0]
            prefix = "  ❯ " if is_cur else "    "
            style = "ansibrightcyan" if is_cur else ""

            if i == other_idx:
                # “其他”行 —— 内联文本输入（与官方一致）
                if text_buf[0]:
                    tokens.append((style, f"{prefix}{i+1}) "))
                    tokens.append(("ansibrightgreen bold", text_buf[0]))
                    if is_cur:
                        tokens.append(("ansigray", "█"))
                elif is_cur:
                    tokens.append((style, f"{prefix}{i+1}) "))
                    tokens.append(("ansigray", "输入内容..."))
                else:
                    tokens.append(
                        ("ansigray" if not is_cur else style, f"{prefix}{i+1}) {label}")
                    )
            else:
                # 普通选项：如果支持多行，添加提示图标
                marker = " ✎" if multilines[i] else ""
                tokens.append((style, f"{prefix}{i+1}) {label}{marker}"))
                if desc:
                    tokens.append(("ansigray", f" — {desc}"))
            tokens.append(("", "\n"))

        # 提示栏
        if _on_other() and text_buf[0]:
            tokens.append(("ansigray", "  ↵ 提交 · esc 清除"))
        else:
            tokens.append(("ansigray", "  ↑↓ 导航 · ↵ 选择 · ✎ 多行输入"))
        return tokens

    control = FormattedTextControl(_get_tokens)
    app: Application = Application(
        layout=Layout(Window(control)),
        key_bindings=kb,
        full_screen=False,
    )

    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return None

    if not result:
        return None
    return result[0] if result[0] != _OTHER else None


def _select_multi(
    question: str, labels: list[str], descriptions: list[str], multilines: list[bool]
) -> list[str] | None:
    """支持箭头键导航的多选菜单，带有内联“其他”文本输入。

    空格键切换选中状态，回车确认。与官方行为一致：
    箭头键始终可以导航，在“其他”上键入进入文本缓冲区，
    空格键切换对勾。

    如果某个选项的 multilines[idx] 为 True，且用户选中了该选项，
    则在最终确认后弹出多行输入框，输入的内容会作为该选项的值。
    """
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    other_idx = len(labels) - 1
    cursor = [0]
    checked: set[int] = set()
    text_buf: list[str] = [""]
    confirmed = [False]

    kb = KeyBindings()

    def _on_other() -> bool:
        return cursor[0] == other_idx

    @kb.add("up")
    def _up(event):
        cursor[0] = (cursor[0] - 1) % len(labels)

    @kb.add("down")
    def _down(event):
        cursor[0] = (cursor[0] + 1) % len(labels)

    @kb.add("space")
    def _toggle(event):
        if _on_other():
            # 在“其他”上按空格 -> 向缓冲区输入一个空格
            text_buf[0] += " "
            checked.add(other_idx)
            return
        idx = cursor[0]
        if idx in checked:
            checked.discard(idx)
        else:
            checked.add(idx)

    @kb.add("enter")
    def _confirm(event):
        confirmed[0] = True
        event.app.exit()

    @kb.add("c-c")
    def _cancel_cc(event):
        event.app.exit()

    @kb.add("escape")
    def _esc(event):
        if _on_other() and text_buf[0]:
            text_buf[0] = ""
            checked.discard(other_idx)
            cursor[0] = max(other_idx - 1, 0)
        else:
            event.app.exit()

    @kb.add("backspace")
    def _bs(event):
        if _on_other():
            text_buf[0] = text_buf[0][:-1]
            if not text_buf[0]:
                checked.discard(other_idx)

    @kb.add("<any>")
    def _char(event):
        ch = event.data
        if not ch or not ch.isprintable():
            return
        if _on_other():
            text_buf[0] += ch
            checked.add(other_idx)
            return
        # 不在“其他”上：数字键聚焦选项，其他字符跳转到“其他”
        if ch.isdigit():
            idx = int(ch) - 1
            if 0 <= idx < len(labels):
                cursor[0] = idx
            return
        cursor[0] = other_idx
        text_buf[0] += ch
        checked.add(other_idx)

    def _get_tokens():
        tokens = [("bold", f"? {question}\n")]
        for i, (label, desc) in enumerate(zip(labels, descriptions)):
            is_cur = i == cursor[0]
            mark = "✓" if i in checked else " "
            prefix = "  ❯ " if is_cur else "    "
            style = "ansibrightcyan" if is_cur else ""

            if i == other_idx:
                if text_buf[0]:
                    tokens.append((style, f"{prefix}[{mark}] {i+1}) "))
                    tokens.append(("ansibrightgreen bold", text_buf[0]))
                    if is_cur:
                        tokens.append(("ansigray", "█"))
                elif is_cur:
                    tokens.append((style, f"{prefix}[{mark}] {i+1}) "))
                    tokens.append(("ansigray", "输入内容"))
                else:
                    tokens.append(
                        (
                            "ansigray" if not is_cur else style,
                            f"{prefix}[{mark}] {i+1}) {label}",
                        )
                    )
            else:
                tokens.append((style, f"{prefix}[{mark}] {i+1}) {label}"))
                if desc:
                    tokens.append(("ansigray", f" — {desc}"))
            tokens.append(("", "\n"))

        tokens.append(("ansigray", "  ↑↓ 导航 · 空格 切换 · ↵ 提交"))
        return tokens

    control = FormattedTextControl(_get_tokens)
    app: Application = Application(
        layout=Layout(Window(control)),
        key_bindings=kb,
        full_screen=False,
    )

    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return None

    if not confirmed[0]:
        return None

    results: list[str] = []
    for i in sorted(checked):
        if i == other_idx:
            if text_buf[0]:
                results.append(text_buf[0])
        else:
            if multilines[i]:
                val = _multiline_input(f"请输入 {labels[i]} 的内容（多行）：")
                if val is not None:
                    results.append(val)
                # 如果用户取消了多行输入，则忽略该选项
            else:
                results.append(labels[i])
    return results if results else None

def _multiline_input(prompt: str) -> str | None:
    """
    多行文本输入，返回输入的字符串，取消时返回 None。
    用户按 Ctrl+D 提交，按 ESC 或 Ctrl+C 取消。
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()
    result = [None]

    @kb.add("c-c")
    @kb.add("escape")
    def _(event):
        result[0] = None
        event.app.exit()

    @kb.add("c-d")
    def _(event):
        # Ctrl+D 提交当前内容
        buf = event.app.current_buffer
        result[0] = buf.text
        event.app.exit()

    session = PromptSession(message=prompt, key_bindings=kb, multiline=True)
    try:
        text = session.prompt()
        # 如果用户直接按回车（空字符串），视为取消
        return text if text else None
    except (EOFError, KeyboardInterrupt):
        return None

# ---------------------------------------------------------------------------
# 工具类
# ---------------------------------------------------------------------------


class AskUserQuestionTool(Tool):
    @property
    def name(self) -> str:
        return "AskUserQuestion"

    @property
    def description(self) -> str:
        return (
            "当你需要在执行过程中向用户提问时使用此工具。这允许你：\n"
            "1. 收集用户偏好或需求\n"
            "2. 澄清模糊的指令\n"
            "3. 在实施过程中获取关于实现选择的决策\n"
            "4. 向用户提供多个方向供其选择。\n\n"
            "使用说明：\n"
            "- 用户始终可以选择“其他”来提供自定义文本输入\n"
            "- 对于一个问题，设置 multiSelect: true 允许选择多个答案\n"
            "- 如果你推荐某个特定选项，请将其放在列表的第一个，并在标签末尾加上“（推荐）”\n\n"
            "计划模式注意事项：在计划模式下，请在最终确定计划之前使用此工具澄清需求或选择方案。"
            "不要使用此工具询问“我的计划准备好了吗？”或“我是否应该继续？”—— 计划批准请使用 ExitPlanMode。"
            "请不要在问题中引用“计划”，因为用户在你调用 ExitPlanMode 之前是看不到计划的。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                        "multiline": {"type": "boolean", "default": False},
                                    },
                                    "required": ["label", "description"],
                                },
                                "minItems": 2,
                                "maxItems": 4,
                            },
                            "multiSelect": {"type": "boolean", "default": False},
                        },
                        "required": ["question", "options"],
                    },
                    "minItems": 1,
                    "maxItems": 4,
                }
            },
            "required": ["questions"],
        }

    def is_read_only(self) -> bool:
        return True

    def execute(self, **kwargs) -> ToolResult:
        questions = kwargs.get("questions", [])
        if not questions:
            return ToolResult(content="未提供问题。", is_error=True)

        answers: list[str] = []

        for q in questions:
            question_text = q.get("question", "")
            options = q.get("options", [])
            multi = q.get("multiSelect", False)

            # 构建标签/描述列表 —— 追加“其他”（输入选项，无描述）
            labels = [o["label"] for o in options] + ["其他"]
            descs = [o.get("description", "") for o in options] + [""]
            # 多行标志列表：对于每个选项，如果配置了 multiline: true，则标记为 True
            multilines = [o.get("multiline", False) for o in options] + [False]

            if multi:
                selected = _select_multi(question_text, labels, descs, multilines)
                if selected is None:
                    return ToolResult(content="用户取消了问题。", is_error=True)
                answer = ", ".join(selected) if selected else "（未选择）"
            else:
                chosen = _select_one(question_text, labels, descs, multilines)
                if chosen is None:
                    return ToolResult(content="用户取消了问题。", is_error=True)
                answer = chosen

            answers.append(f"{question_text} => {answer}")

        result_text = "用户回答：\n" + "\n".join(answers)
        return ToolResult(content=result_text)
