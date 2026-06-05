"""/buddy 命令处理程序 — AI 伴侣宠物。

子命令：
  /buddy          — 孵化（首次）或显示伴侣卡片
  /buddy help     — 显示所有命令和游戏指南
  /buddy pet      — 抚摸你的伴侣（爱心动画）
  /buddy stats    — 显示详细属性
  /buddy mood     — 显示当前心情
  /buddy new      — 孵化一个新的随机伴侣
  /buddy list     — 查看所有伴侣（仓库）
  /buddy select N — 切换到第 N 个伴侣
  /buddy mute     — 静音伴侣的反应
  /buddy unmute   — 取消静音伴侣的反应
"""

from __future__ import annotations

import time
import uuid

from rich.console import Console
from rich.live import Live
from rich.text import Text

from cc_nano.core.llm import LLMClient

from .companion import (companion_user_id, get_all_companions, get_companion,
                        roll, roll_with_seed)
from .render import (render_companion_card,
                     render_companion_list, render_hatch_animation)
from .storage import (load_active_index, save_active_index, save_companion_muted,
                      save_new_companion, save_stored_companion)
from .types import CompanionBones, CompanionSoul


def _generate_soul(
    bones: CompanionBones,
    client: LLMClient,
    model: str,
) -> CompanionSoul:
    """调用配置的 LLM 生成名字和性格。"""
    stats_desc = ", ".join(f"{k}={v}" for k, v in bones.stats.items())
    shiny_note = " 这是一只极其稀有的闪光伴侣！" if bones.shiny else ""

    prompt = (
        f"你正在为一只新的伴侣宠物起名。它是一只 {bones.rarity} 稀有度的 {bones.species}，"
        f"属性如下：{stats_desc}。它的眼睛样式是 {bones.eye}，"
        f"戴着一顶 {bones.hat} 帽子。{shiny_note}\n\n"
        f"请生成：\n"
        f"1. 一个简短、有创意的名字（1-2 个词，不要引号）\n"
        f"2. 一句话的性格描述（不超过 80 个字符）\n\n"
        f"严格按照以下格式回复：\n"
        f"NAME: <名字>\n"
        f"PERSONALITY: <性格>"
    )

    response = client.create_message(
        model=model,
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}],
    )

    text = ""
    for block in response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text", "")
    text = text.strip()
    name = "Buddy"
    personality = f"一只神秘的 {bones.species}。"

    for line in text.split("\n"):
        line = line.strip()
        if line.upper().startswith("NAME:"):
            name = line.split(":", 1)[1].strip()
        elif line.upper().startswith("PERSONALITY:"):
            personality = line.split(":", 1)[1].strip()

    return CompanionSoul(name=name, personality=personality)


def _hatch(client: LLMClient, console: Console, model: str) -> None:
    """孵化一个新的伴侣：生成骨头，调用 API 生成灵魂，保存，播放动画。"""
    roll.cache_clear()  # 确保新鲜随机（种子可能已更改）
    user_id = companion_user_id()
    r = roll(user_id)
    bones = r.bones

    console.print("\n[dim]正在孵化你的伴侣...[/dim]")

    try:
        soul = _generate_soul(bones, client, model)
    except Exception as e:
        console.print(f"[red]生成伴侣灵魂失败：{e}[/red]")
        # 备用灵魂
        soul = CompanionSoul(
            name="Buddy",
            personality=f"一只安静的 {bones.species}，更喜欢用行动代替言语。",
        )

    save_stored_companion(soul)
    render_hatch_animation(bones, soul, console)

    companion = get_companion()
    if companion:
        render_companion_card(companion, console)


def _hatch_new(client: LLMClient, console: Console, model: str) -> None:
    """使用唯一种子孵化一只额外的随机伴侣。"""
    seed = f"buddy-new-{uuid.uuid4()}"
    r = roll_with_seed(seed)
    bones = r.bones

    console.print("\n[dim]正在孵化一只新的伴侣...[/dim]")

    try:
        soul = _generate_soul(bones, client, model)
    except Exception as e:
        console.print(f"[red]生成伴侣灵魂失败：{e}[/red]")
        soul = CompanionSoul(
            name="Buddy",
            personality=f"一只安静的 {bones.species}，更喜欢用行动代替言语。",
        )

    save_new_companion(soul, seed)
    render_hatch_animation(bones, soul, console)

    companion = get_companion()
    if companion:
        render_companion_card(companion, console)


