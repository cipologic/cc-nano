"""生成 bwrap 命令行，用于在沙盒中包装用户命令。

bwrap 主要参数：
- --ro-bind src dest : 只读挂载
- --bind src dest    : 读写挂载
- --dev /dev         : 最小化的 /dev
- --proc /proc       : 挂载 /proc
- --tmpfs /tmp       : 临时文件系统
- --unshare-net      : 隔离网络命名空间
- --die-with-parent  : 父进程退出时终止子进程
- -- /bin/sh -c CMD  : 在沙盒中执行命令
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from .config import SandboxConfig


def build_bwrap_args(
    command: str,
    config: SandboxConfig,
    cwd: str | None = None,
) -> list[str]:
    """根据配置构建完整的 bwrap 参数列表。

    返回的列表可以直接传递给 subprocess.run()。

    挂载顺序很重要：bwrap 按顺序处理参数，后面的会覆盖前面的。
    策略：--ro-bind / / 全局只读 -> --bind 用于可写访问 -> --ro-bind 保护特定文件
    """
    cwd = cwd or os.getcwd()
    args = ["bwrap"]

    # === 基础挂载 ===
    args.extend(["--ro-bind", "/", "/"])  # 全局只读
    args.extend(["--dev", "/dev"])  # 最小化的 /dev
    args.extend(["--proc", "/proc"])  # /proc
    args.extend(["--tmpfs", "/tmp"])  # 临时文件系统

    # === 可写目录 (bind) ===
    # 注意：这些 bind 挂载后，后续的 ro-bind（deny_write）可以覆盖子路径，使其只读。
    # 因此 deny_write 的 --ro-bind 必须放在 allow_write 的 --bind 之后。
    fs = config.filesystem
    allow_resolved = _resolve_paths(fs.allow_write, cwd)
    for write_path in allow_resolved:
        if os.path.exists(write_path):
            args.extend(["--bind", write_path, write_path])

    # === 禁止写入（即使处于 allow_write 范围内也强制只读） ===
    # 这些 --ro-bind 挂载必须放置在对应的 --bind 之后，以覆盖可写子目录。
    # 如果 deny_path 同时出现在 allow_write 中，后面的 --ro-bind 会覆盖前面的 --bind，
    # 从而实现子目录只读、父目录可写的预期行为。
    deny_resolved = _resolve_paths(fs.deny_write, cwd)
    for deny_path in deny_resolved:
        if os.path.exists(deny_path):
            args.extend(["--ro-bind", deny_path, deny_path])

    # === 禁止读取（用空 tmpfs 覆盖） ===
    for deny_path in _resolve_paths(fs.deny_read, cwd):
        if os.path.exists(deny_path):
            args.extend(["--tmpfs", deny_path])

    # === 工作目录 ===
    args.extend(["--bind", cwd, cwd])
    args.extend(["--chdir", cwd])

    # === 网络隔离 ===
    if config.unshare_net:
        args.append("--unshare-net")

    # === 安全选项 ===
    args.append("--die-with-parent")
    args.append("--unshare-pid")

    # === 设置文件保护 ===
    # 对应 sandbox-adapter.ts:230-236
    for protected in _get_protected_paths(cwd):
        if os.path.exists(protected):
            args.extend(["--ro-bind", protected, protected])

    # === 执行命令 ===
    args.extend(["--", "/bin/sh", "-c", command])

    return args


def wrap_command(
    command: str,
    config: SandboxConfig,
    cwd: str | None = None,
) -> str:
    """将命令包装为 bwrap 沙盒命令字符串。

    对应 wrapWithSandbox (sandbox-adapter.ts:704-725)。
    返回适用于 shell=True 执行的字符串。
    """
    bwrap_args = build_bwrap_args(command, config, cwd)
    return " ".join(shlex.quote(a) for a in bwrap_args)


def _resolve_paths(patterns: list[str], cwd: str) -> list[str]:
    """将路径模式解析为绝对路径。

    规则（对应 resolveSandboxFilesystemPath）：
    - "."  -> 当前工作目录
    - "~/" -> 用户主目录
    - "/" 前缀 -> 绝对路径
    - 其他 -> 相对于当前工作目录
    """
    resolved = []
    for p in patterns:
        if p == ".":
            resolved.append(cwd)
        elif p.startswith("~/"):
            resolved.append(str(Path.home() / p[2:]))
        elif p.startswith("/"):
            resolved.append(p)
        else:
            resolved.append(str(Path(cwd) / p))
    return resolved


def _get_protected_paths(cwd: str) -> list[str]:
    """返回沙盒内部必须进行只读保护的路径。

    - .cc-nano.toml（项目配置）
    - ~/.config/cc-nano/config.toml（全局配置）
    - CC-NANO-PROJECT-CHARTER.md（沙盒不应修改）
    """
    from cc_nano.core.project import find_project_root

    project_root = find_project_root(Path(cwd))
    if project_root is None:
        # 回退：使用 cwd
        project_root = Path(cwd)
    paths = []
    local_config = project_root / ".cc-nano.toml"
    if local_config.exists():
        paths.append(str(local_config))
    charter_md = project_root / "CC-NANO-PROJECT-CHARTER.md"
    if charter_md.exists():
        paths.append(str(charter_md))
    return paths
