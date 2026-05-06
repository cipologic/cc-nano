"""内存中的游戏会话状态 — 模块级单例。

会话的生命周期仅与终端进程相同。
"""

from __future__ import annotations

from .types import INITIAL_STATS, Badge, GameSession, Item, Skill

_current_session: GameSession | None = None


def new_session(
    companion_name: str,
    companion_species: str,
    companion_eye: str,
    companion_hat: str,
) -> GameSession:
    """创建一个全新的游戏会话。"""
    global _current_session
    _current_session = GameSession(
        companion_name=companion_name,
        companion_species=companion_species,
        companion_eye=companion_eye,
        companion_hat=companion_hat,
        stats=dict(INITIAL_STATS),
    )
    return _current_session


def get_session() -> GameSession | None:
    return _current_session


def end_session() -> GameSession | None:
    """将会话标记为非活跃状态，并返回以供持久化。"""
    global _current_session
    s = _current_session
    if s:
        s.active = False
    _current_session = None
    return s


# ---------------------------------------------------------------------------
# 便捷修改函数
# ---------------------------------------------------------------------------


def apply_stat_change(stat: str, amount: int) -> int:
    """应用属性变化。返回新值。HP 下限为 0。"""
    s = _current_session
    if not s or stat not in s.stats:
        return 0
    s.stats[stat] = max(0, s.stats[stat] + amount)
    return s.stats[stat]


def add_item(item: Item) -> None:
    if _current_session:
        _current_session.inventory.append(item)


def remove_random_item() -> Item | None:
    s = _current_session
    if not s or not s.inventory:
        return None
    import random

    item = random.choice(s.inventory)
    s.inventory.remove(item)
    return item


def add_skill(skill: Skill) -> None:
    if _current_session:
        _current_session.skills.append(skill)


def remove_random_skill() -> Skill | None:
    s = _current_session
    if not s or not s.skills:
        return None
    import random

    skill = random.choice(s.skills)
    s.skills.remove(skill)
    return skill


def add_badge(badge: Badge) -> None:
    if _current_session:
        _current_session.badges.append(badge)


def add_tickets(amount: int) -> None:
    if _current_session:
        _current_session.tickets += amount


def spend_tickets(amount: int) -> bool:
    s = _current_session
    if not s or s.tickets < amount:
        return False
    s.tickets -= amount
    return True


def append_log(entry: str) -> None:
    if _current_session:
        _current_session.adventure_log.append(entry)


def is_alive() -> bool:
    s = _current_session
    return s is not None and s.stats.get("HP", 0) > 0
