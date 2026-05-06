"""项目管理斜杠命令：/new, /list, /change, /delete, /status。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from cc_nano.core.project import (clear_global_current_project,
                                  find_project_root,
                                  get_global_current_project, init_project,
                                  list_all_projects,
                                  set_global_current_project)

if TYPE_CHECKING:
    from cc_nano.commands import CommandContext

console = Console()


def _confirm(prompt: str) -> bool:
    """终端二次确认，返回是否确认。"""
    answer = input(f"{prompt} (y/N): ").strip().lower()
    return answer in ("y", "yes")


def handle_new(ctx: CommandContext, args: str) -> None:
    """/new - 创建新项目配置"""
    cwd = Path.cwd().resolve()
    # 1. 检查是否在全局活动项目内部
    global_root = get_global_current_project()
    if global_root:
        global_path = Path(global_root).resolve()
        if cwd == global_path or cwd.is_relative_to(global_path):
            console.print(
                f"[yellow]当前目录位于全局活动项目内部：{global_path}[/yellow]\n"
                "请切换到一个不位于任何项目内的目录后再执行 /new 命令。"
            )
            return

    # 2. 检查是否已处于某个已有项目内（向上查找）
    existing = find_project_root(cwd, downward=True, max_depth=5)
    if existing is not None:
        console.print(f"[red]错误：当前位置存在嵌套项目：{existing}[/red]")
        console.print("[dim]无法在当前位置创建新项目。请切换到其他目录。[/dim]")
        return

    # 3. 创建新项目配置
    try:
        init_project(cwd)
    except FileExistsError as e:
        console.print(f"[red]{e}[/red]")
        return

    # 4. 将新项目设为全局活动项目
    set_global_current_project(cwd)
    console.print(f"[green]✓[/green] 已在 {cwd} 创建 .cc-nano.toml")
    console.print("[green]✓[/green] 已将此项目设为全局活动项目")
    console.print("[yellow]配置已生效。[/yellow]")


def handle_list(ctx: CommandContext, args: str) -> None:
    """/list - 列出所有项目"""
    console.print("[dim]正在搜索所有项目（这可能需要几秒钟）...[/dim]")
    projects = list_all_projects()
    if not projects:
        console.print("[dim]未找到任何项目。使用 /new 创建。[/dim]")
        return
    for i, p in enumerate(projects, 1):
        console.print(f"{i:3}. {p}")
    console.print(f"\n[dim]共找到 {len(projects)} 个项目[/dim]")


def handle_change(ctx: CommandContext, args: str) -> None:
    """/change <项目根路径或目录名> - 切换活动项目并切换工作目录"""
    if not args:
        console.print(
            "[dim]用法: /change <项目根目录路径> 或 /change <项目目录名>[/dim]"
        )
        return
    target = args.strip()
    # 尝试作为完整路径
    target_path = Path(target).expanduser()
    if target_path.exists() and (target_path / ".cc-nano.toml").exists():
        proj_root = target_path.resolve()
    else:
        # 作为目录名匹配
        all_projs = list_all_projects()
        matches = [p for p in all_projs if p.name == target]
        if len(matches) == 0:
            console.print(f"[red]未找到名为 '{target}' 的项目。[/red]")
            return
        if len(matches) > 1:
            console.print("[yellow]找到多个匹配：[/yellow]")
            for m in matches:
                console.print(f"  {m}")
            console.print("请使用完整路径重新指定。")
            return
        proj_root = matches[0]

    # 更新全局活动项目
    try:
        set_global_current_project(proj_root)
        console.print(f"[green]✓[/green] 活动项目已切换至: {proj_root}")
        console.print("[yellow]配置已更新，项目生效。[/yellow]")
    except Exception as e:
        console.print(f"[red]切换失败: {e}[/red]")

    # 切换工作目录
    try:
        os.chdir(proj_root)
    except OSError as e:
        console.print(f"[red]无法切换工作目录到 {proj_root}: {e}[/red]")
        return


def handle_delete(ctx: CommandContext, args: str) -> None:
    """/delete <项目根路径或目录名> - 删除项目配置（需二次确认）"""
    if not args:
        console.print(
            "[dim]用法: /delete <项目根目录路径> 或 /delete <项目目录名>[/dim]"
        )
        return
    target = args.strip()
    target_path = Path(target).expanduser()
    if target_path.exists() and (target_path / ".cc-nano.toml").exists():
        proj_root = target_path.resolve()
    else:
        all_projs = list_all_projects()
        matches = [p for p in all_projs if p.name == target]
        if len(matches) == 0:
            console.print(f"[red]未找到项目 '{target}'。[/red]")
            return
        if len(matches) > 1:
            console.print("[yellow]多个匹配：[/yellow]")
            for m in matches:
                console.print(f"  {m}")
            console.print("请使用完整路径重新指定。")
            return
        proj_root = matches[0]

    console.print(f"即将删除项目配置: [bold]{proj_root}[/bold]")
    if not _confirm("确认删除？"):
        console.print("[dim]已取消[/dim]")
        return
    config_file = proj_root / ".cc-nano.toml"
    try:
        config_file.unlink()
        console.print("[green]✓[/green] 已删除配置文件")
        # 如果删除的是当前活动项目，清空全局记录
        curr = get_global_current_project()
        if curr and Path(curr).resolve() == proj_root:
            clear_global_current_project()
            console.print("[dim]当前活动项目已被清空。[/dim]")
        console.print(
            "[yellow] 请执行 /change 命令切换项目，或执行 /new 命令重新创建项目。[/yellow]"
        )
    except Exception as e:
        console.print(f"[red]删除失败: {e}[/red]")


def handle_status(ctx: CommandContext, args: str) -> None:
    """/status - 显示当前项目状态和配置（不暴露 api_key）"""
    # 优先显示当前所在项目（如果处于项目内）

    try:
        current_root = find_project_root(Path.cwd())
        console.print(f"[bold cyan]当前会话使用的项目根[/bold cyan]: {current_root}")
    except RuntimeError:
        console.print("[dim]当前未设置项目根。[/dim]")
        current_root = None

    # 全局活动项目（存储的）
    global_root = get_global_current_project()
    if global_root:
        console.print(
            f"\n[bold yellow]全局活动项目（存储）[/bold yellow]: {global_root}"
        )
        if current_root and Path(global_root).resolve() != current_root:
            console.print(
                "[dim]注意：全局活动项目与当前会话使用的项目不一致。"
                "使用 /change 可切换全局活动项目。[/dim]"
            )
    else:
        console.print("\n[dim]未设置全局活动项目。[/dim]")

    # 读取实际使用的项目配置文件
    if current_root:
        console.print(f"[bold cyan]当前所在项目[/bold cyan]: {current_root}")
        config_file = current_root / ".cc-nano.toml"
        if not config_file.exists():
            console.print(f"[red]配置文件丢失: {config_file}[/red]")
            return
        # 安全加载配置（不显示 api_key）
        try:
            import tomllib

            with open(config_file, "rb") as f:
                data = tomllib.load(f)
            # 屏蔽敏感字段
            data.pop("api_key", None)
            # 显示关键配置
            console.print(f"\n[bold]项目配置 ({current_root}):[/bold]")
            console.print(f"  提供商: {data.get('provider', 'openai')}")
            console.print(f"  模型: {data.get('model', '未设置（将使用默认）')}")
            console.print(f"  API 基础 URL: {data.get('base_url', '未设置')}")
            console.print(f"  最大令牌: {data.get('max_tokens', '默认')}")
            console.print(f"  努力程度: {data.get('effort', '未设置')}")
            console.print(f"  陪伴模型: {data.get('buddy_model', '未设置')}")
            console.print(
                f"  记忆目录: {data.get('memory_dir', '.config/cc-nano/memory')}"
            )
            console.print(f"  自动梦境: {data.get('auto_dream', True)}")
            sandbox = data.get("sandbox", {})
            console.print(f"  沙箱: {'启用' if sandbox.get('enabled') else '禁用'}")
        except Exception as e:
            console.print(f"[red]解析配置失败: {e}[/red]")
    else:
        console.print("[red]没有活动项目。请使用 /new 创建项目或 /change 切换。[/red]")
