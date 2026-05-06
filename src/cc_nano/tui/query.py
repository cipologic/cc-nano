"""运行单次查询交互，并提供 TUI 反馈（加载动画、Markdown 流式输出）。"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console

from cc_nano.core.engine import AbortedError, Engine
from cc_nano.core.permissions import PermissionChecker
from cc_nano.tui.keylistener import EscListener
from cc_nano.tui.rendering import (SpinnerManager, StreamingMarkdown,
                                   collapsed_tool_summary, render_todo_list,
                                   tool_preview)

if TYPE_CHECKING:
    from cc_nano.features.todo import TodoManager

console = Console()

_TODO_TOOL_NAMES = frozenset({"TodoWrite", "TodoUpdate"})


def run_query(
    engine: Engine,
    user_input: str | list,
    print_mode: bool,
    permissions: PermissionChecker | None = None,
    quiet: bool = False,
    todo_manager: TodoManager | None = None,
) -> None:
    """运行单轮对话。Ctrl+C 或 Esc 会取消当前轮次。

    如果 *quiet* 为 True，则抑制所有终端输出（加载动画、工具调用、文本）。
    用于后台任务（如 auto-dream）。
    """
    listener = EscListener(on_cancel=engine.abort)
    if permissions:
        permissions.set_esc_listener(listener)

    spinner = SpinnerManager(console)
    md_stream = StreamingMarkdown(console)
    first_text = True
    streaming = False
    # 跟踪待处理的工具调用，用于显示加载动画。
    # key: 工具唯一标识, value: (工具名, 显示行)
    pending_tools: dict[str, tuple[str, str]] = {}

    try:
        with listener:
            if not quiet:
                spinner.start("思考中…")

            for event in engine.submit(user_input):
                if not quiet and streaming and listener.pressed:
                    md_stream.flush()
                    spinner.stop()
                    engine.cancel_turn()
                    console.print("\n[dim yellow]⏹ 已取消当前轮次 (Esc)[/dim yellow]")
                    return

                if event[0] == "text":
                    if quiet:
                        continue
                    if first_text:
                        spinner.stop()
                        streaming = True
                        first_text = False
                    if print_mode:
                        print(event[1], end="", flush=True)
                    else:
                        md_stream.feed(event[1])

                elif event[0] == "waiting":
                    if not quiet:
                        md_stream.flush()
                    streaming = False
                    if not quiet:
                        listener.resume()
                        spinner.start("准备调用工具…")

                elif event[0] == "tool_call":
                    if not quiet:
                        spinner.stop()
                        streaming = False
                        listener.pause()
                        _, tool_name, tool_input, activity = event
                        preview = tool_preview(tool_name, tool_input)
                        key = f"{tool_name}({preview})"
                        pending_tools[key] = (tool_name, f"↳ {key}")

                elif event[0] == "tool_executing":
                    if not quiet:
                        _, tool_name, tool_input, activity = event
                        n = len(pending_tools)
                        if tool_name == "AskUserQuestion":
                            # 交互式提示 —— 停止加载动画，使其在干净的独立行渲染
                            spinner.stop()
                            _, line = next(
                                iter(pending_tools.values()), ("", f"↳ {tool_name}")
                            )
                            console.print(f"[dim]{line}[/dim]", highlight=False)
                        elif n > 1:
                            names = [tn for tn, _ in pending_tools.values()]
                            spinner.start(collapsed_tool_summary(names))
                        else:
                            _, line = next(
                                iter(pending_tools.values()), ("", f"↳ {tool_name}")
                            )
                            activity_text = activity or f"正在运行 {tool_name}…"
                            spinner.start(f"{line} … {activity_text}")

                elif event[0] == "tool_result":
                    if not quiet:
                        spinner.stop()
                        _, tool_name, tool_input, result = event
                        preview = tool_preview(tool_name, tool_input)
                        key = f"{tool_name}({preview})"
                        tname, line = pending_tools.pop(key, (tool_name, f"↳ {key}"))

                        # 待办工具：渲染任务清单，而不是显示 ✓/✗ 行
                        if tool_name in _TODO_TOOL_NAMES and todo_manager is not None:
                            if result.is_error:
                                console.print(
                                    f"[dim]{line}[/dim] [red]✗[/red]", highlight=False
                                )
                                console.print(f"  [red]{result.content[:200]}[/red]")
                            else:
                                render_todo_list(todo_manager.get_items(), console)
                        elif result.is_error:
                            console.print(
                                f"[dim]{line}[/dim] [red]✗[/red]", highlight=False
                            )
                            console.print(f"  [red]{result.content[:200]}[/red]")
                        else:
                            console.print(
                                f"[dim]{line}[/dim] [green]✓[/green]", highlight=False
                            )

                        if pending_tools:
                            names = [tn for tn, _ in pending_tools.values()]
                            spinner.start(collapsed_tool_summary(names))
                        else:
                            streaming = False
                            listener.resume()
                            # 在加载动画中显示当前进行中的待办项
                            spinner_text = "思考中…"
                            if todo_manager is not None:
                                wip = todo_manager.in_progress_item()
                                if wip:
                                    label = wip.subject
                                    if len(label) > 60:
                                        label = label[:57] + "…"
                                    spinner_text = label
                            spinner.start(spinner_text)
                            first_text = True

                elif event[0] == "error":
                    if not quiet:
                        md_stream.flush()
                        spinner.stop()
                        console.print(f"\n[bold red]{event[1]}[/bold red]")

            md_stream.flush()
            spinner.stop()
    except (AbortedError, KeyboardInterrupt):
        md_stream.flush()
        spinner.stop()
        if not isinstance(sys.exc_info()[1], AbortedError):
            engine.cancel_turn()
        if not quiet:
            console.print("\n[dim yellow]⏹ 已取消当前轮次[/dim yellow]")
        return
    finally:
        md_stream.flush()
        spinner.stop()
        if permissions:
            permissions.set_esc_listener(None)

    if not print_mode:
        console.print()
