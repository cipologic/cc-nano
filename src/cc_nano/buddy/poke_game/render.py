"""Idle Adventure — 固定全屏 TUI 渲染。

四个屏幕状态：
  MAIN_MENU  — 方向键菜单（开始冒险 / 徽章 / 扭蛋）
  ADVENTURE  — HUD + 自动滚动日志
  BADGES     — 徽章收集概览
  GACHA      — 单抽 / 十连抽，含稀有保底
"""

from __future__ import annotations

from rich import box
from rich.align import Align
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..sprites import render_sprite, sprite_frame_count
from ..types import CompanionBones
from .badges import ALL_BADGES, badge_progress
from .types import BADGE_COLORS, TICKET_COST, GameSession

# ---------------------------------------------------------------------------
# 精灵动画的共享帧计数器
# ---------------------------------------------------------------------------
_frame = 0


def tick_frame() -> None:
    global _frame
    _frame += 1


# ---------------------------------------------------------------------------
# 标记辅助函数 — Text.append() 不解析 [bold] 等标记
# ---------------------------------------------------------------------------


def _m(t: Text, markup: str) -> None:
    """将 Rich 标记字符串追加到 Text 对象（已解析）。"""
    t.append_text(Text.from_markup(markup))


# ---------------------------------------------------------------------------
# 精灵辅助函数
# ---------------------------------------------------------------------------


def _get_sprite_lines(session: GameSession) -> list[str]:
    bones = CompanionBones(
        rarity="common",
        species=session.companion_species,
        eye=session.companion_eye,
        hat=session.companion_hat,
        shiny=False,
    )
    count = sprite_frame_count(session.companion_species)
    return render_sprite(bones, _frame % count)


# ---------------------------------------------------------------------------
# 状态条辅助函数（返回纯字符串，无标记）
# ---------------------------------------------------------------------------


def _stat_bar(value: int, maximum: int, width: int = 10) -> str:
    ratio = min(1.0, value / max(1, maximum))
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# 主菜单
# ---------------------------------------------------------------------------

MENU_ITEMS = ["开始新的冒险", "查看已有奖章", "开始抽奖"]

_STORY = (
    "很久以前，世界树「源树」\n"
    "维系着六块大陆的平衡。\n"
    "\n"
    "一场「大断裂」灾难降临，\n"
    "源树核心碎为 30 枚徽章，\n"
    "散落在六块大陆的角落。\n"
    "\n"
    "怪物从虚空裂隙中涌出，\n"
    "世界陷入了混沌与黑暗。\n"
    "\n"
    "传说——\n"
    "集齐全部徽章，\n"
    "便能唤醒源树，\n"
    "让光重回大地。\n"
    "\n"
    "冒险可获得奖券，\n"
    "奖券可以抽取徽章碎片。\n"
    "你的伙伴即将踏上旅途……"
)


def render_main_menu(session: GameSession, cursor: int) -> Panel:
    """渲染主菜单：左侧标题，中间精灵+菜单，右侧故事。全部在一个盒子中。"""
    lines = _get_sprite_lines(session)
    sprite_text = "\n".join(lines)
    owned, total = badge_progress(session)

    # --- 左侧：标题（水平两行，居中）---
    left = Text(justify="center")
    left.append("\n\n\n\n")
    left.append("I D L E\n", style="bold cyan")
    left.append("A D V E N T U R E\n", style="bold cyan")
    left.append("\n")
    left.append("✦ ✦ ✦\n", style="dim cyan")

    # --- 中间：精灵 + 菜单 + 状态 ---
    center = Text()
    center.append("\n")
    center.append(sprite_text + "\n\n", style="bold")
    for i, item in enumerate(MENU_ITEMS):
        if i == cursor:
            center.append(f" ▶  {item}\n", style="bold cyan")
        else:
            center.append(f"    {item}\n", style="dim")
    center.append(f"\n 奖券: {session.tickets}  徽章: {owned}/{total}\n", style="dim")
    center.append("\n ↑↓选择 Enter确认 Q退出\n", style="dim")

    # --- 右侧：故事 ---
    right = Text()
    right.append("\n")
    for line in _STORY.split("\n"):
        right.append(line + "\n", style="dim italic")

    # 通过 Table 实现三列（不会像 Layout 那样自动撑开高度）
    inner = Table(show_header=False, box=None, expand=True, padding=(0, 2))
    inner.add_column(ratio=30)
    inner.add_column(ratio=40)
    inner.add_column(ratio=30)
    inner.add_row(Align.center(left, vertical="middle"), Align.center(center), right)

    return Panel(
        inner,
        border_style="cyan",
        box=box.DOUBLE,
    )


