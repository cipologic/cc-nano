"""TUI 的 Shell 执行与沙盒命令相关功能。"""

from __future__ import annotations

import subprocess

from rich.console import Console

from cc_nano.features.sandbox.manager import SandboxManager


def run_shell(cmd: str, console: Console) -> None:
    """执行一条 Shell 命令并打印输出。"""
    console.print(f"[dim]$ {cmd}[/dim]")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.stdout:
            console.print(result.stdout, end="", markup=False)
        if result.returncode != 0:
            console.print(f"[red][退出 {result.returncode}][/red]")
    except Exception as exc:
        console.print(f"[red]错误：{exc}[/red]")


def handle_sandbox_command(user_input: str, mgr: SandboxManager, con: Console) -> None:
    """处理 /sandbox REPL 命令。

    对应 commands/sandbox-toggle/sandbox-toggle.tsx。

    子命令：
    - /sandbox           -- 交互式设置
    - /sandbox status    -- 显示当前状态
    - /sandbox exclude <pattern> -- 添加排除的命令模式
    - /sandbox mode <auto-allow|regular|disabled> -- 设置模式
    """
    parts = user_input.strip().split(maxsplit=2)
    subcmd = parts[1] if len(parts) > 1 else ""

    if subcmd == "status" or subcmd == "":
        show_sandbox_status(mgr, con)
    elif subcmd == "exclude" and len(parts) > 2:
        pattern = parts[2].strip("\"'")
        msg = mgr.add_excluded_command(pattern)
        mgr.save()
        con.print(f"[green]{msg}[/green]")
    elif subcmd == "mode" and len(parts) > 2:
        msg = mgr.set_mode(parts[2])
        mgr.save()
        con.print(f"[green]{msg}[/green]")
    else:
        interactive_sandbox_setup(mgr, con)


def show_sandbox_status(mgr: SandboxManager, con: Console) -> None:
    """显示沙盒状态。对应 SandboxConfigTab + SandboxDependenciesTab。"""
    dep = mgr.check_dependencies()
    # 根据当前状态生成显示用的模式名称（中文）
    if mgr.is_auto_allow():
        mode_display = "自动允许"
    elif mgr.config.enabled:
        mode_display = "常规"
    else:
        mode_display = "禁用"
    con.print("[bold]沙盒状态[/bold]")
    con.print(f"  模式：[cyan]{mode_display}[/cyan]")
    con.print(f"  已启用：{'是' if mgr.is_enabled() else '否'}")
    con.print(f"  网络隔离：{'是' if mgr.config.unshare_net else '否'}")
    if dep.errors:
        con.print("[bold red]依赖错误：[/bold red]")
        for e in dep.errors:
            con.print(f"  [red]{e}[/red]")
    if dep.warnings:
        for w in dep.warnings:
            con.print(f"  [yellow]{w}[/yellow]")
    if mgr.config.excluded_commands:
        con.print("[bold]排除的命令：[/bold]")
        for cmd in mgr.config.excluded_commands:
            con.print(f"  - {cmd}")


def interactive_sandbox_setup(mgr: SandboxManager, con: Console) -> None:
    """交互式三选一模式设置。对应 SandboxModeTab Select。"""
    dep = mgr.check_dependencies()
    if dep.errors:
        con.print("[bold red]无法启用沙盒：[/bold red]")
        for e in dep.errors:
            con.print(f"  [red]{e}[/red]")
        return

    con.print("[bold]配置沙盒模式：[/bold]")
    con.print("  [1] 自动允许 -- Bash 命令在沙盒中自动批准执行")
    con.print("  [2] 常规    -- Bash 命令仍需确认")
    con.print("  [3] 禁用    -- 不使用沙盒")
    choice = input("  选择 [1/2/3]: ").strip()
    mode_map = {"1": "auto-allow", "2": "regular", "3": "disabled"}
    mode = mode_map.get(choice)
    if mode:
        msg = mgr.set_mode(mode)
        mgr.save()
        con.print(f"[green]{msg}[/green]")
    else:
        con.print("[dim]已取消[/dim]")
