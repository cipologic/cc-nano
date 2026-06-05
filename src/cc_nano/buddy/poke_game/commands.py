"""游戏命令解析器 — 基于规则，中英文双语支持。

所有命令均为硬编码。本模块仅负责解析和分发；
实际逻辑位于 loop.py 及其他模块中。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# 命令别名 (中文 → 标准英文)
# ---------------------------------------------------------------------------

_ALIASES: dict[str, str] = {
    # 移动
    "go": "go",
    "前往": "go",
    "去": "go",
    "move": "go",
    # 查看
    "look": "look",
    "观察": "look",
    "查看": "look",
    # 探索
    "explore": "explore",
    "探索": "explore",
    # 对话
    "talk": "talk",
    "对话": "talk",
    "说话": "talk",
    "chat": "talk",
    # 使用道具
    "use": "use",
    "使用": "use",
    # 抽卡
    "draw": "draw",
    "抽卡": "draw",
    "抽奖": "draw",
    # 背包
    "bag": "bag",
    "背包": "bag",
    "inventory": "bag",
    # 技能
    "skills": "skills",
    "技能": "skills",
    # 属性
    "stats": "stats",
    "属性": "stats",
    "status": "stats",
    # 徽章
    "badges": "badges",
    "徽章": "badges",
    # 地图
    "map": "map",
    "地图": "map",
    # 休息
    "rest": "rest",
    "休息": "rest",
    # 帮助
    "help": "help",
    "帮助": "help",
    # 退出
    "quit": "quit",
    "退出": "quit",
    "exit": "quit",
}

# 特殊短语触发器
_BATTLE_TRIGGERS = ["让我们去战斗吧", "let's battle", "battle", "fight", "战斗"]

# 带描述的命令（用于自动补全，展示给用户）
COMMAND_HINTS: list[tuple[str, str]] = [
    ("explore", "探索当前位置"),
    ("go", "前往其他地点"),
    ("look", "查看当前位置"),
    ("talk", "和伙伴聊天"),
    ("use", "使用道具"),
    ("draw", "抽卡(5券)"),
    ("bag", "查看背包"),
    ("skills", "查看技能"),
    ("stats", "查看属性"),
    ("badges", "查看徽章"),
    ("map", "查看地图"),
    ("rest", "休息恢复HP"),
    ("help", "帮助"),
    ("quit", "退出游戏"),
]


def parse_game_command(raw: str) -> tuple[str, str]:
    """将原始输入解析为 (标准命令, 参数)。

    如果无法识别则返回 ("unknown", raw)。
    如果是战斗触发器则返回 ("battle", "")。
    """
    text = raw.strip()
    if not text:
        return ("empty", "")

    # 检查战斗触发器
    for trigger in _BATTLE_TRIGGERS:
        if trigger in text.lower():
            return ("battle", "")

    # 分割为命令 + 参数
    parts = text.split(None, 1)
    cmd_word = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    canonical = _ALIASES.get(cmd_word)
    if canonical:
        return (canonical, args)

    # 尝试将整个输入匹配为别名（用于单字中文命令）
    canonical = _ALIASES.get(text)
    if canonical:
        return (canonical, "")

    return ("unknown", text)


# ---------------------------------------------------------------------------
# prompt_toolkit 补全器
# ---------------------------------------------------------------------------


class GameCompleter(Completer):
    """动态补全器，建议命令及上下文参数。"""

    def __init__(self, session_getter: Any = None):
        self._session_getter = session_getter

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor.lstrip()
        parts = text.split(None, 1)

        if len(parts) <= 1:
            # 补全命令本身
            query = text.lower()
            for cmd, desc in COMMAND_HINTS:
                if cmd.startswith(query):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)
            # 同时建议中文别名
            for alias, canonical in _ALIASES.items():
                if alias == canonical:
                    continue  # 跳过英文重复项
                if alias.startswith(query) and query:
                    desc = next((d for c, d in COMMAND_HINTS if c == canonical), "")
                    yield Completion(
                        alias, start_position=-len(text), display_meta=desc
                    )
        else:
            # 补全参数
            cmd_word = parts[0]
            arg_text = parts[1] if len(parts) > 1 else ""
            canonical = _ALIASES.get(cmd_word, "")

            session = self._session_getter() if self._session_getter else None
            if not session:
                return

            if canonical == "go":
                # 建议相连的地点
                if session.location:
                    for loc_name in session.location.connections:
                        if loc_name.startswith(arg_text) or not arg_text:
                            yield Completion(loc_name, start_position=-len(arg_text))

            elif canonical == "use":
                # 建议背包中的物品
                for item in session.inventory:
                    if item.name.startswith(arg_text) or not arg_text:
                        yield Completion(
                            item.name,
                            start_position=-len(arg_text),
                            display_meta=item.effect,
                        )


# ---------------------------------------------------------------------------
# 底部工具栏
# ---------------------------------------------------------------------------


def game_toolbar(session_getter: Any) -> str:
    """生成底部工具栏文本，显示关键信息。"""
    session = session_getter() if session_getter else None
    if not session:
        return ""
    hp = session.stats.get("HP", 0)
    return (
        f" HP:{hp} | ATK:{session.stats.get('ATK',0)} "
        f"DEF:{session.stats.get('DEF',0)} SPD:{session.stats.get('SPD',0)} "
        f"LCK:{session.stats.get('LCK',0)} | "
        f"券:{session.tickets} | "
        f"徽章:{len(session.badges)}/32 | "
        f"回合:{session.turn_count}"
    )


# ---------------------------------------------------------------------------
# 帮助文本
# ---------------------------------------------------------------------------

HELP_TEXT = """\
[bold]可用命令:[/bold]
  [cyan]explore[/cyan] / 探索     在当前位置探索（获得道具、技能、抽奖券）
  [cyan]go[/cyan] <地点> / 前往    移动到相连的地点
  [cyan]look[/cyan] / 观察        查看当前位置详情
  [cyan]talk[/cyan] / 对话           和你的伙伴聊聊天
  [cyan]use[/cyan] <物品> / 使用    使用背包中的道具
  [cyan]draw[/cyan] / 抽卡         消耗5张抽奖券抽取徽章
  [cyan]bag[/cyan] / 背包          查看物品列表
  [cyan]skills[/cyan] / 技能       查看已学技能
  [cyan]stats[/cyan] / 属性        查看HP/ATK/DEF/SPD/LCK
  [cyan]badges[/cyan] / 徽章       查看已收集的徽章 (进度X/32)
  [cyan]map[/cyan] / 地图          显示世界地图
  [cyan]rest[/cyan] / 休息         休息恢复HP (消耗1回合)
  [cyan]help[/cyan] / 帮助         显示此帮助
  [cyan]quit[/cyan] / 退出         结束游戏并保存\
"""
