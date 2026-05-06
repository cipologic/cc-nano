"""KAIROS 记忆系统 —— 仅追加的每日日志、梦境整合、会话持久化。"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from cc_nano.core.project import get_project_root

MAX_MEMORY_INDEX_CHARS = 10_000
MAX_ENTRYPOINT_LINES = 200
ENTRYPOINT_NAME = "MEMORY.md"

LOCK_FILE_NAME = ".consolidate-lock"
HOLDER_STALE_S = 3600  # 1 小时 —— 超过此时间后可回收锁
_LAST_CONSOLIDATED_NAME = ".last-consolidated"

SESSION_SCAN_INTERVAL_S = 600  # 10 分钟 —— 扫描限流间隔
# 模块级扫描限流状态（镜像 TS 中 initAutoDream 的闭包实现）
_last_session_scan_at: float = 0.0


# 使用函数获取项目根
def _get_memory_dir() -> Path:
    return get_project_root() / ".config" / "cc-nano" / "memory"


def _get_sessions_dir() -> Path:
    return get_project_root() / ".config" / "cc-nano" / "sessions"


# ---------------------------------------------------------------------------
# 目录辅助函数
# ---------------------------------------------------------------------------


def ensure_memory_dir(memory_dir: Optional[Path]) -> None:
    """若 memory_dir 及其下的 logs 目录不存在则创建。"""
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "logs").mkdir(parents=True, exist_ok=True)
    migrate_lock_files(memory_dir)  # 迁移旧锁


def daily_log_path(memory_dir: Path, today: date | None = None) -> Path:
    """返回 memory_dir/logs/YYYY/MM/YYYY-MM-DD.md 路径，并自动创建父目录。"""
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    today = today or date.today()
    path = (
        memory_dir
        / "logs"
        / str(today.year)
        / f"{today.month:02d}"
        / f"{today.isoformat()}.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_to_daily_log(memory_dir: Path, entry: str) -> None:
    """向今日的每日日志中追加一条带时间戳的记录。"""
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    path = daily_log_path(memory_dir)
    timestamp = datetime.now().strftime("%H:%M")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- [{timestamp}] {entry}\n")


# ---------------------------------------------------------------------------
# 记忆索引
# ---------------------------------------------------------------------------


def load_memory_index(memory_dir: Path) -> str:
    """读取 MEMORY.md，截断至 MAX_MEMORY_INDEX_CHARS 字符。若文件不存在则返回 ''。"""
    path = memory_dir / "MEMORY.md"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:MAX_MEMORY_INDEX_CHARS]
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# 整合锁（镜像 autoDream/consolidationLock.ts）
# 锁文件的 mtime = 上次整合时间。文件内容 = 持有锁的进程 PID。
# ---------------------------------------------------------------------------


def _get_lock_path(memory_dir: Path) -> Path:
    """互斥锁文件路径（仅用于互斥）"""
    return memory_dir / LOCK_FILE_NAME


def _get_timestamp_path(memory_dir: Path) -> Path:
    """上次整合时间戳文件路径"""
    return memory_dir / _LAST_CONSOLIDATED_NAME


def read_last_consolidated_at(memory_dir: Path) -> float:
    """返回上次整合的 Unix 时间戳（秒），从未整合过则返回 0。"""
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    ts_path = _get_timestamp_path(memory_dir)
    try:
        return ts_path.stat().st_mtime
    except OSError:
        return 0.0


def try_acquire_lock(memory_dir: Path) -> bool:
    """尝试获取互斥锁，成功返回 True。使用原子创建 + PID 存活检查回收。"""
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    lock_path = _get_lock_path(memory_dir)
    my_pid = os.getpid()

    # 尝试原子创建
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w") as f:
            f.write(str(my_pid))
        return True
    except FileExistsError:
        pass

    # 锁文件存在，检查是否可回收
    try:
        data = lock_path.read_text().strip()
        holder_pid = int(data)
    except (OSError, ValueError):
        # 文件损坏或无法读取，删除后重试
        try:
            lock_path.unlink()
        except OSError:
            pass
        return try_acquire_lock(memory_dir)

    # 检查持有者是否存活且锁未过期
    try:
        os.kill(holder_pid, 0)
        # 进程存活，检查锁 age
        age = time.time() - lock_path.stat().st_mtime
        if age < HOLDER_STALE_S:
            return False
        # 锁已过期，可以回收
    except OSError:
        # 进程不存在，回收
        pass

    # 回收旧锁：删除后重新创建
    try:
        lock_path.unlink()
    except OSError:
        pass
    return try_acquire_lock(memory_dir)


def release_lock(memory_dir: Path) -> None:
    """释放互斥锁（删除锁文件）。"""
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    lock_path = _get_lock_path(memory_dir)
    try:
        lock_path.unlink()
    except OSError:
        pass


def record_consolidation(memory_dir: Path) -> None:
    """记录整合完成：更新时间戳文件的 mtime"""
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    ts_path = _get_timestamp_path(memory_dir)
    try:
        # 确保文件存在
        ts_path.touch()
        now = time.time()
        os.utime(ts_path, (now, now))
    except OSError:
        pass


def migrate_lock_files(memory_dir: Path) -> None:
    """迁移旧锁文件：若存在旧锁文件且无时间戳文件，则从旧锁文件的 mtime 初始化时间戳文件。"""
    lock_path = _get_lock_path(memory_dir)
    ts_path = _get_timestamp_path(memory_dir)
    if lock_path.exists() and not ts_path.exists():
        try:
            mtime = lock_path.stat().st_mtime
            ts_path.touch()
            os.utime(ts_path, (mtime, mtime))
        except OSError:
            pass


def count_sessions_since(since_ts: float) -> int:
    """统计 mtime 大于 since_ts 的会话文件数量。"""
    if not _get_sessions_dir().exists():
        return 0
    count = 0
    for f in _get_sessions_dir().iterdir():
        if f.suffix == ".jsonl" and f.stat().st_mtime > since_ts:
            count += 1
    return count


def should_auto_dream(
    memory_dir: Path,
    min_hours: float,
    min_sessions: int,
    current_session_id: str,
    sessions_dir: Path | None = None,
) -> bool:
    """检查所有条件：时间 ≥ min_hours 且 新会话数 ≥ min_sessions。

    包含 10 分钟的扫描限流（镜像 TS 中的 SESSION_SCAN_INTERVAL_MS），
    避免在时间条件已满足但会话数条件未满足时，每轮对话都重复扫描会话目录。
    """
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    if sessions_dir is None:
        sessions_dir = _get_sessions_dir()
    global _last_session_scan_at

    last = read_last_consolidated_at(memory_dir)
    now = datetime.now().timestamp()
    hours_since = (now - last) / 3600 if last > 0 else float("inf")

    if hours_since < min_hours:
        return False

    # 扫描限流：若距离上次扫描不足 10 分钟，则跳过会话计数
    if now - _last_session_scan_at < SESSION_SCAN_INTERVAL_S:
        return False
    _last_session_scan_at = now

    # 统计比上次整合更新的会话，排除当前会话
    scan_dir = sessions_dir or _get_sessions_dir()
    count = 0
    if scan_dir.exists():
        for f in scan_dir.iterdir():
            if (
                f.suffix == ".jsonl"
                and current_session_id not in f.name
                and f.stat().st_mtime > last
            ):
                count += 1

    return count >= min_sessions


def list_sessions_since(
    since_ts: float, sessions_dir: Path | None = None, current_session_id: str = ""
) -> list[str]:
    """返回自 since_ts 之后被修改过的会话 ID（不含 .jsonl 后缀）。"""
    if sessions_dir is None:
        sessions_dir = _get_sessions_dir()
    scan_dir = sessions_dir or _get_sessions_dir()
    result: list[str] = []
    if not scan_dir.exists():
        return result
    for f in scan_dir.iterdir():
        if (
            f.suffix == ".jsonl"
            and current_session_id not in f.name
            and f.stat().st_mtime > since_ts
        ):
            result.append(f.stem)
    return result


# ---------------------------------------------------------------------------
# <memory> 标签提取
# ---------------------------------------------------------------------------


def extract_memory_tags(text: str) -> list[str]:
    """提取文本中所有 <memory>...</memory> 标签内的内容。"""
    return [m.strip() for m in re.findall(r"<memory>(.*?)</memory>", text, re.DOTALL)]


# ---------------------------------------------------------------------------
# 系统提示中的记忆章节
# ---------------------------------------------------------------------------


def build_memory_system_section(memory_dir: Path) -> str:
    """返回记忆指令 + MEMORY.md 内容，用于系统提示。

    包含四种记忆类型、前置元数据格式、不应保存的内容规则以及过时警告。
    """
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    index = load_memory_index(memory_dir)

    section = f"""\

