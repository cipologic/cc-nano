"""会话持久化 — 基于 JSONL 的对话存储。

每个会话对应 ``~/.config/cc-nano/sessions/{sanitized_cwd}/`` 下的一对文件：

* ``{session_id}.jsonl``  — 每条消息一个 JSON 对象
* ``{session_id}.meta.json`` — 轻量级元数据，用于快速列出会话
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cc_nano.core.project import get_project_root


def _get_sessions_root() -> Path:
    return get_project_root() / ".config" / "cc-nano" / "sessions"


# 标题生成常量
_TITLE_MAX_LEN = 80  # 标题最大长度（字符）
_TITLE_MIN_TRUNCATE_POS = 40  # 截断时保留的最小长度，若在此位置之后找到空格才截断

# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass
class SessionMeta:
    session_id: str
    title: str
    cwd: str
    model: str
    created_at: str
    updated_at: str
    message_count: int = 0
    mode: str | None = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _sanitize_cwd(cwd: str) -> str:
    """将绝对路径转换为安全的目录名。

    清理规则：将非字母数字字符替换为 ``-``，去除前导横线，
    并合并连续的横线。截断后附加一个短哈希值以避免冲突。
    """
    name = re.sub(r"[^a-zA-Z0-9]", "-", cwd)
    name = re.sub(r"-+", "-", name).strip("-")
    if len(name) > 80:
        h = hashlib.sha1(cwd.encode()).hexdigest()[:8]
        name = name[:80] + "-" + h
    return name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_content(content: Any) -> Any:
    """递归地将 Anthropic SDK 的 Pydantic 对象转换为普通字典。"""
    if content is None:
        return content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [_serialize_content(item) for item in content]
    # Anthropic SDK 的内容块是 Pydantic BaseModel 实例
    if hasattr(content, "model_dump"):
        return content.model_dump()
    if isinstance(content, dict):
        return {k: _serialize_content(v) for k, v in content.items()}
    return content


def _serialize_message(msg: dict) -> dict:
    """返回一个 JSON 安全的消息字典副本。"""
    out: dict[str, Any] = {}
    for key, val in msg.items():
        if key == "content":
            out[key] = _serialize_content(val)
        else:
            out[key] = val
    return out


def _extract_text(content: Any) -> str:
    """尽力从消息内容中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(getattr(block, "text", ""))
        return " ".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------


class SessionStore:
    """管理单个会话的 JSONL 持久化。"""

    def __init__(
        self,
        cwd: str,
        model: str,
        session_id: str | None = None,
        mode: str | None = None,
    ):
        self.session_id = session_id or uuid.uuid4().hex
        self.cwd = cwd
        self.model = model
        self.mode = mode
        self._dir = _get_sessions_root() / _sanitize_cwd(cwd)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path = self._dir / f"{self.session_id}.jsonl"
        self._meta_path = self._dir / f"{self.session_id}.meta.json"
        self._message_count = 0
        self._title: str = ""
        self._created_at: str | None = None

    # -- 写入 -----------------------------------------------------------

    def append_message(self, message: dict) -> None:
        """持久化一条消息（追加到 JSONL 文件）。"""
        safe = _serialize_message(message)
        safe["_ts"] = _now_iso()
        with open(self._jsonl_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")
        self._message_count += 1

        # 从第一条用户消息自动生成标题
        if not self._title and message.get("role") == "user":
            self._title = _generate_title(message.get("content", ""))

        self._save_meta()

    def _save_meta(self) -> None:
        now = _now_iso()
        if self._created_at is None:
            self._created_at = now
        meta = SessionMeta(
            session_id=self.session_id,
            title=self._title or "(无标题)",
            cwd=self.cwd,
            model=self.model,
            created_at=self._created_at,
            updated_at=now,
            message_count=self._message_count,
            mode=self.mode,
        )
        with open(self._meta_path, "w", encoding="utf-8") as fh:
            json.dump(asdict(meta), fh, ensure_ascii=False)

    # -- 读取（类方法） -------------------------------------------

    @classmethod
    def load_messages(cls, session_id: str, cwd: str) -> list[dict]:
        """从磁盘读取 *session_id* 的所有消息。"""
        d = _get_sessions_root() / _sanitize_cwd(cwd)
        path = d / f"{session_id}.jsonl"
        if not path.exists():
            return []
        messages: list[dict] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                obj.pop("_ts", None)
                messages.append(obj)
        return messages

    @classmethod
    def list_sessions(cls, cwd: str) -> list[SessionMeta]:
        """返回 *cwd* 下可用的会话，按最近更新时间倒序排列。"""
        d = _get_sessions_root() / _sanitize_cwd(cwd)
        if not d.exists():
            return []
        results: list[SessionMeta] = []
        for meta_file in d.glob("*.meta.json"):
            try:
                with open(meta_file, encoding="utf-8") as fh:
                    data = json.load(fh)
                results.append(SessionMeta(**data))
            except Exception:
                continue
        results.sort(key=lambda m: m.updated_at, reverse=True)
        return results

    @classmethod
    def load_session(
        cls, session_id: str, cwd: str
    ) -> tuple[SessionMeta | None, list[dict]]:
        """加载 *session_id* 的元数据和消息。"""
        d = _get_sessions_root() / _sanitize_cwd(cwd)
        meta_path = d / f"{session_id}.meta.json"
        meta = None
        if meta_path.exists():
            with open(meta_path, encoding="utf-8") as fh:
                meta = SessionMeta(**json.load(fh))
        messages = cls.load_messages(session_id, cwd)
        return meta, messages


# ---------------------------------------------------------------------------
# 标题生成
# ---------------------------------------------------------------------------


def _generate_title(content: Any) -> str:
    """从第一条用户消息生成一个简短标题。"""
    text = _extract_text(content).strip()
    if not text:
        return "(无标题)"
    # 在单词边界处截断
    if len(text) <= _TITLE_MAX_LEN:
        return text
    truncated = text[:_TITLE_MAX_LEN]
    last_space = truncated.rfind(" ")
    # 仅在最后一个空格位置 > 最小截断点时，截断到该空格处
    # 否则保留完整 80 字符后再追加省略号（适用于无空格的中文或长URL）
    if last_space > _TITLE_MIN_TRUNCATE_POS:
        truncated = truncated[:last_space]
    return truncated + "…"
