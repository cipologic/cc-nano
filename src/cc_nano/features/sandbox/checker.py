"""检测沙箱运行所需的系统依赖项。

对应 sandbox-adapter.ts 中的 checkDependencies()（第 451-457 行）
以及 SandboxDependenciesTab.tsx 中的显示逻辑。
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DependencyCheck:
    """依赖项检查结果。

    对应 SandboxDependencyCheck 类型。
    errors: 致命问题（沙箱无法运行）
    warnings: 非致命问题（可降级运行）
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def check_dependencies() -> DependencyCheck:
    """检查沙箱依赖项是否满足。

    检查项：
    1. 平台 —— 仅支持 Linux
    2. bwrap（bubblewrap）—— 必需
    3. 用户命名空间支持 —— 必需（某些内核/容器会禁用）
    4. bwrap 运行时测试
    """
    result = DependencyCheck()

    # 0. 平台检查
    if platform.system() != "Linux":
        result.errors.append(f"沙箱需要 Linux 系统（当前系统：{platform.system()}）")
        return result

    # 1. bwrap 二进制文件
    if not shutil.which("bwrap"):
        result.errors.append(
            "未找到 bubblewrap（bwrap）。请安装：apt install bubblewrap"
        )
        return result

    # 2. 用户命名空间支持
    userns_path = Path("/proc/sys/kernel/unprivileged_userns_clone")
    try:
        val = userns_path.read_text().strip()
        if val == "0":
            result.errors.append(
                "用户命名空间已禁用（unprivileged_userns_clone=0）。"
                "沙箱需要用户命名空间支持。"
            )
            return result
    except OSError:
        pass  # 文件不存在表示内核默认允许

    # 3. 实际的 bwrap 运行时测试
    try:
        proc = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--", "/bin/true"],
            capture_output=True,
            timeout=5,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            result.errors.append(f"bwrap 测试失败：{stderr}")
    except subprocess.TimeoutExpired:
        result.errors.append("bwrap 测试超时")
    except (FileNotFoundError, OSError) as e:
        result.errors.append(f"bwrap 测试失败：{e}")

    return result