# 自动记忆

你有一个持久化的、基于文件的记忆系统，位于 `{memory_dir}/`。
该目录已经存在 —— 请直接使用 Write 工具写入（不要运行 mkdir 或检查其存在性）。

你应该随着时间的推移构建这个记忆系统，以便未来的对话能够完整了解：
- 用户是谁
- 他们希望如何与你协作
- 哪些行为需要避免或重复
- 用户交给你的工作背后的上下文

如果用户明确要求你记住某件事，请立即保存它，并选择最合适的记忆类型。
如果用户要求你忘记某件事，请找到并删除相关条目。

## 记忆类型

共有四种离散的记忆类型：

### user（用户）
关于用户的角色、目标、职责和知识的信息。优秀的用户记忆能帮助你根据用户的偏好调整未来行为。
**何时保存：** 当你了解到用户的角色、偏好、职责或知识等细节时。

### feedback（反馈）
用户给予你的指导或纠正。这些非常重要 —— 它们让你能够在不同会话之间保持一致和响应性。如果没有这些反馈，你会重复同样的错误。
**何时保存：** 任何时候用户纠正了你的方法，且该纠正适用于未来的对话（例如“不要模拟数据库”、“停止做总结”）。
**内容结构：** 先写规则，然后一行 **Why：（为什么）**，再一行 **How to apply：（如何应用）**。

