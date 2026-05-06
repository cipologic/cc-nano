"""使用 rich 的同伴卡片和动画的终端渲染。"""

from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .sprites import render_face, render_sprite
from .types import (MOOD_DIMENSIONS, RARITY_COLORS, RARITY_STARS, STAT_NAMES,
                    Companion, CompanionBones, CompanionSoul)


def _stat_bar(value: int, width: int = 20) -> str:
    filled = round(value / 100 * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def render_companion_card(companion: Companion, console: Console) -> None:
    """显示完整的同伴卡片，包含精灵、属性和信息。"""
    color = RARITY_COLORS.get(companion.rarity, "dim")
    stars = RARITY_STARS.get(companion.rarity, "\u2605")
    shiny_tag = " \u2728 闪光" if companion.shiny else ""

    sprite_lines = render_sprite(
        CompanionBones(
            rarity=companion.rarity,
            species=companion.species,
            eye=companion.eye,
            hat=companion.hat,
            shiny=companion.shiny,
            stats=companion.stats,
        ),
        frame=0,
    )

    # 构建卡片内容
    lines: list[str] = []
    lines.append(f"  {companion.name}（{companion.species}）{shiny_tag}")
    lines.append(f"  {stars}  （{companion.rarity}）")
    lines.append("")

    # 精灵
    for sl in sprite_lines:
        lines.append(f"  {sl}")
    lines.append("")

    # 个性
    lines.append(f"  “{companion.personality}”")
    lines.append("")

    # 属性
    for stat in STAT_NAMES:
        val = companion.stats.get(stat, 0)
        bar = _stat_bar(val)
        lines.append(f"  {stat:<10} {bar} {val:>3}")

    # 心情
    lines.append("")
    lines.append("  心情：")
    mood = companion.mood
    for dim in MOOD_DIMENSIONS:
        val = getattr(mood, dim)
        bar = _stat_bar(val)
        lines.append(f"  {dim.capitalize():<10} {bar} {val:>3}")
    lines.append(f"  感受：{mood.dominant().lower()}")

    # 孵化日期
    from datetime import datetime, timezone

    hatched = datetime.fromtimestamp(companion.hatched_at / 1000, tz=timezone.utc)
    lines.append("")
    lines.append(f'  孵化日期：{hatched.strftime("%Y-%m-%d")}')

    content = "\n".join(lines)
    panel = Panel(
        Text.from_ansi(content),
        title=f"[{color}]\u2605 同伴 \u2605[/{color}]",
        border_style=color,
        padding=(1, 2),
    )
    console.print(panel)


def render_hatch_animation(
    bones: CompanionBones, soul: CompanionSoul, console: Console
) -> None:
    """展示蛋晃动 → 裂纹 → 破碎 → 揭示动画。

    稀有度越高，晃动阶段越长，揭示效果越华丽。
    """
    color = RARITY_COLORS.get(bones.rarity, "dim")
    stars = RARITY_STARS.get(bones.rarity, "\u2605")

    # 晃动帧 —— 蛋左右摇摆
    egg_left = [
        "            ",
        "    .--.    ",
        "   /    \\   ",
        "  |      |  ",
        "   \\    /   ",
        "    `--\u00b4    ",
    ]
    egg_center = [
        "            ",
        "     .--.   ",
        "    /    \\  ",
        "   |      | ",
        "    \\    /  ",
        "     `--\u00b4   ",
    ]
    egg_right = [
        "            ",
        "      .--. ",
        "     /    \\",
        "    |      |",
        "     \\    / ",
        "      `--\u00b4  ",
    ]

    # 裂纹帧 —— 渐进损坏
    crack1 = [
        "            ",
        "     .--.   ",
        "    / *  \\  ",
        "   |      | ",
        "    \\    /  ",
        "     `--\u00b4   ",
    ]
    crack2 = [
        "            ",
        "     .--*   ",
        "    /*   \\  ",
        "   * *  * | ",
        "    \\*  */  ",
        "     *--\u00b4   ",
    ]
    crack3 = [
        "            ",
        "     * --*  ",
        "    *  * \\  ",
        "   * ** * * ",
        "    ** * *  ",
        "     *--*   ",
    ]

    # 稀有度决定晃动次数：普通=2，罕见=3，稀有=4，史诗=5，传说=6
    rarity_wobbles = {"common": 2, "uncommon": 3, "rare": 4, "epic": 5, "legendary": 6}
    wobble_count = rarity_wobbles.get(bones.rarity, 2)

    sprite = render_sprite(bones)
    shiny_tag = " \u2728 闪光！" if bones.shiny else ""

    with Live(console=console, refresh_per_second=8, transient=False) as live:
        # 阶段 1：晃动 —— 蛋以越来越快的速度摇摆
        wobble_frames = [egg_center, egg_left, egg_center, egg_right]
        for i in range(wobble_count):
            speed = max(0.15, 0.4 - i * 0.05)  # 越来越快
            for wf in wobble_frames:
                text = Text("\n".join(f"  {line}" for line in wf), style="dim")
                live.update(text)
                time.sleep(speed)

        # 阶段 2：裂纹序列
        for crack, delay in [(crack1, 0.4), (crack2, 0.3), (crack3, 0.2)]:
            text = Text("\n".join(f"  {line}" for line in crack), style="yellow")
            live.update(text)
            time.sleep(delay)

        # 阶段 3：破碎 —— 短暂闪烁
        shatter = [
            "            ",
            "    * * *   ",
            "   *     *  ",
            "  *  \u2726  \u2726  * ",
            "   *     *  ",
            "    * * *   ",
        ]
        text = Text("\n".join(f"  {line}" for line in shatter), style="bold yellow")
        live.update(text)
        time.sleep(0.3)

        # 阶段 4：揭示 —— 同伴出现
        reveal_lines = [f"  {line}" for line in sprite]
        reveal_lines.append("")
        reveal_lines.append(f"  {soul.name} 孵化啦！{stars}{shiny_tag}")
        reveal_lines.append(f"  {bones.rarity.upper()} {bones.species}")
        reveal_lines.append(f"  “{soul.personality}”")
        text = Text("\n".join(reveal_lines), style=f"bold {color}")
        live.update(text)
        time.sleep(2.0)

    console.print()  # 动画结束后空一行


def render_compact_status(companion: Companion) -> str:
    """一行简洁的同伴状态，用于在 REPL 提示符前显示。"""
    face = render_face(
        CompanionBones(
            rarity=companion.rarity,
            species=companion.species,
            eye=companion.eye,
            hat=companion.hat,
            shiny=companion.shiny,
            stats=companion.stats,
        )
    )
    stars = RARITY_STARS.get(companion.rarity, "\u2605")
    shiny = " \u2728" if companion.shiny else ""
    dominant = companion.mood.dominant().lower()
    return (
        f"  {face} {companion.name}（{companion.species}）{stars}{shiny}（{dominant}）"
    )


def render_speech_bubble(text: str, color: str = "dim") -> str:
    """渲染一个圆角对话框，与 CompanionSprite.tsx 的 SpeechBubble 一致。

    使用 Unicode 圆角画线字符，呈现精致外观。
    """
    max_width = 30
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip() if current else word
    if current:
        lines.append(current)

    if not lines:
        return ""

    width = max(len(line) for line in lines)
    # 圆角：╭ ╮ ╰ ╯ 匹配原始代码的 borderStyle="round"
    top = "\u256d" + "\u2500" * (width + 2) + "\u256e"
    bottom = "\u2570" + "\u2500" * (width + 2) + "\u256f"
    body = "\n".join(f"\u2502 {line:<{width}} \u2502" for line in lines)
    return f"{top}\n{body}\n{bottom}"


def render_speech_bubble_rich(
    text: str,
    companion: Companion,
    console: Console,
    fading: bool = False,
) -> None:
    """使用 rich 的 Panel 渲染对话框，边框颜色取决于稀有度。

    匹配 CompanionSprite.tsx SpeechBubble 样式：
    - 圆角边框（Panel 默认）
    - 稀有度颜色边框
    - 斜体文本
    - 淡出时变暗
    """
    color = RARITY_COLORS.get(companion.rarity, "dim")
    border_color = "dim" if fading else color
    text_style = "dim italic" if fading else f"{color} italic"

    panel = Panel(
        Text(text, style=text_style),
        border_style=border_color,
        width=min(36, len(text) + 6),
        padding=(0, 1),
    )
    console.print(panel)


def render_companion_list(
    companions: list[Companion], active_index: int, console: Console
) -> None:
    """渲染一个表格，展示所有拥有的同伴（仓库）。"""
    if not companions:
        console.print("[dim]还没有同伴。输入 /buddy 来孵化一只！[/dim]")
        return

    table = Table(title="同伴收藏", border_style="dim", padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("名字", min_width=12)
    table.add_column("种类", min_width=10)
    table.add_column("稀有度", min_width=10)
    table.add_column("表情", min_width=8)
    table.add_column("闪光", width=5)

    for i, comp in enumerate(companions):
        color = RARITY_COLORS.get(comp.rarity, "dim")
        stars = RARITY_STARS.get(comp.rarity, "\u2605")
        face = render_face(
            CompanionBones(
                rarity=comp.rarity,
                species=comp.species,
                eye=comp.eye,
                hat=comp.hat,
                shiny=comp.shiny,
                stats=comp.stats,
            )
        )
        marker = "\u25b6" if i == active_index else " "
        shiny_mark = "\u2728" if comp.shiny else ""
        table.add_row(
            f"{marker}{i + 1}",
            f"[{color}]{comp.name}[/{color}]",
            comp.species,
            f"[{color}]{stars} {comp.rarity}[/{color}]",
            face,
            shiny_mark,
        )

    console.print(table)
