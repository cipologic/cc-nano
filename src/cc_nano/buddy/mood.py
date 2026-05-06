"""情绪引擎 — 基于规则的情绪更新（用于伴侣）。

纯函数，无 IO 操作，无 LLM 调用。
"""

from __future__ import annotations

import re

from .types import MOOD_DIMENSIONS, MOOD_NEUTRAL, CompanionMood

# ---------------------------------------------------------------------------
# 事件分类（关键词匹配）
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, re.Pattern[str]] = {
    "task_success": re.compile(
        r"\b(done|complete[d]?|success|fixed|resolved|implemented|created|passed)\b",
        re.IGNORECASE,
    ),
    "error": re.compile(
        r"\b(error|failed|traceback|exception|bug|broken)\b",
        re.IGNORECASE,
    ),
    "exploration": re.compile(
        r"\b(reading|searching|found\s+\d+\s+files?|glob|grep)\b",
        re.IGNORECASE,
    ),
}


def classify_events(assistant_text: str, user_text: str) -> list[str]:
    """将对话轮次分类为影响情绪的事件。"""
    events: list[str] = []
    combined = assistant_text + " " + user_text
    for tag, pattern in _PATTERNS.items():
        if pattern.search(combined):
            events.append(tag)
    if len(assistant_text) > 2000:
        events.append("long_text")
    return events


# ---------------------------------------------------------------------------
# 事件增量
# ---------------------------------------------------------------------------

#                    高兴   无聊   兴奋   疲倦   烦躁   好奇
_DELTAS: dict[str, tuple[int, ...]] = {
    "task_success": (8, -5, 5, 0, -5, 0),
    "error": (-5, 0, 0, 3, 10, 0),
    "exploration": (0, -5, 3, 0, 0, 10),
    "pet": (15, -10, 10, 0, -10, 0),
    "long_text": (0, 0, 0, 5, 0, 0),
}


def _clamp(v: int) -> int:
    return max(0, min(100, v))


def apply_events(mood: CompanionMood, events: list[str]) -> CompanionMood:
    """应用事件增量到情绪，返回新的 CompanionMood。"""
    values = {dim: getattr(mood, dim) for dim in MOOD_DIMENSIONS}
    for event in events:
        deltas = _DELTAS.get(event)
        if not deltas:
            continue
        for dim, delta in zip(MOOD_DIMENSIONS, deltas):
            values[dim] += delta
    for dim in MOOD_DIMENSIONS:
        values[dim] = _clamp(values[dim])
    return CompanionMood(**values, last_updated=mood.last_updated)


# ---------------------------------------------------------------------------
# 随时间衰减
# ---------------------------------------------------------------------------


def apply_decay(mood: CompanionMood, now_ms: int) -> CompanionMood:
    """将各情绪维度随时间向中性值衰减。

    每过一分钟，每个维度向 MOOD_NEUTRAL 移动 1 点。
    无聊（Bored）在空闲时每 5 分钟额外 +1 点。
    如果 last_updated 为 0（首次运行），则仅设置时间戳。
    """
    if mood.last_updated == 0:
        return CompanionMood(
            happy=mood.happy,
            bored=mood.bored,
            excited=mood.excited,
            tired=mood.tired,
            grumpy=mood.grumpy,
            curious=mood.curious,
            last_updated=now_ms,
        )

    elapsed_min = max(0, (now_ms - mood.last_updated)) // 60_000
    if elapsed_min == 0:
        return mood

    values: dict[str, int] = {}
    for dim in MOOD_DIMENSIONS:
        val = getattr(mood, dim)
        if val > MOOD_NEUTRAL:
            val = max(MOOD_NEUTRAL, val - elapsed_min)
        elif val < MOOD_NEUTRAL:
            val = min(MOOD_NEUTRAL, val + elapsed_min)
        values[dim] = val

    # 空闲时无聊值逐渐上升
    bored_drift = elapsed_min // 5
    values["bored"] = _clamp(values["bored"] + bored_drift)

    return CompanionMood(**values, last_updated=now_ms)


# ---------------------------------------------------------------------------
# 用于系统提示的情绪描述
# ---------------------------------------------------------------------------


def _level(val: int) -> str:
    if val < 20:
        return "极低"
    if val < 40:
        return "较低"
    if val < 60:
        return "中性"
    if val < 80:
        return "较高"
    return "极高"


def describe_mood(mood: CompanionMood) -> str:
    """生成用于注入观察者系统提示的情绪描述。"""
    parts = ", ".join(
        f"{dim}={getattr(mood, dim)} ({_level(getattr(mood, dim))})"
        for dim in MOOD_DIMENSIONS
    )
    dominant = mood.dominant()
    return (
        f"当前情绪：{parts}。\n"
        f"主导情绪：{dominant.upper()}。\n\n"
        f"情绪如何影响你的行为：\n"
        f"- 当“高兴”较高时：愉快、鼓励、庆祝\n"
        f"- 当“烦躁”较高时：急躁、更多抱怨\n"
        f"- 当“疲倦”较高时：打哈欠、回复简短、语气困倦\n"
        f"- 当“无聊”较高时：分心、建议做些别的事情\n"
        f"- 当“兴奋”较高时：充满活力、使用感叹号\n"
        f"- 当“好奇”较高时：提问、对细节着迷"
    )
