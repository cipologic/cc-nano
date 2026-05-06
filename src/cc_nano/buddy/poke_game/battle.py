"""自动战斗系统 — 宝可梦风格的回合制自动对战。

战斗完全自动进行。每回合双方都会攻击，属性决定伤害，速度决定谁先出手。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable

from .types import GameSession, Item, Monster, Skill

# ---------------------------------------------------------------------------
# 元素克制关系（简化版石头剪刀布）
# ---------------------------------------------------------------------------

# 元素 → 它所克制的元素列表
ELEMENT_ADVANTAGE: dict[str, list[str]] = {
    "fire": ["earth", "wind"],
    "water": ["fire", "shadow"],
    "earth": ["wind", "light"],
    "wind": ["water", "earth"],
    "shadow": ["light", "earth"],
    "light": ["shadow", "fire"],
}


def _element_multiplier(attacker_elem: str, defender_elem: str) -> float:
    advantages = ELEMENT_ADVANTAGE.get(attacker_elem, [])
    if defender_elem in advantages:
        return 1.3
    # 检查防守方是否有克制攻击方的元素（意味着我方被克制）
    defender_advantages = ELEMENT_ADVANTAGE.get(defender_elem, [])
    if attacker_elem in defender_advantages:
        return 0.7
    return 1.0


# ---------------------------------------------------------------------------
# 伤害计算公式
# ---------------------------------------------------------------------------


def _calc_damage(
    atk: int, defense: int, skill_power: int = 0, elem_mult: float = 1.0
) -> int:
    """简易伤害公式。最低伤害为1。"""
    base = max(1, atk - defense // 2)
    if skill_power > 0:
        base = base + skill_power // 5
    damage = int(base * elem_mult * random.uniform(0.85, 1.15))
    return max(1, damage)


# ---------------------------------------------------------------------------
# 战斗结果
# ---------------------------------------------------------------------------


@dataclass
class BattleResult:
    won: bool
    rounds: int
    hp_lost: int  # 玩家损失的生命值
    log: list[str] = field(default_factory=list)
    # 奖励（仅在胜利时获得）
    reward_item: Item | None = None
    reward_skill: Skill | None = None
    reward_stat: tuple[str, int] | None = None  # (属性名, 增加量)
    reward_tickets: int = 0


# ---------------------------------------------------------------------------
# 奖励池
# ---------------------------------------------------------------------------

_BATTLE_ITEMS: list[dict] = [
    {
        "name": "怪物精华",
        "rarity": "common",
        "effect": "ATK+1",
        "description": "击败怪物后提取的精华",
    },
    {
        "name": "野兽之牙",
        "rarity": "common",
        "effect": "ATK+2",
        "description": "锋利的獠牙",
    },
    {
        "name": "坚韧外壳",
        "rarity": "uncommon",
        "effect": "DEF+2",
        "description": "坚硬的甲壳碎片",
    },
    {
        "name": "元素结晶",
        "rarity": "uncommon",
        "effect": "LCK+2",
        "description": "凝聚的元素之力",
    },
    {
        "name": "战斗回忆",
        "rarity": "rare",
        "effect": "ATK+4",
        "description": "战斗中领悟的力量",
    },
    {
        "name": "守护之心",
        "rarity": "rare",
        "effect": "DEF+4",
        "description": "守护同伴的决心结晶",
    },
    {
        "name": "生命之泉",
        "rarity": "epic",
        "effect": "HP+30",
        "description": "蕴含强大生命力的泉水",
    },
]

_BATTLE_SKILLS: list[dict] = [
    {"name": "猛击", "power": 20, "element": "earth", "description": "全力一击"},
    {"name": "疾风突刺", "power": 25, "element": "wind", "description": "极速突进攻击"},
    {"name": "火焰吐息", "power": 30, "element": "fire", "description": "喷射灼热火焰"},
    {
        "name": "暗影利爪",
        "power": 30,
        "element": "shadow",
        "description": "暗影化的锐利爪击",
    },
    {
        "name": "圣光冲击",
        "power": 35,
        "element": "light",
        "description": "凝聚圣光的冲击波",
    },
    {
        "name": "海啸之力",
        "power": 35,
        "element": "water",
        "description": "召唤海啸的力量",
    },
]


def _generate_rewards(monster: Monster, session: GameSession) -> BattleResult:
    """根据怪物等级生成奖励。"""
    result = BattleResult(won=True, rounds=0, hp_lost=0)

    # 券：基于等级
    result.reward_tickets = random.randint(1, max(1, monster.level // 2))

    # 物品掉落几率：40% + 每级5%
    if random.random() < 0.40 + monster.level * 0.05:
        # 等级越高，物品越好
        if monster.level >= 7:
            pool = [i for i in _BATTLE_ITEMS if i["rarity"] in ("rare", "epic")]
        elif monster.level >= 4:
            pool = [i for i in _BATTLE_ITEMS if i["rarity"] in ("uncommon", "rare")]
        else:
            pool = [i for i in _BATTLE_ITEMS if i["rarity"] in ("common", "uncommon")]
        if pool:
            data = random.choice(pool)
            result.reward_item = Item(
                name=data["name"],
                description=data["description"],
                rarity=data["rarity"],
                effect=data["effect"],
                from_battle=True,
            )

    # 技能掉落几率：15% + 每级3%
    if random.random() < 0.15 + monster.level * 0.03:
        pool = [s for s in _BATTLE_SKILLS if s["power"] <= monster.level * 8]
        if pool:
            data = random.choice(pool)
            result.reward_skill = Skill(
                name=data["name"],
                description=data["description"],
                power=data["power"],
                element=data["element"],
                from_battle=True,
            )

    # 属性加成几率：30%
    if random.random() < 0.30:
        stat = random.choice(["ATK", "DEF", "SPD", "LCK"])
        amount = random.randint(1, max(1, monster.level // 3))
        result.reward_stat = (stat, amount)

    return result


# ---------------------------------------------------------------------------
# 自动战斗引擎
# ---------------------------------------------------------------------------


def run_battle(
    session: GameSession, monster: Monster, log_fn: Callable[[str], None]
) -> BattleResult:
    """运行一场自动回合制战斗。通过 log_fn 输出信息。返回 BattleResult。"""
    name = session.companion_name
    player_hp = session.stats.get("HP", 1)
    player_atk = session.stats.get("ATK", 10)
    player_def = session.stats.get("DEF", 10)
    player_spd = session.stats.get("SPD", 10)

    monster_hp = monster.hp
    monster_max_hp = monster.hp

    player_elem = "light"
    if session.skills:
        best_skill = max(session.skills, key=lambda s: s.power)
        player_elem = best_skill.element

    elem_mult_player = _element_multiplier(player_elem, monster.element)
    elem_mult_monster = _element_multiplier(monster.element, player_elem)
    skill_power = max((s.power for s in session.skills), default=0)

    log: list[str] = []
    round_num = 0
    start_hp = player_hp

    log_fn(
        f"⚔️  野生的 [bold red]{monster.name}[/bold red] 出现了！"
        f"(Lv.{monster.level} {monster.element}元素  HP:{monster.hp})"
    )
    time.sleep(0.4)

    while player_hp > 0 and monster_hp > 0 and round_num < 20:
        round_num += 1

        player_first = player_spd >= monster.spd
        if player_spd == monster.spd:
            player_first = random.random() < 0.5

        def _player_attack() -> str:
            nonlocal monster_hp
            dmg = _calc_damage(
                player_atk, monster.defense, skill_power, elem_mult_player
            )
            crit = ""
            lck = session.stats.get("LCK", 10)
            if random.random() < lck / 200:
                dmg = int(dmg * 1.5)
                crit = " [yellow]暴击！[/yellow]"
            monster_hp = max(0, monster_hp - dmg)
            return f"   {name} 攻击！造成 [bold]{dmg}[/bold] 伤害{crit} → 怪物HP {monster_hp}/{monster_max_hp}"

        def _monster_attack() -> str:
            nonlocal player_hp
            dmg = _calc_damage(monster.atk, player_def, 0, elem_mult_monster)
            player_hp = max(0, player_hp - dmg)
            return f"   {monster.name} 反击！造成 [bold red]{dmg}[/bold red] 伤害 → HP {player_hp}"

        if player_first:
            msg = _player_attack()
            log_fn(msg)
            log.append(msg)
            time.sleep(0.25)
            if monster_hp > 0:
                msg = _monster_attack()
                log_fn(msg)
                log.append(msg)
                time.sleep(0.25)
        else:
            msg = _monster_attack()
            log_fn(msg)
            log.append(msg)
            time.sleep(0.25)
            if player_hp > 0:
                msg = _player_attack()
                log_fn(msg)
                log.append(msg)
                time.sleep(0.25)

    hp_lost = start_hp - player_hp

    if monster_hp <= 0:
        result = _generate_rewards(monster, session)
        result.rounds = round_num
        result.hp_lost = hp_lost
        result.log = log
        reward_parts = []
        if result.reward_tickets:
            reward_parts.append(f"+{result.reward_tickets}券")
        if result.reward_stat:
            reward_parts.append(f"{result.reward_stat[0]}+{result.reward_stat[1]}")
        rewards_str = "  ".join(reward_parts) if reward_parts else "无额外奖励"
        log_fn(
            f"   [bold green]胜利！[/bold green] 击败了 {monster.name}！"
            f"({round_num}回合  -{hp_lost}HP)  {rewards_str}"
        )
        return result
    else:
        log_fn(
            f"   [bold red]战败...[/bold red] {name} 被 {monster.name} 击倒了。(坚持了{round_num}回合)"
        )
        return BattleResult(won=False, rounds=round_num, hp_lost=hp_lost, log=log)
