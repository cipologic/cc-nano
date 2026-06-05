"""挂机冒险主循环 — 状态机 + 键盘输入 + 自动冒险后台线程。

屏幕状态：MAIN_MENU → ADVENTURE / BADGES / GACHA

渲染：手动 ANSI 备用屏幕 + Rich Console 渲染到缓冲区。
不使用 Rich.Live — 避免与 tty 原始模式发生线程冲突。
"""

from __future__ import annotations

import os
import random
import sys
import threading
import time
from io import StringIO

from rich.console import Console
from rich.panel import Panel

from ..companion import get_companion
from . import state as game_state
from .badges import draw_badge, draw_badge_multi
from .lockfile import acquire_lock, release_lock, update_heartbeat
from .persistence import restore_from_loot, save_session
from .render import (GACHA_OPTIONS, MENU_ITEMS, render_adventure,
                     render_badges_screen, render_gacha_screen,
                     render_main_menu, tick_frame)

# ---------------------------------------------------------------------------
# 屏幕状态常量
# ---------------------------------------------------------------------------

MAIN_MENU = "MAIN_MENU"
ADVENTURE = "ADVENTURE"
BADGES = "BADGES"
GACHA = "GACHA"


# ---------------------------------------------------------------------------
# 非阻塞键盘输入（需要 tty 原始模式）
# ---------------------------------------------------------------------------


def _read_key() -> str | None:
    """使用原始 fd I/O 进行非阻塞按键读取。

    一次性读取所有可用字节，并匹配完整的序列。
    只返回识别的按键；其他所有内容均被静默忽略。
    """
    import select

    fd = sys.stdin.fileno()
    try:
        r, _, _ = select.select([fd], [], [], 0)
        if not r:
            return None
        data = os.read(fd, 64)  # 一次性读取所有待处理的字节
        if not data:
            return None

        # 匹配完整的转义序列
        if data == b"\x1b[A" or data == b"\x1bOA":
            return "UP"
        if data == b"\x1b[B" or data == b"\x1bOB":
            return "DOWN"
        if data in (b"\r", b"\n"):
            return "ENTER"
        if data in (b"q", b"Q"):
            return "QUIT"
        if data == b"\x1b":
            return "ESC"

        # 其他所有内容（鼠标事件、其他按键）→ 忽略
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 自动冒险后台线程
# ---------------------------------------------------------------------------


def _adventure_thread(
    stop_event: threading.Event,
    log_buffer: list[str],
    log_lock: threading.Lock,
) -> None:
    """自动冒险循环。持续运行直到 stop_event 被设置或 HP 归零。"""
    from .battle import run_battle
    from .events import auto_event
    from .world import get_location, get_random_monster

    def log(msg: str) -> None:
        with log_lock:
            log_buffer.append(msg)
            if len(log_buffer) > 300:
                log_buffer.pop(0)

    session = game_state.get_session()
    if not session:
        return

    if not session.location:
        session.location = get_location("林间小径")

    log(f"🌟 [bold]{session.companion_name}[/bold] 踏上了冒险之旅！")
    if session.location:
        log(
            f"   出发地：[bold cyan]{session.location.name}[/bold cyan]（{session.location.region}）"
        )

    while not stop_event.is_set():
        # 动作之间的睡眠间隔（每 0.1 秒检查一次 stop_event）
        interval = random.uniform(2.0, 3.5)
        elapsed = 0.0
        while elapsed < interval:
            if stop_event.is_set():
                break
            time.sleep(0.1)
            elapsed += 0.1

        if stop_event.is_set():
            break

        session = game_state.get_session()
        if not session:
            break

        if not game_state.is_alive():
            log(
                f"💀 [bold red]{session.companion_name} 的体力耗尽了...冒险结束！[/bold red]"
            )
            stop_event.set()
            break

        roll = random.random()

        if roll < 0.30:
            # 战斗
            region = session.location.region if session.location else "幽暗森林"
            monster = get_random_monster(region)
            if monster:
                loc_name = session.location.name if session.location else "???"
                log(f"⚔️  在 [bold]{loc_name}[/bold] 遭遇了野生 {monster.name}！")
                result = run_battle(session, monster, log)
                if result.won:
                    if result.reward_tickets:
                        game_state.add_tickets(result.reward_tickets)
                    if result.reward_stat:
                        stat_name, amount = result.reward_stat
                        game_state.apply_stat_change(stat_name, amount)
                game_state.apply_stat_change("HP", -result.hp_lost)

        elif roll < 0.50:
            # 移动到相邻地点
            if session.location and session.location.connections:
                dest_name = random.choice(session.location.connections)
                dest = get_location(dest_name)
                if dest:
                    session.location = dest
                    bonus = dest.ticket_bonus
                    if bonus > 0:
                        game_state.add_tickets(bonus)
                        log(
                            f"🚶 {session.companion_name} 前往了 [bold cyan]{dest_name}[/bold cyan]！(+{bonus}券)"
                        )
                    else:
                        log(
                            f"🚶 {session.companion_name} 前往了 [bold cyan]{dest_name}[/bold cyan]。"
                        )

        elif roll < 0.80:
            # 探索当前位置
            loc = session.location
            if loc:
                w = loc.event_weights
                ev_roll = random.random()
                cumulative = 0.0
                chosen = "nothing"
                for etype, weight in w.items():
                    cumulative += weight
                    if ev_roll < cumulative:
                        chosen = etype
                        break

                if chosen == "tickets":
                    amount = random.randint(1, 2) + loc.ticket_bonus
                    game_state.add_tickets(amount)
                    log(
                        f"🔍 在 [bold]{loc.name}[/bold] 探索，发现了 [yellow]{amount}[/yellow] 张奖券！"
                    )
                elif chosen == "stat":
                    stat_name = random.choice(["HP", "ATK", "DEF", "SPD", "LCK"])
                    amount = random.randint(1, 2)
                    game_state.apply_stat_change(stat_name, amount)
                    log(
                        f"🔍 在 [bold]{loc.name}[/bold] 探索，感觉状态提升了！({stat_name}+{amount})"
                    )
                else:
                    log(
                        f"🔍 在 [bold]{loc.name}[/bold] 四处探索，平静地度过了一段时光。"
                    )

        else:
            # 随机事件
            auto_event(session, log)

        # 动作后重新检查 HP
        if not game_state.is_alive():
            log(
                f"💀 [bold red]{session.companion_name} 的体力耗尽了...冒险结束！[/bold red]"
            )
            stop_event.set()
            break


