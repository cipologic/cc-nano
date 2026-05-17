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
import sys
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

    # === 确保当前 Python 解释器的标准库在沙箱内可访问 ===
    # 在 WSL2 等环境中，--ro-bind / / 可能无法正确映射 pyenv 等非系统路径，
    # 导致 Python 解释器找不到 encodings 等标准库模块。
    # 从当前解释器的可执行文件路径反向推导真实的标准库位置（优于 sys.base_prefix，
    # 因为 pyenv 编译的 Python 可能因 platlibdir 与安装路径不一致而导致 base_prefix 错误）。
    _bind_python_stdlib(args)

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


def _bind_python_stdlib(args: list[str]) -> None:
    """将当前 Python 解释器的标准库路径添加到 bwrap 绑定参数中。

    pyenv 编译的 Python 有时会出现 platlibdir（如 lib64）与实际安装目录（如 lib）
    不匹配的情况，导致解释器在 bwrap 沙箱内因 --ro-bind / / 的映射异常而无法找到
    encodings 等标准库模块。此函数通过从 sys.executable 的 realpath 反向推导
    真实的标准库路径，并将其显式绑定到沙箱中。
    """
    try:
        real_exe = os.path.realpath(sys.executable)
        prefix = str(Path(real_exe).parent.parent)
        if os.path.exists(prefix) and prefix != "/":
            # 绑定 lib/ 下的标准库（常见的安装位置）
            lib_path = os.path.join(prefix, "lib")
            if os.path.exists(lib_path):
                args.extend(["--ro-bind", lib_path, lib_path])
            # 部分系统使用 lib64/，也一并绑定（如已通过 lib 绑定覆盖则无影响）
            lib64_path = os.path.join(prefix, "lib64")
            if os.path.exists(lib64_path):
                args.extend(["--ro-bind", lib64_path, lib64_path])
            # 绑定可执行文件所在 bin/ 目录
            bin_path = os.path.join(prefix, "bin")
            if os.path.exists(bin_path):
                args.extend(["--ro-bind", bin_path, bin_path])
    except Exception:
        pass  # 静默失败——绑定是优化而非必需


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
