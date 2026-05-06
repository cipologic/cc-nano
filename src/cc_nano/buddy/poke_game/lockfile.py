"""跨终端单实例锁，用于挂机冒险游戏。

所有终端上同时只能运行一个 IA 会话。
使用 PID + 心跳机制检测过期的锁。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from cc_nano.core.project import get_project_root


def _get_lock_dir() -> Path:
    return get_project_root() / ".config" / "cc-nano"


def _get_lock_file() -> Path:
    return _get_lock_dir() / "ia_game.lock"


_HEARTBEAT_INTERVAL = 30  # 秒
_HEARTBEAT_TIMEOUT = 60  # 秒 — 超过此时间则认为锁已过期


def _pid_alive(pid: int) -> bool:
    """检查给定 PID 对应的进程是否仍在运行。"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_lock() -> bool:
    """尝试获取游戏锁。

    如果成功获取锁则返回 True，如果已有其他会话处于活动状态则返回 False。
    """
    _get_lock_dir().mkdir(parents=True, exist_ok=True)

    if _get_lock_file().exists():
        try:
            data = json.loads(_get_lock_file().read_text(encoding="utf-8"))
            pid = data.get("pid", -1)
            heartbeat = data.get("heartbeat", 0)
            # 检查拥有锁的进程是否仍然存活，并且心跳是否新鲜
            if _pid_alive(pid) and (time.time() - heartbeat) < _HEARTBEAT_TIMEOUT:
                return False  # 存在其他活动会话
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass  # 锁文件损坏 — 可以安全覆盖

    # 写入新锁
    lock_data = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "heartbeat": time.time(),
    }
    try:
        _get_lock_file().write_text(json.dumps(lock_data), encoding="utf-8")
    except OSError:
        return False
    return True


def release_lock() -> None:
    """释放游戏锁。"""
    try:
        if _get_lock_file().exists():
            # 仅当锁属于自己时才删除
            data = json.loads(_get_lock_file().read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                _get_lock_file().unlink(missing_ok=True)
    except (json.JSONDecodeError, OSError):
        # 尽力而为
        try:
            _get_lock_file().unlink(missing_ok=True)
        except OSError:
            pass


def update_heartbeat() -> None:
    """更新锁文件中的心跳时间戳。"""
    try:
        if not _get_lock_file().exists():
            return
        data = json.loads(_get_lock_file().read_text(encoding="utf-8"))
        if data.get("pid") == os.getpid():
            data["heartbeat"] = time.time()
            _get_lock_file().write_text(json.dumps(data), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass
