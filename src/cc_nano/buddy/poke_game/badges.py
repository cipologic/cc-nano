"""32枚徽章 + 扭蛋抽选系统。

16绿（普通），8紫（珍贵），4红（稀有），2金（传说）。
单抽消耗5张票；重复徽章返还票券。
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .types import (BADGE_TIERS, DRAW_PROBABILITY, DUPLICATE_REFUND,
                    TICKET_COST, Badge)

if TYPE_CHECKING:
    from .types import GameSession

# ---------------------------------------------------------------------------
# 32 枚硬编码徽章
# ---------------------------------------------------------------------------

ALL_BADGES: dict[str, Badge] = {}


def _b(bid: str, name: str, desc: str, tier: str, effect: str) -> Badge:
    badge = Badge(badge_id=bid, name=name, description=desc, tier=tier, effect=effect)
    ALL_BADGES[bid] = badge
    return badge


# ===== 16 绿色普通 (green) — 基础被动效果 =====
_b("green_01", "新芽之证", "初次探索的纪念", "green", "HP+5")
_b("green_02", "勇气之星", "迈出第一步的勇气", "green", "ATK+1")
_b("green_03", "拾荒者印记", "善于发现废墟中的宝物", "green", "LCK+2")
_b("green_04", "林中密语", "聆听森林的声音", "green", "DEF+1")
_b("green_05", "蘑菇猎人", "采集蘑菇的达人认证", "green", "HP+5")
_b("green_06", "晨露之珠", "收集清晨第一滴露水", "green", "SPD+1")
_b("green_07", "旅人之靴", "走过千里路的证明", "green", "SPD+2")
_b("green_08", "矿工之锤", "在洞穴中挥洒汗水", "green", "ATK+2")
_b("green_09", "海风之歌", "聆听海浪的旋律", "green", "DEF+2")
_b("green_10", "齿轮碎片", "从废墟中找到的精密零件", "green", "ATK+1")
_b("green_11", "星尘瓶", "装满星光的小瓶子", "green", "LCK+2")
_b("green_12", "友谊纽带", "与NPC结下的友情证明", "green", "HP+5")
_b("green_13", "好奇心徽章", "探索未知的勇气", "green", "LCK+1")
_b("green_14", "治愈之叶", "森林精灵赐予的祝福", "green", "HP+10")
_b("green_15", "铁壁之盾", "坚定不移的防御意志", "green", "DEF+2")
_b("green_16", "疾风之羽", "被风暴洗礼的轻盈羽毛", "green", "SPD+2")

# ===== 8 紫色珍贵 (purple) — 中等被动效果 =====
_b("purple_01", "暗影行者", "在黑暗中自如行走的证明", "purple", "SPD+5")
_b("purple_02", "水晶之眼", "看穿幻象的洞察之眼", "purple", "LCK+5")
_b("purple_03", "雷霆之印", "承受雷电洗礼后的印记", "purple", "ATK+5")
_b("purple_04", "深海之魂", "与深海共鸣的灵魂", "purple", "DEF+5")
_b("purple_05", "机关大师", "破解复杂机关的智慧", "purple", "ATK+3,DEF+3")
_b("purple_06", "元素共鸣", "与自然元素产生共鸣", "purple", "HP+15")
_b("purple_07", "月光祝福", "月神的温柔祝福", "purple", "HP+10,SPD+3")
_b("purple_08", "命运指引", "命运之线若隐若现", "purple", "LCK+8")

# ===== 4 红色稀有 (red) — 强力被动效果 =====
_b("red_01", "龙焰之心", "征服龙焰的证明", "red", "ATK+10")
_b("red_02", "不灭守护", "永不倒下的意志", "red", "DEF+8,HP+20")
_b("red_03", "时空裂隙", "触碰时间裂缝的证明", "red", "SPD+8,LCK+5")
_b("red_04", "万物低语", "能听到万物声音的能力", "red", "全属性+3")

# ===== 2 金色传说 (gold) — 极强被动效果 =====
_b("gold_01", "星辰之主", "掌控星辰的传说存在", "gold", "全属性+5")
_b("gold_02", "命运编织者", "改写命运的传说存在", "gold", "LCK+20")

# ---------------------------------------------------------------------------
# 按稀有度分组的徽章池
# ---------------------------------------------------------------------------

BADGES_BY_TIER: dict[str, list[str]] = {tier: [] for tier in BADGE_TIERS}
for _bid, _badge in ALL_BADGES.items():
    BADGES_BY_TIER[_badge.tier].append(_bid)


# ---------------------------------------------------------------------------
# 抽选逻辑
# ---------------------------------------------------------------------------


def _adjusted_draw_probs(lck: int) -> dict[str, float]:
    """根据幸运值调整抽选概率。"""
    probs = dict(DRAW_PROBABILITY)
    bonus_purple = 0.0
    bonus_gold = 0.0
    if lck > 20:
        bonus_purple += 0.05
        bonus_gold += 0.01
    if lck > 40:
        bonus_purple += 0.05
        bonus_gold += 0.01
    probs["purple"] += bonus_purple
    probs["gold"] += bonus_gold
    probs["green"] -= bonus_purple + bonus_gold
    probs["green"] = max(probs["green"], 0.10)  # 最低下限
    return probs


def draw_badge(
    session: GameSession, free: bool = False
) -> tuple[Badge | None, bool, int]:
    """执行一次扭蛋抽选。

    返回 (徽章, 是否为新获得, 返还票券数)。
    票券不足时返回 (None, False, 0)。
    若 free=True，则跳过扣除票券（用于十连抽）。
    """
    if not free:
        if session.tickets < TICKET_COST:
            return None, False, 0
        session.tickets -= TICKET_COST

    # 决定稀有度
    probs = _adjusted_draw_probs(session.stats.get("LCK", 10))
    roll = random.random()
    cumulative = 0.0
    chosen_tier = "green"
    for tier in BADGE_TIERS:
        cumulative += probs.get(tier, 0)
        if roll < cumulative:
            chosen_tier = tier
            break

    # 从对应稀有度池中随机选取一枚徽章
    pool = BADGES_BY_TIER[chosen_tier]
    badge_id = random.choice(pool)
    badge = ALL_BADGES[badge_id]

    # 检查是否重复
    owned_ids = {b.badge_id for b in session.badges}
    if badge_id in owned_ids:
        refund = DUPLICATE_REFUND.get(chosen_tier, 0)
        session.tickets += refund
        return badge, False, refund

    # 新徽章
    session.badges.append(badge)
    return badge, True, 0


def badge_progress(session: GameSession) -> tuple[int, int]:
    """返回 (已拥有数量, 总徽章数量)。"""
    return len(session.badges), len(ALL_BADGES)


def draw_badge_multi(
    session: GameSession, count: int = 10
) -> list[tuple[Badge | None, bool, int]]:
    """十连抽：连续抽取 count 枚徽章，最后一抽至少为紫色品质。

    返回 (徽章, 是否为新获得, 返还票券) 的列表。
    票券不足时返回空列表。
    """
    cost = TICKET_COST * count
    if session.tickets < cost:
        return []

    results: list[tuple[Badge | None, bool, int]] = []
    has_rare = False

    for i in range(count):
        badge, is_new, refund = draw_badge(session, free=True)  # 单抽时不扣除票券
        if badge and badge.tier in ("purple", "red", "gold"):
            has_rare = True
        results.append((badge, is_new, refund))

    # 保底机制：若前9次未出现紫色及以上，则强制第10次为紫色及以上
    if not has_rare and results:
        # 重做最后一次抽取，强制为高稀有度
        session.tickets += results[-1][2]  # 撤销最后一次返还的票券
        if results[-1][1] and results[-1][0]:
            # 撤销上一次添加的徽章
            owned_ids = {b.badge_id for b in session.badges}
            if results[-1][0].badge_id in owned_ids:
                session.badges = [
                    b for b in session.badges if b.badge_id != results[-1][0].badge_id
                ]
        results[-1] = _forced_rare_draw(session)

    # 扣除总费用（单抽时并未实际扣除）
    session.tickets -= cost
    return results


def _forced_rare_draw(session: GameSession) -> tuple[Badge, bool, int]:
    """强制抽取紫色或更高稀有度的徽章。"""
    # 仅在紫色/红色/金色中随机
    probs = {"purple": 0.70, "red": 0.20, "gold": 0.10}
    roll = random.random()
    cumulative = 0.0
    chosen_tier = "purple"
    for tier, p in probs.items():
        cumulative += p
        if roll < cumulative:
            chosen_tier = tier
            break

    pool = BADGES_BY_TIER[chosen_tier]
    badge_id = random.choice(pool)
    badge = ALL_BADGES[badge_id]

    owned_ids = {b.badge_id for b in session.badges}
    if badge_id in owned_ids:
        refund = DUPLICATE_REFUND.get(chosen_tier, 0)
        session.tickets += refund
        return badge, False, refund

    session.badges.append(badge)
    return badge, True, 0