# ---------------------------------------------------------------------------
# 徽章标志渲染（紧凑，按稀有度着色）
#
# 稀有度边框：   金色 ⟪N⟫   红色 ⟨N⟩   紫色 [N]   绿色 (N)
# 只显示已拥有的徽章。共 4 行，最上方为最稀有。
# ---------------------------------------------------------------------------


# 徽章 ID 格式："green_01" .. "gold_02" → 提取数字部分
def _badge_num(badge_id: str) -> str:
    return badge_id.split("_")[-1].lstrip("0") or "0"


_TIER_FMT = {
    "gold": ("⟪{}⟫", "bold yellow"),
    "red": ("⟨{}⟩", "bold red"),
    "purple": ("[{}]", "bold magenta"),
    "green": ("({})", "green"),
}

_TIER_ORDER = ["gold", "red", "purple", "green"]


def _render_badge_panel(session: GameSession) -> Text:
    """渲染拥有的徽章标志，按稀有度分组，每行一个稀有度。"""
    owned_ids = {b.badge_id for b in session.badges}

    t = Text()
    for tier in _TIER_ORDER:
        fmt, style = _TIER_FMT[tier]
        badges_in_tier = [
            bid for bid, b in ALL_BADGES.items() if b.tier == tier and bid in owned_ids
        ]
        if not badges_in_tier:
            t.append("\n")
            continue
        for bid in badges_in_tier:
            num = _badge_num(bid)
            t.append(fmt.format(num) + " ", style=style)
        t.append("\n")
    return t


# ---------------------------------------------------------------------------
# 冒险 HUD（4 列：精灵 15% | 徽章 35% | 属性 25% | 位置 25%）
# ---------------------------------------------------------------------------


def render_adventure(session: GameSession, log_lines: list[str]) -> Layout:
    """渲染冒险屏幕，包含 HUD 和日志。"""
    layout = Layout()
    layout.split_column(
        Layout(name="hud", size=9),
        Layout(name="log"),
        Layout(name="footer", size=3),
    )

    sprite_lines = _get_sprite_lines(session)
    sprite_text = "\n".join(sprite_lines)

    hp = session.stats.get("HP", 0)
    atk = session.stats.get("ATK", 0)
    def_ = session.stats.get("DEF", 0)
    spd = session.stats.get("SPD", 0)
    lck = session.stats.get("LCK", 0)

    stats_text = Text()
    stats_text.append("HP  ")
    stats_text.append(_stat_bar(hp, 100, 10), style="red")
    stats_text.append(f" {hp:>3}\n")
    stats_text.append("ATK ")
    stats_text.append(_stat_bar(atk, 50, 10), style="yellow")
    stats_text.append(f" {atk:>3}\n")
    stats_text.append("DEF ")
    stats_text.append(_stat_bar(def_, 50, 10), style="blue")
    stats_text.append(f" {def_:>3}\n")
    stats_text.append("SPD ")
    stats_text.append(_stat_bar(spd, 50, 10), style="green")
    stats_text.append(f" {spd:>3}\n")
    stats_text.append("LCK ")
    stats_text.append(_stat_bar(lck, 50, 10), style="magenta")
    stats_text.append(f" {lck:>3}\n")

    loc_name = session.location.name if session.location else "未知"
    region = session.location.region if session.location else ""
    connections = session.location.connections if session.location else []
    connections_str = " ".join(connections[:3]) if connections else "—"

    loc_text = Text()
    loc_text.append("位置: ")
    loc_text.append(loc_name, style="bold")
    loc_text.append("\n")
    loc_text.append(f"区域: {region}\n", style="dim")
    loc_text.append(f"连接: {connections_str}\n", style="dim")
    loc_text.append("\n奖券: ")
    loc_text.append(str(session.tickets), style="bold yellow")
    loc_text.append("\n")

    badge_text = _render_badge_panel(session)

    # 使用 Layout 实现精确比例的四列 HUD
    hud_inner = Layout()
    hud_inner.split_row(
        Layout(
            Panel(Text(sprite_text, style="bold"), border_style="dim", box=box.SIMPLE),
            name="sprite",
            ratio=15,
        ),
        Layout(
            Panel(badge_text, title="徽章", border_style="cyan", box=box.SIMPLE),
            name="badges",
            ratio=35,
        ),
        Layout(
            Panel(stats_text, title="属性", border_style="yellow", box=box.SIMPLE),
            name="stats",
            ratio=25,
        ),
        Layout(
            Panel(loc_text, title="位置", border_style="blue", box=box.SIMPLE),
            name="loc",
            ratio=25,
        ),
    )

    layout["hud"].update(
        Panel(
            hud_inner,
            title=f"[bold cyan]✦ {session.companion_name} 的冒险 ✦[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(0, 0),
        )
    )

    # 日志面板 — 日志行包含 Rich 标记，需解析
    log_text = Text()
    for line in log_lines[-50:]:
        try:
            log_text.append_text(Text.from_markup(line + "\n"))
        except Exception:
            log_text.append(line + "\n")

    layout["log"].update(
        Panel(
            log_text,
            title="[bold]冒险日志[/bold]",
            border_style="dim",
            box=box.SIMPLE_HEAVY,
        )
    )

    layout["footer"].update(
        Panel(
            Text(" Q/ESC 结束冒险并结算", style="dim"),
            border_style="dim",
            box=box.SIMPLE,
        )
    )

    return layout


