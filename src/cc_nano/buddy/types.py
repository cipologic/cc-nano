"""伙伴类型定义和常量。"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 稀有度
# ---------------------------------------------------------------------------

RARITIES = ("common", "uncommon", "rare", "epic", "legendary")

RARITY_WEIGHTS: dict[str, int] = {
    "common": 60,
    "uncommon": 25,
    "rare": 10,
    "epic": 4,
    "legendary": 1,
}

RARITY_STARS: dict[str, str] = {
    "common": "\u2605",
    "uncommon": "\u2605\u2605",
    "rare": "\u2605\u2605\u2605",
    "epic": "\u2605\u2605\u2605\u2605",
    "legendary": "\u2605\u2605\u2605\u2605\u2605",
}

# 映射到富文本样式名称（原始代码使用主题键）
RARITY_COLORS: dict[str, str] = {
    "common": "dim",
    "uncommon": "green",
    "rare": "blue",
    "epic": "magenta",
    "legendary": "yellow",
}

RARITY_FLOOR: dict[str, int] = {
    "common": 5,
    "uncommon": 15,
    "rare": 25,
    "epic": 35,
    "legendary": 50,
}

# ---------------------------------------------------------------------------
# 物种
# ---------------------------------------------------------------------------

SPECIES = (
    "duck",
    "goose",
    "blob",
    "cat",
    "dragon",
    "octopus",
    "owl",
    "penguin",
    "turtle",
    "snail",
    "ghost",
    "axolotl",
    "capybara",
    "cactus",
    "robot",
    "rabbit",
    "mushroom",
    "chonk",
)

# 奖励物种 — 仅通过 CC_NANO_BUDDY_SEED 获得，不在随机池中
BONUS_SPECIES = ("pikachu",)
ALL_SPECIES = SPECIES + BONUS_SPECIES

# ---------------------------------------------------------------------------
# 外观
# ---------------------------------------------------------------------------

EYES = ("\u00b7", "\u2726", "\u00d7", "\u25c9", "@", "\u00b0")
# ·  ✦  ×  ◉  @  °

HATS = ("none", "crown", "tophat", "propeller", "halo", "wizard", "beanie", "tinyduck")

# ---------------------------------------------------------------------------
# 属性
# ---------------------------------------------------------------------------

STAT_NAMES = ("DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK")

# ---------------------------------------------------------------------------
# 情绪
# ---------------------------------------------------------------------------

MOOD_DIMENSIONS = ("happy", "bored", "excited", "tired", "grumpy", "curious")
MOOD_NEUTRAL = 50

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanionBones:
    """确定性部分 — 由用户ID哈希派生。"""

    rarity: str
    species: str
    eye: str
    hat: str
    shiny: bool
    stats: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CompanionMood:
    """动态情绪状态 — 基于对话事件变化。"""

    happy: int = MOOD_NEUTRAL
    bored: int = MOOD_NEUTRAL
    excited: int = MOOD_NEUTRAL
    tired: int = MOOD_NEUTRAL
    grumpy: int = MOOD_NEUTRAL
    curious: int = MOOD_NEUTRAL
    last_updated: int = 0  # 自纪元起的毫秒数

    def to_dict(self) -> dict:
        return {
            "happy": self.happy,
            "bored": self.bored,
            "excited": self.excited,
            "tired": self.tired,
            "grumpy": self.grumpy,
            "curious": self.curious,
            "lastUpdated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CompanionMood":
        return cls(
            happy=d.get("happy", MOOD_NEUTRAL),
            bored=d.get("bored", MOOD_NEUTRAL),
            excited=d.get("excited", MOOD_NEUTRAL),
            tired=d.get("tired", MOOD_NEUTRAL),
            grumpy=d.get("grumpy", MOOD_NEUTRAL),
            curious=d.get("curious", MOOD_NEUTRAL),
            last_updated=d.get("lastUpdated", 0),
        )

    def dominant(self) -> str:
        """返回偏离中性最远的情绪维度。"""
        best_dim = "happy"
        best_dist = 0
        for dim in MOOD_DIMENSIONS:
            dist = abs(getattr(self, dim) - MOOD_NEUTRAL)
            if dist > best_dist:
                best_dist = dist
                best_dim = dim
        return best_dim


@dataclass(frozen=True)
class CompanionSoul:
    """模型生成的灵魂 — 首次孵化后存储在配置中。"""

    name: str
    personality: str


@dataclass(frozen=True)
class StoredCompanion:
    """实际保存在磁盘上的数据。"""

    name: str
    personality: str
    hatched_at: int  # 自纪元起的毫秒数


@dataclass(frozen=True)
class StoredCompanionWithSeed(StoredCompanion):
    """保存的伙伴，同时记住用于生成骨骼的种子。"""

    seed: str = ""


@dataclass(frozen=True)
class Companion:
    """完整伙伴 = 骨骼 + 灵魂 + 元数据。"""

    # 骨骼
    rarity: str
    species: str
    eye: str
    hat: str
    shiny: bool
    stats: dict[str, int]
    # 灵魂
    name: str
    personality: str
    # 元数据
    hatched_at: int
    # 情绪
    mood: CompanionMood = field(default_factory=CompanionMood)
