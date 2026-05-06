"""挂机冒险持久化 — 在会话间保存/加载门票和徽章。

只有门票和徽章会在多次运行间持久化。其他所有内容都会重置。
存储位置：~/.config/cc-nano/companion_loot.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from cc_nano.core.project import get_project_root

from .badges import ALL_BADGES
from .types import GAME_STAT_NAMES, GameSession


def _get_loot_dir() -> Path:
    return get_project_root() / ".config" / "cc-nano"


def _get_loot_file() -> Path:
    return _get_loot_dir() / "companion_loot.json"


# 用于解析徽章效果的正则表达式，例如 "HP+5"、"ATK+3,DEF+3"、"全属性+3"
_EFFECT_RE = re.compile(r"(HP|ATK|DEF|SPD|LCK|全属性)\+(\d+)")


def load_loot() -> dict:
    """从磁盘加载持久化的数据。"""
    if not _get_loot_file().exists():
        return {"tickets": 0, "badges": [], "total_runs": 0}
    try:
        data = json.loads(_get_loot_file().read_text(encoding="utf-8"))
        data.setdefault("tickets", 0)
        data.setdefault("badges", [])
        data.setdefault("total_runs", 0)
        return data
    except (json.JSONDecodeError, TypeError):
        return {"tickets": 0, "badges": [], "total_runs": 0}


def save_loot(loot: dict) -> None:
    """将数据保存到磁盘。"""
    _get_loot_dir().mkdir(parents=True, exist_ok=True)
    _get_loot_file().write_text(
        json.dumps(loot, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def save_session(session: GameSession) -> None:
    """在会话结束时保存门票和徽章。其他所有内容均被丢弃。"""
    loot = load_loot()
    loot["tickets"] = session.tickets
    loot["badges"] = [b.badge_id for b in session.badges]
    loot["total_runs"] = loot.get("total_runs", 0) + 1
    save_loot(loot)


def restore_from_loot(session: GameSession) -> None:
    """恢复已存的门票、拥有的徽章以及徽章带来的属性加成。"""
    loot = load_loot()
    session.tickets = loot.get("tickets", 0)
    for badge_id in loot.get("badges", []):
        badge = ALL_BADGES.get(badge_id)
        if badge and badge_id not in {b.badge_id for b in session.badges}:
            session.badges.append(badge)

    # 将所有徽章的被动效果应用到初始属性上
    for badge in session.badges:
        for stat, amount in _parse_effect(badge.effect):
            if stat in session.stats:
                session.stats[stat] += amount


def _parse_effect(effect: str) -> list[tuple[str, int]]:
    """将徽章效果字符串解析为 [(属性, 数值), ...] 的列表。

    示例：
      "HP+5"          → [("HP", 5)]
      "ATK+3,DEF+3"   → [("ATK", 3), ("DEF", 3)]
      "全属性+3"       → [("HP",3),("ATK",3),("DEF",3),("SPD",3),("LCK",3)]
    """
    results = []
    for match in _EFFECT_RE.finditer(effect):
        stat_name = match.group(1)
        amount = int(match.group(2))
        if stat_name == "全属性":
            for s in GAME_STAT_NAMES:
                results.append((s, amount))
        else:
            results.append((stat_name, amount))
    return results