### project（项目）
关于进行中的工作、目标、计划、缺陷或事件的信息，且这些信息无法从代码或 git 历史中推导出来。
**何时保存：** 当你了解到谁在做什么、为什么做、或截止日期时。始终将相对日期转换为绝对日期。
**内容结构：** 先陈述事实/决策，然后写 **Why：** 和 **How to apply：** 行。

### reference（参考）
指向信息在外部系统中存放位置的指针。
**何时保存：** 当你了解到某个资源及其用途时（例如“缺陷在 Linear 项目 INGEST 中跟踪”）。

## 不应保存的内容
- 代码模式、架构、文件路径 —— 这些可以通过阅读项目获得
- Git 历史、近期变更 —— `git log` / `git blame` 才是权威来源
- 调试解决方案 —— 修复已在代码中，提交信息提供了上下文
- 任何已在 CHARTER.md 文件中记录的内容
- 临时的任务细节或当前对话的上下文

## 如何保存记忆

**方式 A —— <memory> 标签（快速记录）：**
在回复中用 `<memory>...</memory>` 标签包裹文本。这些内容会被自动提取并追加到每日日志中。

**方式 B —— 直接写入文件（结构化记忆）：**
向 `{memory_dir}/` 写入一个 `.md` 文件，并包含以下前置元数据：

```markdown
---
name: {{{{记忆名称}}}}
description: {{{{一行描述 —— 后续用于判断相关性}}}}
type: {{{{user | feedback | project | reference}}}}
---

{{{{记忆内容}}}}
```

然后将指向该文件的链接添加到 `{memory_dir}/MEMORY.md` 中。
MEMORY.md 是索引，不是记忆本身 —— 它只应包含带简短描述的链接。请保持它在 200 行以内。

## 何时访问记忆
- 当已知的特定记忆似乎与当前任务相关时
- 当用户似乎在引用之前对话中的工作时
- 当用户明确要求你回忆或记住某事时，你必须访问记忆

## 斜杠命令
- `/dream` —— 将每日日志整合为主题文件并更新 MEMORY.md
- `/remember <文本>` —— 手动向每日日志追加一条记录
- `/memory` —— 打印当前 MEMORY.md 的内容
"""

    if index:
        section += f"\n## 当前记忆索引（MEMORY.md）\n{index}\n"
    else:
        section += "\n尚未整合任何记忆。\n"

    return section


# ---------------------------------------------------------------------------
# Dream 整合提示词
# ---------------------------------------------------------------------------


def build_dream_prompt(
    memory_dir: Path, transcript_dir: str = "", session_ids: list[str] | None = None
) -> str:
    """
    为 dream 代理构建四阶段整合提示词。
    """
    extra_parts: list[str] = []

    # 工具约束说明（镜像 TS：仅在 auto-dream 中添加为 extra）
    extra_parts.append(
        "**本次运行的工具约束：** Bash 不可用。Edit 和 Write 仅限写入记忆目录内的文件。"
        "Read、Grep、Glob 不受限制。请在规划探索时牢记这一点。"
    )

    if session_ids:
        # 构建带完整路径的会话列表
        session_lines = []
        for sid in session_ids:
            if transcript_dir:
                full_path = f"{transcript_dir}/{sid}.jsonl"
                session_lines.append(f"- {sid} ({full_path})")
            else:
                session_lines.append(f"- {sid} (会话文件目录未知)")
        extra_parts.append(
            f"上次整合以来的会话（共 {len(session_ids)} 个）：\n"
            + "\n".join(session_lines)
        )

    extra = "\n\n".join(extra_parts)
    extra_section = f"\n\n## 额外上下文\n\n{extra}" if extra else ""

    transcript_line = ""
    if transcript_dir:
        transcript_line = (
            f"\n会话转录文件目录：`{transcript_dir}` "
            "（大型 JSONL 文件 —— 请使用 grep 精确搜索，不要读取整个文件）\n"
        )

    return f"""\
