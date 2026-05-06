"""伴侣持久化 — JSON 存储于 ~/.config/cc-nano/companion.json

支持多个伴侣。JSON 结构如下：
{
  "active": 0,
  "muted": true,
  "companions": [
    {"name": "...", "personality": "...", "hatchedAt": ..., "seed": "..."},
    ...
  ]
}

旧的单伴侣格式（包含 name/personality/hatchedAt 的扁平对象）会在首次读取时自动迁移。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .types import (CompanionMood, CompanionSoul, StoredCompanion,
                    StoredCompanionWithSeed)

_CONFIG_DIR = Path.home() / ".config" / "cc-nano" / "buddy"
_COMPANION_FILE = _CONFIG_DIR / "companion.json"


def _read_data(path: Path | None = None) -> dict | None:
    """读取并返回原始 JSON 数据，若文件缺失或损坏则返回 None。"""
    fp = path or _COMPANION_FILE
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError):
        return None


def _write_data(data: dict, path: Path | None = None) -> None:
    """将数据写入 JSON 文件。"""
    fp = path or _COMPANION_FILE
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _migrate_if_needed(data: dict, default_seed: str, path: Path | None = None) -> dict:
    """将旧的扁平格式原地迁移为新的多伴侣格式。"""
    if "companions" in data:
        return data  # 已经是新格式

    # 旧格式：扁平结构 {name, personality, hatchedAt, muted?}
    if "name" not in data:
        return data

    new_data = {
        "active": 0,
        "muted": data.get("muted", True),
        "companions": [
            {
                "name": data["name"],
                "personality": data["personality"],
                "hatchedAt": data["hatchedAt"],
                "seed": default_seed,
            }
        ],
    }
    _write_data(new_data, path)
    return new_data


def _default_seed() -> str:
    """构建原始伴侣的默认种子（与 companion.py 中的逻辑一致）。"""
    from .companion import SALT, companion_user_id

    return companion_user_id() + SALT


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def load_stored_companion(path: Path | None = None) -> StoredCompanion | None:
    """从磁盘加载*当前激活的*存储伴侣，若尚未孵化则返回 None。"""
    data = _read_data(path)
    if data is None:
        return None
    try:
        data = _migrate_if_needed(data, _default_seed(), path)
        companions = data.get("companions", [])
        active = data.get("active", 0)
        if not companions or active >= len(companions):
            return None
        c = companions[active]
        return StoredCompanion(
            name=c["name"],
            personality=c["personality"],
            hatched_at=c["hatchedAt"],
        )
    except (KeyError, TypeError, IndexError):
        return None


def load_active_seed(path: Path | None = None) -> str | None:
    """加载当前激活伴侣的种子。"""
    data = _read_data(path)
    if data is None:
        return None
    try:
        data = _migrate_if_needed(data, _default_seed(), path)
        companions = data.get("companions", [])
        active = data.get("active", 0)
        if not companions or active >= len(companions):
            return None
        return companions[active].get("seed", "")
    except (KeyError, TypeError, IndexError):
        return None


def save_stored_companion(
    soul: CompanionSoul, path: Path | None = None
) -> StoredCompanion:
    """将伴侣灵魂保存到磁盘（第一个伴侣 / 初始孵化）。"""
    fp = path or _COMPANION_FILE
    seed = _default_seed()
    hatched_at = int(time.time() * 1000)
    entry = {
        "name": soul.name,
        "personality": soul.personality,
        "hatchedAt": hatched_at,
        "seed": seed,
    }

    data = _read_data(fp)
    if data and "companions" in data:
        # 追加到现有列表
        data["companions"].append(entry)
        data["active"] = len(data["companions"]) - 1
    else:
        data = {
            "active": 0,
            "muted": True,
            "companions": [entry],
        }
    _write_data(data, fp)
    return StoredCompanion(
        name=soul.name,
        personality=soul.personality,
        hatched_at=hatched_at,
    )


def save_new_companion(
    soul: CompanionSoul, seed: str, path: Path | None = None
) -> StoredCompanion:
    """向集合中添加一个新伴侣并将其设为激活状态。"""
    fp = path or _COMPANION_FILE
    hatched_at = int(time.time() * 1000)
    entry = {
        "name": soul.name,
        "personality": soul.personality,
        "hatchedAt": hatched_at,
        "seed": seed,
    }

    data = _read_data(fp)
    if data is None:
        data = {"active": 0, "muted": True, "companions": []}
    elif "companions" not in data:
        data = _migrate_if_needed(data, _default_seed(), fp)

    data["companions"].append(entry)
    data["active"] = len(data["companions"]) - 1
    _write_data(data, fp)
    return StoredCompanion(
        name=soul.name,
        personality=soul.personality,
        hatched_at=hatched_at,
    )


def load_all_stored_companions(
    path: Path | None = None,
) -> list[StoredCompanionWithSeed]:
    """加载所有存储的伴侣。"""
    data = _read_data(path)
    if data is None:
        return []
    try:
        data = _migrate_if_needed(data, _default_seed(), path)
        result = []
        for c in data.get("companions", []):
            result.append(
                StoredCompanionWithSeed(
                    name=c["name"],
                    personality=c["personality"],
                    hatched_at=c["hatchedAt"],
                    seed=c.get("seed", ""),
                )
            )
        return result
    except (KeyError, TypeError):
        return []


def load_active_index(path: Path | None = None) -> int:
    """返回当前激活伴侣的索引（从 0 开始）。"""
    data = _read_data(path)
    if data is None:
        return 0
    data = _migrate_if_needed(data, _default_seed(), path)
    return data.get("active", 0)


def save_active_index(index: int, path: Path | None = None) -> bool:
    """设置激活伴侣的索引。成功时返回 True。"""
    fp = path or _COMPANION_FILE
    data = _read_data(fp)
    if data is None:
        return False
    data = _migrate_if_needed(data, _default_seed(), fp)
    companions = data.get("companions", [])
    if index < 0 or index >= len(companions):
        return False
    data["active"] = index
    _write_data(data, fp)
    return True


def load_companion_muted(path: Path | None = None) -> bool:
    """检查伴侣反应是否被静音。"""
    data = _read_data(path)
    if data is None:
        return True
    data = _migrate_if_needed(data, _default_seed(), path)
    return bool(data.get("muted", True))


def save_companion_muted(muted: bool, path: Path | None = None) -> None:
    """切换伴侣文件中的静音标志。"""
    fp = path or _COMPANION_FILE
    data = _read_data(fp)
    if data is None:
        return
    data = _migrate_if_needed(data, _default_seed(), fp)
    data["muted"] = muted
    _write_data(data, fp)


def load_active_mood(path: Path | None = None) -> CompanionMood:
    """加载当前激活伴侣的情绪。若缺失则返回中性情绪。"""
    data = _read_data(path)
    if data is None:
        return CompanionMood()
    try:
        data = _migrate_if_needed(data, _default_seed(), path)
        companions = data.get("companions", [])
        active = data.get("active", 0)
        if not companions or active >= len(companions):
            return CompanionMood()
        mood_data = companions[active].get("mood")
        if mood_data is None:
            return CompanionMood()
        return CompanionMood.from_dict(mood_data)
    except (KeyError, TypeError, IndexError):
        return CompanionMood()


def save_active_mood(mood: CompanionMood, path: Path | None = None) -> None:
    """保存当前激活伴侣的情绪。"""
    fp = path or _COMPANION_FILE
    data = _read_data(fp)
    if data is None:
        return
    try:
        data = _migrate_if_needed(data, _default_seed(), fp)
        companions = data.get("companions", [])
        active = data.get("active", 0)
        if not companions or active >= len(companions):
            return
        companions[active]["mood"] = mood.to_dict()
        _write_data(data, fp)
    except (KeyError, TypeError, IndexError):
        pass