def _pet_animation(console: Console) -> None:
    """抚摸伴侣时显示爱心动画。

    与 CompanionSprite.tsx 中的 PET_HEARTS 匹配：2.5 秒内 5 帧爱心上浮动画，
    最后以淡出的点结束。
    """
    companion = get_companion()
    if not companion:
        return

    from .sprites import render_sprite
    from .types import RARITY_COLORS

    color = RARITY_COLORS.get(companion.rarity, "dim")
    bones = CompanionBones(
        rarity=companion.rarity,
        species=companion.species,
        eye=companion.eye,
        hat=companion.hat,
        shiny=companion.shiny,
        stats=companion.stats,
    )

    # 匹配 CompanionSprite.tsx 中的 PET_HEARTS — 爱心上浮并淡化为点
    H = "\u2764"
    pet_hearts = [
        f"   {H}    {H}   ",
        f"  {H}  {H}   {H}  ",
        f" {H}   {H}  {H}   ",
        f"{H}  {H}      {H} ",
        "\u00b7    \u00b7   \u00b7  ",
    ]

    # 兴奋模式：快速循环所有精灵帧
    with Live(console=console, refresh_per_second=4, transient=True) as live:
        for i, heart_line in enumerate(pet_hearts):
            sprite_lines = render_sprite(bones, frame=i % 3)
            # 使用富文本样式构建 Text（而不是标记字符串）
            frame_text = Text()
            frame_text.append(f"  {heart_line}\n", style="bold red")
            for sl in sprite_lines:
                frame_text.append(f"  {sl}\n", style=color)
            live.update(frame_text)
            time.sleep(0.5)

    console.print(f"[dim]{companion.name} 高兴地扭了扭。[/dim]")

    # 抚摸提升心情
    try:
        from .mood import apply_decay, apply_events
        from .storage import load_active_mood, save_active_mood

        now_ms = int(time.time() * 1000)
        mood = load_active_mood()
        mood = apply_decay(mood, now_ms)
        mood = apply_events(mood, ["pet"])
        save_active_mood(mood)
    except Exception:
        pass


def _render_mood(companion, console: Console) -> None:
    """显示伴侣的心情详情。"""
    from .render import _stat_bar
    from .types import MOOD_DIMENSIONS, MOOD_NEUTRAL, RARITY_COLORS

    color = RARITY_COLORS.get(companion.rarity, "dim")
    mood = companion.mood
    console.print(f"\n[{color}]{companion.name} 的心情：[/{color}]")
    for dim in MOOD_DIMENSIONS:
        val = getattr(mood, dim)
        bar = _stat_bar(val)
        if abs(val - MOOD_NEUTRAL) < 10:
            label = "平稳"
        elif val > MOOD_NEUTRAL:
            label = "高涨"
        else:
            label = "低落"
        console.print(f"  {dim.capitalize():<10} {bar} {val:>3} ({label})")
    console.print(f"\n[dim]主导情绪：{mood.dominant().lower()}[/dim]")


