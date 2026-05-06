"""基于用户ID的确定性伴侣生成。

关键不变性：同一userId始终产生相同的CompanionBones。
骨骼（Bones）永远不会被持久化——每次读取时都会根据hash(userId)重新生成，
因此物种重命名不会破坏已存储的伴侣，用户也无法通过编辑获得传说级伴侣。
"""

from __future__ import annotations

import getpass
import socket
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Sequence

from .types import (ALL_SPECIES, BONUS_SPECIES, EYES, HATS, RARITIES,
                    RARITY_FLOOR, RARITY_WEIGHTS, SPECIES, STAT_NAMES,
                    Companion, CompanionBones, CompanionMood)

_MASK = 0xFFFFFFFF  # 32位无符号掩码


# ---------------------------------------------------------------------------
# Mulberry32 —— 微型种子伪随机数生成器，对选鸭子来说足够好
# 精确移植 companion.ts 第16-25行
# ---------------------------------------------------------------------------


def mulberry32(seed: int) -> Callable[[], float]:
    a = seed & _MASK

    def _next() -> float:
        nonlocal a
        a = (a | 0) & _MASK
        a = (a + 0x6D2B79F5) & _MASK
        t = ((a ^ (a >> 15)) * (1 | a)) & _MASK
        t = (t + (((t ^ (t >> 7)) * (61 | t)) & _MASK)) & _MASK
        return ((t ^ (t >> 14)) & _MASK) / 4294967296

    return _next


# ---------------------------------------------------------------------------
# FNV-1a 哈希（companion.ts 第27-37行的非 Bun 分支）
# ---------------------------------------------------------------------------


def hash_string(s: str) -> int:
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        # 模拟 Math.imul：相乘后掩码为32位有符号，再转无符号
        h = (h * 16777619) & _MASK
    return h & _MASK


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def pick(rng: Callable[[], float], arr: Sequence) -> object:
    return arr[int(rng() * len(arr))]


def roll_rarity(rng: Callable[[], float]) -> str:
    total = sum(RARITY_WEIGHTS.values())
    r = rng() * total
    for rarity in RARITIES:
        r -= RARITY_WEIGHTS[rarity]
        if r < 0:
            return rarity
    return "common"


def roll_stats(rng: Callable[[], float], rarity: str) -> dict[str, int]:
    """一个峰值属性，一个低谷属性，其余随机分布。稀有度提升属性下限。"""
    floor = RARITY_FLOOR[rarity]
    peak = pick(rng, STAT_NAMES)
    dump = pick(rng, STAT_NAMES)
    while dump == peak:
        dump = pick(rng, STAT_NAMES)

    stats: dict[str, int] = {}
    for name in STAT_NAMES:
        if name == peak:
            stats[name] = min(100, floor + 50 + int(rng() * 30))
        elif name == dump:
            stats[name] = max(1, floor - 10 + int(rng() * 15))
        else:
            stats[name] = floor + int(rng() * 40)
    return stats


# ---------------------------------------------------------------------------
# 掷骰
# ---------------------------------------------------------------------------

SALT = "friend-2026-401"


@dataclass(frozen=True)
class Roll:
    bones: CompanionBones
    inspiration_seed: int


def _roll_from(rng: Callable[[], float], species_pool: Sequence = SPECIES) -> Roll:
    rarity = roll_rarity(rng)
    bones = CompanionBones(
        rarity=rarity,
        species=pick(rng, species_pool),
        eye=pick(rng, EYES),
        hat="none" if rarity == "common" else pick(rng, HATS),
        shiny=rng() < 0.01,
        stats=roll_stats(rng, rarity),
    )
    return Roll(bones=bones, inspiration_seed=int(rng() * 1e9))


@lru_cache(maxsize=1)
def roll(user_id: str) -> Roll:
    key = user_id + SALT
    pool = ALL_SPECIES if any(b in user_id.lower() for b in BONUS_SPECIES) else SPECIES
    return _roll_from(mulberry32(hash_string(key)), species_pool=pool)


def roll_with_seed(seed: str) -> Roll:
    pool = ALL_SPECIES if any(b in seed.lower() for b in BONUS_SPECIES) else SPECIES
    return _roll_from(mulberry32(hash_string(seed)), species_pool=pool)


# ---------------------------------------------------------------------------
# 用户身份
# ---------------------------------------------------------------------------


def companion_user_id() -> str:
    """为伴侣生成推导出一个稳定的用户身份标识。

    由于 cc-nano 没有 OAuth，使用 username@hostname 作为种子。
    同一台机器上的同一用户总是得到相同的伴侣。

    设置环境变量 CC_NANO_BUDDY_SEED 可覆盖（用于测试）。
    """
    import os

    override = os.environ.get("CC_NANO_BUDDY_SEED")
    if override:
        return override
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except Exception:
        return "anon"


# ---------------------------------------------------------------------------
# 获取伴侣（合并存储的“灵魂”和重新生成的骨骼）
# ---------------------------------------------------------------------------


def _companion_from_stored(
    stored_name: str,
    stored_personality: str,
    stored_hatched_at: int,
    seed: str,
    mood: CompanionMood | None = None,
) -> Companion:
    """通过从种子重新生成骨骼来构建完整的 Companion 对象。"""
    bones = roll_with_seed(seed).bones
    return Companion(
        rarity=bones.rarity,
        species=bones.species,
        eye=bones.eye,
        hat=bones.hat,
        shiny=bones.shiny,
        stats=bones.stats,
        name=stored_name,
        personality=stored_personality,
        hatched_at=stored_hatched_at,
        mood=mood or CompanionMood(),
    )


def get_companion() -> Companion | None:
    """获取当前已孵化的活跃伴侣（若已孵化），否则返回 None。"""
    from .storage import (load_active_mood, load_active_seed,
                          load_stored_companion)

    stored = load_stored_companion()
    if stored is None:
        return None
    seed = load_active_seed()
    if not seed:
        # 对旧数据的回退处理
        seed = companion_user_id() + SALT
    mood = load_active_mood()
    return _companion_from_stored(
        stored.name,
        stored.personality,
        stored.hatched_at,
        seed,
        mood,
    )


def get_all_companions() -> list[Companion]:
    """获取所有已孵化的伴侣（从每个种子重新生成骨骼）。"""
    from .storage import load_all_stored_companions

    result = []
    for sc in load_all_stored_companions():
        seed = sc.seed or (companion_user_id() + SALT)
        result.append(
            _companion_from_stored(
                sc.name,
                sc.personality,
                sc.hatched_at,
                seed,
            )
        )
    return result
