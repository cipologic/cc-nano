"""沙箱配置数据类与 TOML 持久化。

对应 sandboxTypes.ts 中的 SandboxSettingsSchema（第 91-144 行）。
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SandboxFilesystemConfig:
    """文件系统限制配置。

    对应 SandboxFilesystemConfigSchema（sandboxTypes.ts 第 50-80 行）。
    """

    allow_write: list[str] = field(default_factory=lambda: ["."])
    deny_write: list[str] = field(default_factory=list)
    deny_read: list[str] = field(default_factory=list)
    allow_read: list[str] = field(default_factory=list)


@dataclass
class SandboxConfig:
    """顶层沙箱配置。

    对应 SandboxSettings（sandboxTypes.ts 第 91-144 行）。
    字段：
    - enabled: 沙箱是否启用
    - auto_allow_bash: 自动批准沙箱中的 bash 命令（autoAllowBashIfSandboxed）
    - allow_unsandboxed: 当沙箱失败时允许回退（allowUnsandboxedCommands）
    - excluded_commands: 跳过沙箱的命令模式列表
    - filesystem: 文件系统限制
    - unshare_net: 隔离网络命名空间
    """

    enabled: bool = False
    auto_allow_bash: bool = False
    allow_unsandboxed: bool = False
    excluded_commands: list[str] = field(default_factory=list)
    filesystem: SandboxFilesystemConfig = field(default_factory=SandboxFilesystemConfig)
    unshare_net: bool = True


def load_sandbox_config(
    config_paths: tuple[Path, ...] = (),
) -> SandboxConfig:
    """从项目根目录的 .cc-nano.toml 中加载沙箱配置。"""
    if not config_paths:
        from cc_nano.core.project import get_project_root

        config_paths = (get_project_root() / ".cc-nano.toml",)
    merged: dict[str, Any] = {}

    for p in config_paths:
        if not p.exists():
            continue
        try:
            with p.open("rb") as fh:
                data = tomllib.load(fh)
        except (tomllib.TOMLDecodeError, OSError):
            continue
        sandbox_section = data.get("sandbox")
        if isinstance(sandbox_section, dict):
            merged.update(sandbox_section)

    return _dict_to_config(merged)


def save_sandbox_config(config: SandboxConfig, path: Path) -> None:
    """将沙箱配置保存到 TOML 的 [sandbox] 节。

    对应 setSandboxSettings（sandbox-adapter.ts 第 669-691 行）。
    仅更新 [sandbox]，通过按行手术保留其他内容。
    """
    sandbox_dict = _config_to_dict(config)
    sandbox_lines = _render_sandbox_section(sandbox_dict)

    if path.exists():
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            original = ""
    else:
        original = ""

    new_content = _replace_sandbox_section(original, sandbox_lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8")


def _dict_to_config(d: dict[str, Any]) -> SandboxConfig:
    """将扁平/嵌套字典转换为 SandboxConfig。"""
    fs_raw = d.get("filesystem", {})
    fs = SandboxFilesystemConfig(
        allow_write=fs_raw.get("allow_write", ["."]),
        deny_write=fs_raw.get("deny_write", []),
        deny_read=fs_raw.get("deny_read", []),
        allow_read=fs_raw.get("allow_read", []),
    )
    return SandboxConfig(
        enabled=bool(d.get("enabled", False)),
        auto_allow_bash=bool(d.get("auto_allow_bash", False)),
        allow_unsandboxed=bool(d.get("allow_unsandboxed", False)),
        excluded_commands=list(d.get("excluded_commands", [])),
        filesystem=fs,
        unshare_net=bool(d.get("unshare_net", True)),
    )


def _config_to_dict(config: SandboxConfig) -> dict[str, Any]:
    """将 SandboxConfig 转换为可序列化的字典。"""
    return {
        "enabled": config.enabled,
        "auto_allow_bash": config.auto_allow_bash,
        "allow_unsandboxed": config.allow_unsandboxed,
        "excluded_commands": config.excluded_commands,
        "unshare_net": config.unshare_net,
        "filesystem": {
            "allow_write": config.filesystem.allow_write,
            "deny_write": config.filesystem.deny_write,
            "deny_read": config.filesystem.deny_read,
            "allow_read": config.filesystem.allow_read,
        },
    }


import re


def _render_sandbox_section(sandbox_dict: dict[str, Any]) -> str:
    """将 [sandbox] 和 [sandbox.filesystem] 渲染为 TOML 文本。"""
    lines: list[str] = ["[sandbox]"]
    for key, val in sandbox_dict.items():
        if isinstance(val, dict):
            continue
        lines.append(_format_kv(key, val))
    fs = sandbox_dict.get("filesystem")
    if isinstance(fs, dict):
        lines.append("")
        lines.append("[sandbox.filesystem]")
        for key, val in fs.items():
            lines.append(_format_kv(key, val))
    return "\n".join(lines) + "\n"


def _replace_sandbox_section(original: str, new_section: str) -> str:
    """替换或追加 TOML 文本中的 [sandbox] 块，保留其他所有内容。

    使用基于行的解析：删除属于 [sandbox] 或 [sandbox.*] 节的所有行，
    然后在原位置插入新节。
    """
    if not original.strip():
        return new_section

    _HEADER_RE = re.compile(r"^\[(.+)\]\s*$")

    lines = original.splitlines(keepends=True)
    kept: list[str] = []
    insert_pos: int | None = None
    in_sandbox = False

    for line in lines:
        m = _HEADER_RE.match(line)
        if m:
            header_name = m.group(1).strip()
            if header_name == "sandbox" or header_name.startswith("sandbox."):
                in_sandbox = True
                if insert_pos is None:
                    insert_pos = len(kept)
                continue
            else:
                in_sandbox = False

        if in_sandbox:
            continue

        kept.append(line)

    # 构建结果：插入点之前的保留行 + 新节 + 插入点之后的保留行
    if insert_pos is not None:
        before = "".join(kept[:insert_pos]).rstrip("\n")
        after = "".join(kept[insert_pos:]).lstrip("\n")
        parts = [p for p in (before, new_section.strip(), after) if p]
        return "\n\n".join(parts) + "\n"
    else:
        # 没有已存在的 [sandbox] —— 追加
        return original.rstrip("\n") + "\n\n" + new_section


def _format_kv(key: str, val: Any) -> str:
    """格式化单个 TOML key = value 对。"""
    if isinstance(val, bool):
        return f"{key} = {'true' if val else 'false'}"
    if isinstance(val, int):
        return f"{key} = {val}"
    if isinstance(val, float):
        return f"{key} = {val}"
    if isinstance(val, str):
        return f'{key} = "{val}"'
    if isinstance(val, list):
        items = ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in val)
        return f"{key} = [{items}]"
    return f'{key} = "{val}"'