def _render_help(console: Console) -> None:
    """显示所有 buddy 命令和游戏指南。"""
    from rich.panel import Panel

    help_text = (
        "[bold]命令列表[/bold]\n"
        "\n"
        "  [cyan]/buddy[/cyan]              孵化你的第一只伴侣，或显示它的卡片\n"
        "  [cyan]/buddy help[/cyan]          显示本帮助\n"
        "  [cyan]/buddy pet[/cyan]           抚摸你的伴侣（爱心动画，提升快乐值）\n"
        "  [cyan]/buddy stats[/cyan]         显示伴侣卡片（含属性和心情）\n"
        "  [cyan]/buddy mood[/cyan]          显示当前心情详情\n"
        "  [cyan]/buddy new[/cyan]           孵化一只额外的随机伴侣\n"
        "  [cyan]/buddy list[/cyan]          查看你收藏中的所有伴侣\n"
        "  [cyan]/buddy select N[/cyan]      切换到第 N 个伴侣\n"
        "  [cyan]/buddy mute[/cyan]          静音伴侣的对话气泡\n"
        "  [cyan]/buddy unmute[/cyan]        取消静音伴侣的对话气泡\n"
        "  [cyan]/buddy ia[/cyan]            开始宝可梦游戏冒险\n"
        "\n"
        "[bold]游戏指南[/bold]\n"
        "\n"
        "  [yellow]孵化[/yellow]  你的第一只伴侣由你的用户名决定。\n"
        "            使用 [cyan]/buddy new[/cyan] 可以孵化更多随机种子的伴侣。\n"
        "            18 种物种，5 种稀有度（普通到传说），1% 闪光几率。\n"
        "\n"
        "  [yellow]属性[/yellow]  每只伴侣有 5 项永久属性（0-100）：\n"
        "            DEBUGGING（调试）、PATIENCE（耐心）、CHAOS（混沌）、\n"
        "            WISDOM（智慧）、SNARK（挖苦）。\n"
        "            这些属性决定了伴侣的说话和反应风格。\n"
        "\n"
        "  [yellow]心情[/yellow]  6 个动态心情维度，随时间变化：\n"
        "            快乐（Happy）、无聊（Bored）、兴奋（Excited）、\n"
        "            疲倦（Tired）、易怒（Grumpy）、好奇（Curious）。\n"
        "            心情受你的编码活动影响：\n"
        "            - 任务成功 / 修复 bug  → 快乐、兴奋\n"
        "            - 错误 / 失败          → 易怒、疲倦\n"
        "            - 阅读 / 探索代码      → 好奇\n"
        "            - 抚摸（/buddy pet）   → 快乐、兴奋\n"
        "            - 长时间空闲           → 无聊\n"
        "            心情会随时间逐渐衰减回中性。\n"
        "\n"
        "  [yellow]对话[/yellow]  在 CC-NANO 每次回复后，你的伴侣会做出反应。\n"
        "            用它的名字称呼它，即可直接聊天（20 轮记忆）。\n"
        "            它的语气会同时适配属性和当前心情。\n"
        "\n"
        "  [yellow]皮卡丘[/yellow]  在孵化前设置环境变量 CC_NANO_BUDDY_SEED=pikachu-3361\n"
        "             即可解锁隐藏的传说级物种——皮卡丘。"
    )

    panel = Panel(
        help_text,
        title="[bold]Buddy — AI 伴侣宠物[/bold]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)


def handle_buddy_command(
    args: str,
    client: LLMClient,
    console: Console,
    model: str,
) -> None:
    """处理 /buddy 命令。"""
    subcmd = args.strip().lower()

    if subcmd == "":
        # 孵化或显示卡片
        companion = get_companion()
        if companion:
            render_companion_card(companion, console)
        else:
            _hatch(client, console, model)

    elif subcmd == "help":
        _render_help(console)

    elif subcmd == "pet":
        companion = get_companion()
        if not companion:
            console.print("[dim]还没有伴侣。输入 /buddy 孵化一只！[/dim]")
        else:
            _pet_animation(console)

    elif subcmd == "stats":
        companion = get_companion()
        if not companion:
            console.print("[dim]还没有伴侣。输入 /buddy 孵化一只！[/dim]")
        else:
            render_companion_card(companion, console)

    elif subcmd == "mute":
        save_companion_muted(True)
        console.print("[dim]伴侣反应已静音。[/dim]")

    elif subcmd == "unmute":
        save_companion_muted(False)
        console.print("[dim]伴侣反应已取消静音。[/dim]")

    elif subcmd == "mood":
        companion = get_companion()
        if not companion:
            console.print("[dim]还没有伴侣。输入 /buddy 孵化一只！[/dim]")
        else:
            _render_mood(companion, console)

    elif subcmd == "ia":
        from .poke_game import start_game

        start_game(client, console, model)

    elif subcmd == "new":
        _hatch_new(client, console, model)

    elif subcmd == "list":
        companions = get_all_companions()
        active = load_active_index()
        render_companion_list(companions, active, console)

    elif subcmd.startswith("select"):
        parts = subcmd.split()
        if len(parts) != 2 or not parts[1].isdigit():
            console.print(
                "[dim]用法：/buddy select <数字>（例如 /buddy select 2）[/dim]"
            )
        else:
            n = int(parts[1])
            companions = get_all_companions()
            if n < 1 or n > len(companions):
                console.print(
                    f"[dim]无效数字。你拥有 {len(companions)} 只伴侣。请输入 1-{len(companions)}。[/dim]"
                )
            else:
                idx = n - 1
                save_active_index(idx)
                comp = companions[idx]
                console.print(
                    f"[bold]已切换到 #{n}：{comp.name}（{comp.species}）[/bold]"
                )
                render_companion_card(comp, console)

    else:
        console.print(
            "[dim]用法：/buddy [help|pet|stats|mood|new|list|select N|mute|unmute|ia][/dim]"
        )
