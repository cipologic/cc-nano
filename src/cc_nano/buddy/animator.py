"""实时伙伴动画状态管理器。

基于 CompanionSprite.tsx 中滴答动画系统的移植。

驱动空闲动画（7.5秒周期，含眨眼）、兴奋模式（说话/抚摸时快速循环帧），
以及对话气泡生命周期（显示10秒，3秒淡出）。后台线程每500毫秒触发一次滴答，
并使 prompt_toolkit 应用刷新底部工具栏。
"""

from __future__ import annotations

import threading
from typing import Callable

from .sprites import render_sprite, sprite_frame_count
from .types import RARITY_COLORS, RARITY_STARS, Companion, CompanionBones

# 匹配 CompanionSprite.tsx 中的时间常量
TICK_MS = 500
BUBBLE_SHOW = 20  # 滴答次数（10秒）
FADE_WINDOW = 6  # 滴答次数（3秒淡出）
PET_BURST_MS = 2500  # 2.5秒

# 匹配 CompanionSprite.tsx 第23行
IDLE_SEQUENCE = [0, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 2, 0, 0, 0]

# 爱心动画帧 —— 匹配 CompanionSprite.tsx 中的 PET_HEARTS
_H = "\u2764"
PET_HEARTS = [
    f"   {_H}    {_H}   ",
    f"  {_H}  {_H}   {_H}  ",
    f" {_H}   {_H}  {_H}   ",
    f"{_H}  {_H}      {_H} ",
    "\u00b7    \u00b7   \u00b7  ",
]


