"""放置冒险（IA）类型定义与常量。

所有游戏数据类型、RPG属性及概率表。
游戏属性与伙伴原本的 调试/耐心/混沌/智慧/讽刺 属性无关。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 游戏RPG属性（与伙伴原始属性无关）
# ---------------------------------------------------------------------------

GAME_STAT_NAMES = ("HP", "ATK", "DEF", "SPD", "LCK")

INITIAL_STATS: dict[str, int] = {
    "HP": 100,
    "ATK": 10,
    "DEF": 10,
    "SPD": 10,
    "LCK": 10,
}

MAX_STAT_BOOST = 50  # 每项属性的永久增益上限

# ---------------------------------------------------------------------------
# Roguelike 保存概率
# ---------------------------------------------------------------------------

SAVE_PROBABILITY: dict[str, float] = {
    "common": 0.40,
    "uncommon": 0.30,
    "rare": 0.20,
    "epic": 0.10,
    "legendary": 0.05,
}

BATTLE_LOOT_MULTIPLIER = 2.0

# ---------------------------------------------------------------------------
# 门票经济
# ---------------------------------------------------------------------------

TICKET_COST = 5
EXPLORE_TICKETS_MIN = 1
EXPLORE_TICKETS_MAX = 3

# ---------------------------------------------------------------------------
# 徽章等级
# ---------------------------------------------------------------------------

BADGE_TIERS = ("green", "purple", "red", "gold")

DRAW_PROBABILITY: dict[str, float] = {
    "green": 0.60,
    "purple": 0.25,
    "red": 0.10,
    "gold": 0.05,
}

DUPLICATE_REFUND: dict[str, int] = {
    "green": 3,
    "purple": 8,
    "red": 20,
    "gold": 50,
}

BADGE_COLORS: dict[str, str] = {
    "green": "green",
    "purple": "magenta",
    "red": "red",
    "gold": "yellow",
}

# ---------------------------------------------------------------------------
# 元素
# ---------------------------------------------------------------------------

ELEMENTS = ("fire", "water", "earth", "wind", "shadow", "light")

# ---------------------------------------------------------------------------
# 物品稀有度（与伙伴系统共用）
# ---------------------------------------------------------------------------

RARITIES = ("common", "uncommon", "rare", "epic", "legendary")

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class Item:
    name: str
    description: str
    rarity: str  # common/uncommon/rare/epic/legendary
    effect: str  # 例如 "HP+20", "ATK+3"
    from_battle: bool = False


@dataclass
class Skill:
    name: str
    description: str
    power: int  # 1-100
    element: str  # fire/water/earth/wind/shadow/light
    from_battle: bool = False


@dataclass
class Badge:
    badge_id: str  # "green_01" .. "gold_02"
    name: str
    description: str
    tier: str  # green/purple/red/gold
    effect: str  # 被动效果描述


@dataclass
class NPC:
    name: str
    species: str
    personality: str
    disposition: str  # friendly/neutral/hostile
    greeting: str = ""  # 遇到时说的话
    gifts: list[dict] = field(default_factory=list)  # 可能给予的物品列表
    secrets: list[str] = field(default_factory=list)  # 世界观碎片
    # 遭遇结果概率
    gift_chance: float = 0.30  # 给予物品的概率
    ignore_chance: float = 0.30  # 无视你的概率
    secret_chance: float = 0.40  # 透露秘密的概率


@dataclass
class Monster:
    name: str
    species: str
    hp: int
    atk: int
    defense: int  # 'def' 是保留字
    spd: int
    element: str  # fire/water/earth/wind/shadow/light
    level: int  # 1-10，影响奖励
    description: str = ""


@dataclass
class Location:
    name: str
    region: str
    description: str
    connections: list[str] = field(default_factory=list)
    event_weights: dict[str, float] = field(default_factory=dict)
    ticket_bonus: int = 0  # 此地点额外奖励的门票数量（0-2）


@dataclass
class GameSession:
    # 伙伴身份（仅外观，不包含原始属性）
    companion_name: str
    companion_species: str
    companion_eye: str
    companion_hat: str
    # 游戏RPG属性
    stats: dict[str, int] = field(default_factory=lambda: dict(INITIAL_STATS))
    # 当前状态
    location: Location | None = None
    inventory: list[Item] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    badges: list[Badge] = field(default_factory=list)
    tickets: int = 0
    # 日志与历史
    adventure_log: list[str] = field(default_factory=list)
    summary_history: str = ""
    # 计数器
    turn_count: int = 0
    mood: int = 80  # 0-100
    # 标志
    active: bool = True
    # 每个地点的探索次数（用于前3次保证事件）
    explore_counts: dict[str, int] = field(default_factory=dict)
    # 记录离开每个地点后访问过的不同地点集合
    # 用于判断何时重置 explore_counts（需要 >=3 个其他地点）
    locations_since_left: dict[str, set] = field(default_factory=dict)


# 每个地点保证触发事件的探索次数
GUARANTEED_EXPLORE_COUNT = 3
# 保证次数用尽后的事件触发概率
POST_GUARANTEE_EVENT_CHANCE = 0.05