# Dream：记忆整合

你正在进行一次梦 —— 对你记忆文件的反思性整理。请将你最近学到的东西综合成持久、组织良好的记忆，
以便未来的会话能够快速定位方向。

记忆目录：`{memory_dir}`
该目录已经存在 —— 请直接使用 Write 工具写入（不要运行 mkdir 或检查其存在性）。
{transcript_line}
---

## 第一阶段 —— 定位

- 使用 Glob 列出 `{memory_dir}/` 下的所有文件，了解已有内容
- 读取 `{ENTRYPOINT_NAME}`，理解当前索引
- 浏览现有的主题文件，以便改进它们而不是创建重复内容
- 如果存在 `logs/` 或 `sessions/` 子目录，请查看其中的近期记录

## 第二阶段 —— 收集近期信号

寻找值得持久化的新信息。按大致优先级排序的来源：

1. **每日日志**（`logs/YYYY/MM/YYYY-MM-DD.md`）—— 如果存在，这是仅追加的原始流
2. **已过时的现有记忆** —— 与当前代码库中观察到的事实相矛盾的记忆
3. **转录文件搜索** —— 如果你需要特定上下文（例如“昨天构建失败的错误信息是什么？”），
   请使用 grep 在 JSONL 转录文件中精确搜索窄词：
   `grep -rn "<窄词>" {transcript_dir}/ --include="*.jsonl" | tail -50`

不要穷举读取转录文件。只查找你已经怀疑可能重要的内容。

## 第三阶段 —— 整合

对于每件值得记住的事情，在记忆目录的顶层写入或更新一个记忆文件。
请使用系统提示中自动记忆章节所规定的记忆文件格式和类型约定 —— 那里是“保存什么、如何结构化、不保存什么”的权威来源。

重点关注：
- 将新信号合并到现有主题文件中，而不是创建几乎重复的新文件
- 将相对日期（“昨天”、“上周”）转换为绝对日期，以便时间流逝后仍然可解释
- 删除已被证伪的事实 —— 如果今天的调查推翻了一条旧记忆，请直接在源头修正它

## 第四阶段 —— 精简与索引

更新 `{ENTRYPOINT_NAME}`，使其保持在 {MAX_ENTRYPOINT_LINES} 行以内且小于约 25KB。
它是一个**索引**，而不是转储 —— 每个条目应该是一行，不超过约 150 个字符：
`- [标题](file.md) —— 一行钩子`。永远不要直接将记忆内容写入它。

- 删除指向已过时、错误或被取代记忆的指针
- 降级冗长条目：如果某行超过约 200 个字符，说明它携带了本应属于主题文件的内容 —— 缩短该行，将细节移至主题文件
- 添加指向新重要记忆的指针
- 解决矛盾 —— 如果两个文件不一致，修正错误的那一个

---

返回一个简要总结，说明你整合、更新或精简了什么。如果没有变化（记忆已经很紧凑），请直接说明。{extra_section}"""


# ---------------------------------------------------------------------------
# 会话持久化（JSONL）
# ---------------------------------------------------------------------------


def save_session(messages: list[dict], session_id: str) -> None:
    """将消息序列化为 JSONL，并更新 last-session 符号链接。"""
    _get_sessions_dir().mkdir(parents=True, exist_ok=True)
    path = _get_sessions_dir() / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(serialize_message(msg), default=str) + "\n")

    # 更新符号链接
    link = _get_sessions_dir() / "last-session"
    link.unlink(missing_ok=True)
    link.symlink_to(path.name)


def load_session(session_id: str | None = None) -> list[dict] | None:
    """从 JSONL 加载消息。若不提供 ID，则跟随 last-session 符号链接。"""
    _get_sessions_dir().mkdir(parents=True, exist_ok=True)

    if session_id:
        path = _get_sessions_dir() / f"{session_id}.jsonl"
    else:
        link = _get_sessions_dir() / "last-session"
        if not link.exists():
            return None
        path = _get_sessions_dir() / link.resolve().name

    if not path.exists():
        return None

    messages = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(json.loads(line))
    return messages or None


def serialize_message(msg: dict) -> dict:
    """同时处理 Anthropic SDK 对象（.model_dump()）和普通字典。"""
    content = msg.get("content")
    if content is None:
        return dict(msg)

    if isinstance(content, list):
        serialized = []
        for item in content:
            if hasattr(item, "model_dump"):
                serialized.append(item.model_dump())
            elif isinstance(item, dict):
                serialized.append(item)
            else:
                # ContentBlock 或类似对象 —— 尝试转换
                serialized.append({"type": "text", "text": str(item)})
        return {"role": msg["role"], "content": serialized}

    return dict(msg)