# ---------------------------------------------------------------------------
# 徽章屏幕
# ---------------------------------------------------------------------------


def render_badges_screen(session: GameSession) -> Panel:
    """渲染徽章收集屏幕。"""
    owned_ids = {b.badge_id for b in session.badges}
    owned, total = badge_progress(session)

    table = Table(box=box.SIMPLE, expand=True, show_header=True)
    table.add_column("徽章", width=12)
    table.add_column("稀有度", width=6)
    table.add_column("效果", width=16)
    table.add_column("说明")

    tiers = ["gold", "red", "purple", "green"]
    tier_labels = {"gold": "传说", "red": "稀有", "purple": "珍贵", "green": "普通"}
    for tier in tiers:
        for badge_id, badge in ALL_BADGES.items():
            if badge.tier != tier:
                continue
            color = BADGE_COLORS.get(tier, "white")
            if badge_id in owned_ids:
                table.add_row(
                    f"[{color}]{badge.name}[/{color}]",
                    f"[{color}]{tier_labels[tier]}[/{color}]",
                    badge.effect,
                    f"[dim]{badge.description}[/dim]",
                )
            else:
                table.add_row(
                    "[dim]???[/dim]",
                    f"[dim]{tier_labels[tier]}[/dim]",
                    "[dim]???[/dim]",
                    "[dim]尚未解锁[/dim]",
                )

    return Panel(
        table,
        title=f"[bold]徽章图鉴  [{owned}/{total}][/bold]",
        border_style="magenta",
        box=box.DOUBLE,
        subtitle="[dim]Q/ESC 返回[/dim]",
    )


# ---------------------------------------------------------------------------
# 扭蛋屏幕 — 单抽 + 十连抽
# ---------------------------------------------------------------------------

GACHA_OPTIONS = ["单抽 (5 奖券)", "十连抽 (50 奖券，保底珍贵)"]
MULTI_COST = TICKET_COST * 10


def render_gacha_screen(
    session: GameSession,
    gacha_cursor: int = 0,
    last_draw: list | None = None,  # list of (badge, is_new, refund)
    animating: bool = False,
) -> Panel:
    """渲染扭蛋抽奖屏幕。"""
    owned, total = badge_progress(session)

    t = Text()
    t.append("  奖券余额: ")
    t.append(str(session.tickets), style="bold yellow")
    t.append(f"    已收集徽章: {owned}/{total}\n\n")

    # 概率提示
    t.append("  概率: ", style="dim")
    for color, label in [
        ("green", "普通60%"),
        ("magenta", "珍贵25%"),
        ("red", "稀有10%"),
        ("yellow", "传说5%"),
    ]:
        t.append(f" {label} ", style=color)
    t.append("\n\n")

    # 抽选选项
    for i, opt in enumerate(GACHA_OPTIONS):
        if i == gacha_cursor:
            t.append(f"  ▶  {opt}\n", style="bold cyan")
        else:
            t.append(f"     {opt}\n", style="dim")
    t.append("\n")

    # 抽选结果
    if animating:
        t.append("  ✦ ★ ◆ ✿ ♦ ✸ ❋\n", style="bold yellow")
    elif last_draw is not None:
        if not last_draw:
            t.append("  奖券不足，无法抽奖！\n", style="bold red")
        else:
            tier_cn = {"green": "普通", "purple": "珍贵", "red": "稀有", "gold": "传说"}
            for badge, is_new, refund in last_draw:
                color = BADGE_COLORS.get(badge.tier, "white")
                tag = tier_cn.get(badge.tier, "")
                if is_new:
                    t.append("  ✨ ", style="bold")
                    t.append(f"【{badge.name}】", style=color)
                    t.append(f"  {tag}  {badge.effect}\n", style="dim")
                else:
                    t.append("  ↩ ", style="dim")
                    t.append(f"{badge.name}", style=color)
                    t.append(f"  重复 → 退还{refund}券\n", style="dim")

    return Panel(
        t,
        title="[bold yellow]✦ 扭蛋抽奖 ✦[/bold yellow]",
        border_style="yellow",
        box=box.DOUBLE,
        subtitle="[dim]↑↓ 选择   Enter 抽奖   Q/ESC 返回[/dim]",
    )