class CompanionAnimator:
    """管理伙伴精灵的实时动画状态。

    调用 start() 启动 500ms 滴答循环，调用 stop() 停止。
    toolbar_text() 方法返回当前帧的格式化文本，用于 prompt_toolkit 的底部工具栏。
    """

    def __init__(self, companion: Companion):
        self.companion = companion
        self._tick = 0
        self._timer: threading.Timer | None = None
        self._running = False
        self._invalidate: Callable[[], None] | None = None

        # 对话气泡状态
        self._reaction: str | None = None
        self._reaction_tick: int = 0

        # 抚摸状态
        self._pet_tick: int | None = None

        # 用于渲染的骨骼数据
        self._bones = CompanionBones(
            rarity=companion.rarity,
            species=companion.species,
            eye=companion.eye,
            hat=companion.hat,
            shiny=companion.shiny,
            stats=companion.stats,
        )

    def set_invalidate(self, fn: Callable[[], None]) -> None:
        """设置 app.invalidate 回调，用于刷新工具栏。"""
        self._invalidate = fn

    def update_companion(self, companion: Companion) -> None:
        """刷新伙伴引用（例如心情更新后）。"""
        self.companion = companion
        self._bones = CompanionBones(
            rarity=companion.rarity,
            species=companion.species,
            eye=companion.eye,
            hat=companion.hat,
            shiny=companion.shiny,
            stats=companion.stats,
        )

    def start(self) -> None:
        self._running = True
        self._schedule_tick()

    def stop(self) -> None:
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def set_reaction(self, text: str) -> None:
        """设置对话气泡反应（来自观察者）。"""
        self._reaction = text
        self._reaction_tick = self._tick

    def clear_reaction(self) -> None:
        self._reaction = None

    def pet(self) -> None:
        """触发抚摸/爱心动画。"""
        self._pet_tick = self._tick

    # -- 渲染 ----------------------------------------------------------

    def toolbar_text(self) -> list[tuple[str, str]]:
        """返回用于 prompt_toolkit 底部工具栏的格式化文本元组列表。

        返回 (样式, 文本) 元组的列表。
        """
        comp = self.companion
        color = RARITY_COLORS.get(comp.rarity, "dim")
        stars = RARITY_STARS.get(comp.rarity, "\u2605")

        # 确定动画状态
        petting = (
            self._pet_tick is not None
            and (self._tick - self._pet_tick) * TICK_MS < PET_BURST_MS
        )
        speaking = self._reaction is not None
        excited = petting or speaking

        # 确定精灵帧
        frame_count = sprite_frame_count(comp.species)
        if excited:
            sprite_frame = self._tick % frame_count
            blink = False
        else:
            step = IDLE_SEQUENCE[self._tick % len(IDLE_SEQUENCE)]
            if step == -1:
                sprite_frame = 0
                blink = True
            else:
                sprite_frame = step
                blink = False

        # 渲染精灵行
        lines = render_sprite(self._bones, sprite_frame)
        if blink:
            lines = [line.replace(comp.eye, "-") for line in lines]

        # 抚摸时的心形叠加层
        heart_line = None
        if petting and self._pet_tick is not None:
            pet_age = self._tick - self._pet_tick
            heart_line = PET_HEARTS[pet_age % len(PET_HEARTS)]

        # 对话气泡
        bubble_lines: list[str] = []
        bubble_fading = False
        if self._reaction:
            age = self._tick - self._reaction_tick
            if age >= BUBBLE_SHOW:
                self._reaction = None
            else:
                bubble_fading = age >= BUBBLE_SHOW - FADE_WINDOW
                bubble_lines = self._wrap_bubble(self._reaction, bubble_fading)

        # 组合输出：左侧精灵，右侧气泡
        result: list[tuple[str, str]] = []

        shiny_tag = " \u2728" if comp.shiny else ""
        dominant = comp.mood.dominant().lower()
        name_line = f" {comp.name} the {comp.species} {stars}{shiny_tag}  [{dominant}]"

        # 组装精灵行（可选的心形在顶部）
        sprite_lines_full = []
        if heart_line:
            sprite_lines_full.append(heart_line)
        sprite_lines_full.extend(lines)

        max_sw = max((len(l) for l in sprite_lines_full), default=12)

        # 样式名称
        s_sprite = f"fg:{_rich_to_ansi(color)}"
        s_heart = "fg:red bold"
        s_bubble = (
            "fg:gray italic" if bubble_fading else f"fg:{_rich_to_ansi(color)} italic"
        )

        total_rows = max(len(sprite_lines_full), len(bubble_lines))
        for i in range(total_rows):
            # 精灵部分
            if i < len(sprite_lines_full):
                sl = sprite_lines_full[i].ljust(max_sw)
                st = s_heart if (heart_line and i == 0) else s_sprite
                result.append((st, sl))
            else:
                result.append(("", " " * max_sw))

            # 气泡部分
            if i < len(bubble_lines):
                result.append(("", "  "))
                result.append((s_bubble, bubble_lines[i]))

            result.append(("", "\n"))

        # 名称行
        result.append((s_sprite, name_line))

        return result

    def _wrap_bubble(self, text: str, fading: bool) -> list[str]:
        """将文本换行并加上边框，生成对话气泡行。"""
        max_w = 30
        words = text.split()
        wrapped: list[str] = []
        current = ""
        for word in words:
            if current and len(current) + 1 + len(word) > max_w:
                wrapped.append(current)
                current = word
            else:
                current = f"{current} {word}".strip() if current else word
        if current:
            wrapped.append(current)
        if not wrapped:
            return []

        width = max(len(l) for l in wrapped)
        border = "\u256d" + "\u2500" * (width + 2) + "\u256e"  # 圆角边框
        bottom = "\u2570" + "\u2500" * (width + 2) + "\u256f"
        lines = [border]
        for l in wrapped:
            lines.append(f"\u2502 {l:<{width}} \u2502")
        lines.append(bottom)
        return lines

    def _schedule_tick(self) -> None:
        if not self._running:
            return
        self._tick += 1
        if self._invalidate:
            try:
                self._invalidate()
            except Exception:
                pass
        self._timer = threading.Timer(TICK_MS / 1000, self._schedule_tick)
        self._timer.daemon = True
        self._timer.start()


def _rich_to_ansi(color: str) -> str:
    """将 rich 样式名称映射为 prompt_toolkit 的 ANSI 颜色名称。"""
    mapping = {
        "dim": "gray",
        "green": "ansigreen",
        "blue": "ansiblue",
        "magenta": "ansimagenta",
        "yellow": "ansiyellow",
        "red": "ansired",
    }
    return mapping.get(color, color)