# ---------------------------------------------------------------------------
# 入口点
# ---------------------------------------------------------------------------


def start_game(client, console: Console, model: str) -> None:
    """由 /buddy ia 调用的入口点。"""
    companion = get_companion()
    if not companion:
        console.print("[red]请先创建一个buddy伙伴！使用 /buddy create[/red]")
        return

    if not acquire_lock():
        console.print("[yellow]游戏已在另一个终端运行中。[/yellow]")
        return

    try:
        _run_game(companion, console)
    finally:
        release_lock()


# ---------------------------------------------------------------------------
# 主游戏 UI 循环 — 手动 ANSI 屏幕 + Rich 渲染到缓冲区
# ---------------------------------------------------------------------------


def _run_game(companion, console: Console) -> None:
    import termios
    import tty

    try:
        term_width, term_height = os.get_terminal_size()
    except OSError:
        term_width, term_height = 120, 40

    # 可变游戏 UI 状态
    from typing import Any

    ui: dict[str, Any] = {
        "screen": MAIN_MENU,
        "cursor": 0,
        "log_buffer": [],
        "log_lock": threading.Lock(),
        "stop_event": threading.Event(),
        "adv_thread": None,
        "last_draw": None,
        "gacha_cursor": 0,
        "gacha_anim": False,
        "gacha_anim_until": 0.0,
        "running": True,
    }

    # 使用已有的券和徽章创建初始会话
    sess = game_state.new_session(
        companion_name=companion.name,
        companion_species=companion.species,
        companion_eye=companion.eye,
        companion_hat=companion.hat,
    )
    restore_from_loot(sess)

    # 心跳线程
    hb_stop = threading.Event()

    def _heartbeat() -> None:
        while not hb_stop.is_set():
            update_heartbeat()
            hb_stop.wait(30)

    hb_thread = threading.Thread(target=_heartbeat, daemon=True)
    hb_thread.start()

    def _start_adventure() -> None:
        s = game_state.new_session(
            companion_name=companion.name,
            companion_species=companion.species,
            companion_eye=companion.eye,
            companion_hat=companion.hat,
        )
        restore_from_loot(s)
        with ui["log_lock"]:
            ui["log_buffer"].clear()
        ui["stop_event"] = threading.Event()
        ui["screen"] = ADVENTURE
        t = threading.Thread(
            target=_adventure_thread,
            args=(ui["stop_event"], ui["log_buffer"], ui["log_lock"]),
            daemon=True,
        )
        ui["adv_thread"] = t
        t.start()

    def _end_adventure() -> None:
        ui["stop_event"].set()
        t = ui["adv_thread"]
        if t and t.is_alive():
            t.join(timeout=2)
        s = game_state.get_session()
        if s:
            save_session(s)
        ui["screen"] = MAIN_MENU

    def _build_frame():
        try:
            s = game_state.get_session() or sess
            sc = ui["screen"]
            if sc == MAIN_MENU:
                return render_main_menu(s, ui["cursor"])
            elif sc == ADVENTURE:
                with ui["log_lock"]:
                    lines = list(ui["log_buffer"])
                return render_adventure(s, lines)
            elif sc == BADGES:
                return render_badges_screen(s)
            elif sc == GACHA:
                return render_gacha_screen(
                    s, ui["gacha_cursor"], ui["last_draw"], ui["gacha_anim"]
                )
            return render_main_menu(s, ui["cursor"])
        except Exception as e:
            return Panel(f"[red]渲染错误: {e}[/red]")

    def _paint(frame) -> None:
        """将 Rich 可渲染对象渲染为字符串，并一次性写入 stdout。"""
        buf = StringIO()
        rc = Console(
            file=buf,
            force_terminal=True,
            width=term_width,
            height=term_height,
            color_system="256",
        )
        rc.print(frame)
        # \033[H = 光标回家，帧内容，\033[J = 擦除从光标到屏幕末尾的内容
        sys.stdout.write("\033[H" + buf.getvalue() + "\033[J")
        sys.stdout.flush()

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        # 进入备用屏幕缓冲区 + 隐藏光标
        sys.stdout.write("\033[?1049h\033[?25l\033[2J")
        sys.stdout.flush()
        # cbreak 模式：立即获取字符输入，不显示回显，但保留输出处理
        # （原始模式会禁用 OPOST，这会破坏 Rich 输出中的 \n → \r\n 转换）
        tty.setcbreak(sys.stdin.fileno())

        while ui["running"]:
            # 检查冒险是否自然结束（HP=0，线程已停止）
            if ui["screen"] == ADVENTURE:
                t = ui["adv_thread"]
                if ui["stop_event"].is_set() and t and not t.is_alive():
                    s = game_state.get_session()
                    if s:
                        save_session(s)
                    ui["screen"] = MAIN_MENU

            # 处理键盘输入
            key = _read_key()
            if key:
                sc = ui["screen"]

                if sc == MAIN_MENU:
                    if key == "UP":
                        ui["cursor"] = (ui["cursor"] - 1) % len(MENU_ITEMS)
                    elif key == "DOWN":
                        ui["cursor"] = (ui["cursor"] + 1) % len(MENU_ITEMS)
                    elif key == "ENTER":
                        if ui["cursor"] == 0:
                            _start_adventure()
                        elif ui["cursor"] == 1:
                            ui["screen"] = BADGES
                        elif ui["cursor"] == 2:
                            ui["screen"] = GACHA
                            ui["last_draw"] = None
                            ui["gacha_cursor"] = 0
                    elif key in ("QUIT", "ESC"):
                        ui["running"] = False

                elif sc == ADVENTURE:
                    if key in ("QUIT", "ESC"):
                        _end_adventure()

                elif sc == BADGES:
                    if key in ("QUIT", "ESC"):
                        ui["screen"] = MAIN_MENU

                elif sc == GACHA:
                    if key in ("QUIT", "ESC"):
                        ui["screen"] = MAIN_MENU
                    elif key == "UP":
                        ui["gacha_cursor"] = (ui["gacha_cursor"] - 1) % len(
                            GACHA_OPTIONS
                        )
                    elif key == "DOWN":
                        ui["gacha_cursor"] = (ui["gacha_cursor"] + 1) % len(
                            GACHA_OPTIONS
                        )
                    elif key == "ENTER":
                        s = game_state.get_session() or sess
                        ui["gacha_anim"] = True
                        ui["gacha_anim_until"] = time.time() + 0.8
                        if ui["gacha_cursor"] == 0:
                            # 单抽
                            badge, is_new, refund = draw_badge(s)
                            if badge is None:
                                ui["last_draw"] = []  # 空列表表示券不足
                            else:
                                ui["last_draw"] = [(badge, is_new, refund)]
                        else:
                            # 十连抽
                            results = draw_badge_multi(s)
                            ui["last_draw"] = results  # 空列表表示券不足
                        save_session(s)

            # 抽卡动画过期
            if ui["gacha_anim"] and time.time() > ui["gacha_anim_until"]:
                ui["gacha_anim"] = False

            # 渲染帧
            tick_frame()
            _paint(_build_frame())
            time.sleep(0.15)

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        # 显示光标 + 退出备用屏幕
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()
        hb_stop.set()
        ui["stop_event"].set()
        if ui["screen"] == ADVENTURE:
            s = game_state.get_session()
            if s:
                save_session(s)
        game_state.end_session()
