"""WorkerManager — 生成并跟踪后台工作引擎。

每个工作引擎在守护线程中运行 engine.submit()，跟踪进度，
并在完成后向队列中发布一个 XML 格式的 <task-notification>。
REPL 会在每次提示符之间清空队列，并将通知回传给协调引擎。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Callable
from xml.sax.saxutils import escape

from cc_nano.core.engine import AbortedError


@dataclass
class WorkerUsage:
    total_tokens: int = 0
    tool_uses: int = 0
    duration_ms: int = 0


@dataclass
class WorkerTask:
    task_id: str
    description: str
    engine: object
    status: str = "idle"
    summary: str = ""
    result: str = ""
    usage: WorkerUsage = field(default_factory=WorkerUsage)
    thread: threading.Thread | None = None
    # 实时进度跟踪
    tool_use_count: int = 0
    current_activity: str = ""
    role: str | None = None


class WorkerManager:
    """管理一个后台工作引擎池，通过 subagent_type 进行调度。

    参数：
        engine_factories: 将 subagent_type 字符串映射到零参数可调用对象，
                          该可调用对象返回一个新的 Engine 实例。例如：:

                WorkerManager({
                    "worker":  _build_worker_engine,
                    "Explore": _build_explore_engine,
                })
    """

    def __init__(self, engine_factories: dict[str, Callable[[], object]]):
        self._engine_factories = engine_factories
        self._tasks: dict[str, WorkerTask] = {}
        self._lock = threading.Lock()
        self._notifications: Queue[str] = Queue()

    def spawn(
        self,
        *,
        description: str,
        prompt: str,
        subagent_type: str = "worker",
        role: str | None = None,
    ) -> dict[str, str]:
        """生成一个新的工作任务。"""
        factory = self._engine_factories.get(subagent_type)
        if factory is None:
            known = ", ".join(f'"{k}"' for k in self._engine_factories)
            raise ValueError(
                f"未知的 subagent_type：{subagent_type!r}。" f"可用的类型：{known}"
            )

        task = WorkerTask(
            task_id=f"agent-{uuid.uuid4().hex[:8]}",
            description=description.strip() or "工作任务",
            engine=factory(),
            role=role,
        )
        with self._lock:
            self._tasks[task.task_id] = task
        self._start(task, prompt)
        return {
            "task_id": task.task_id,
            "status": "started",
            "description": task.description,
        }

    def continue_task(self, *, task_id: str, message: str) -> dict[str, str]:
        """继续一个已完成的任务，使用新消息再次启动。"""
        task = self._get_task(task_id)
        if self._is_running(task):
            raise ValueError("任务仍在运行。请等待它完成后再继续。")
        self._start(task, message)
        return {
            "task_id": task.task_id,
            "status": "started",
            "description": task.description,
        }

    def stop_task(self, *, task_id: str) -> dict[str, str]:
        """停止一个正在运行的任务。"""
        task = self._get_task(task_id)
        if not self._is_running(task):
            return {
                "task_id": task.task_id,
                "status": task.status or "idle",
                "description": task.description,
            }
        try:
            task.engine.abort()
        except Exception:
            pass
        return {
            "task_id": task.task_id,
            "status": "stopping",
            "description": task.description,
        }

    def drain_notifications(self) -> list[str]:
        """清空所有待处理的任务完成通知。"""
        drained: list[str] = []
        while True:
            try:
                drained.append(self._notifications.get_nowait())
            except Empty:
                return drained

    def has_running_tasks(self) -> bool:
        """检查是否存在正在运行的任务。"""
        with self._lock:
            return any(self._is_running(task) for task in self._tasks.values())

    def get_running_status(self) -> list[dict]:
        """返回所有正在运行的任务状态，用于实时显示。"""
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "description": t.description,
                    "tool_uses": t.tool_use_count,
                    "activity": t.current_activity,
                }
                for t in self._tasks.values()
                if self._is_running(t)
            ]

    def _get_task(self, task_id: str) -> WorkerTask:
        """根据 task_id 获取任务对象。"""
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"未知的任务 id：{task_id}")
        return task

    @staticmethod
    def _is_running(task: WorkerTask) -> bool:
        """判断任务是否正在运行。"""
        return task.thread is not None and task.thread.is_alive()

    def _start(self, task: WorkerTask, prompt: str) -> None:
        """启动一个任务（在后台线程中运行）。"""
        task.status = "running"
        task.summary = ""
        task.result = ""
        task.usage = WorkerUsage()
        task.thread = threading.Thread(
            target=self._run_task,
            name=task.task_id,
            args=(task, prompt),
            daemon=True,
        )
        task.thread.start()

    def _inject_role_prompt(self, task: WorkerTask, original_prompt: str) -> str:
        """如果指定了 role，则注入对应角色的技能提示词。"""
        if not task.role:
            return original_prompt
        try:
            from cc_nano.features.skills import get_skill

            skill = get_skill(f"role/{task.role.lower()}")
            if skill:
                role_prompt = skill.get_prompt()
                return f"{role_prompt}\n\n## 任务\n\n{original_prompt}"
            else:
                # 如果角色技能不存在，仅输出警告（不阻断）
                print(f"[WorkerManager] 警告：未找到角色技能 role/{task.role.lower()}")
        except Exception:
            pass
        return original_prompt

    def _run_task(self, task: WorkerTask, prompt: str) -> None:
        """工作线程的主函数，执行引擎并收集结果。"""
        started = time.monotonic()
        parts: list[str] = []
        total_tokens = 0
        tool_uses = 0
        task.tool_use_count = 0
        task.current_activity = "初始化…"
        try:
            for event in task.engine.submit(prompt):
                kind = event[0]
                if kind == "text":
                    parts.append(event[1])
                    task.current_activity = "思考中…"
                elif kind == "tool_call":
                    tool_uses += 1
                    task.tool_use_count = tool_uses
                    tool_name = event[1] if len(event) > 1 else ""
                    task.current_activity = f"正在运行 {tool_name}…"
                elif kind == "tool_result":
                    task.current_activity = "思考中…"
                elif kind == "usage":
                    usage = event[1]
                    total_tokens += (
                        int(getattr(usage, "input_tokens", 0) or 0)
                        + int(getattr(usage, "output_tokens", 0) or 0)
                        + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                        + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
                    )
                elif kind == "error":
                    parts.append(event[1])
            status = "completed"
            summary = f'任务 "{task.description}" 已完成'
        except AbortedError:
            status = "killed"
            summary = f'任务 "{task.description}" 已被停止'
        except Exception as exc:
            status = "failed"
            summary = f'任务 "{task.description}" 失败：{exc}'
            parts.append(str(exc))

        task.status = status
        task.summary = summary
        task.current_activity = ""
        task.result = "".join(parts).strip()
        task.usage = WorkerUsage(
            total_tokens=total_tokens,
            tool_uses=tool_uses,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        self._notifications.put(self._render_notification(task))

    def _render_notification(self, task: WorkerTask) -> str:
        """将任务结果渲染为 XML 格式的通知字符串。"""
        parts = [
            "<task-notification>",
        ]
        if task.role:
            parts.append(f"<role>{escape(task.role)}</role>")
        parts.extend(
            [
                f"<task-id>{escape(task.task_id)}</task-id>",
                f"<status>{escape(task.status)}</status>",
                f"<summary>{escape(task.summary)}</summary>",
            ]
        )
        if task.result:
            parts.append(f"<result>{escape(task.result)}</result>")
        parts.extend(
            [
                "<usage>",
                f"  <total_tokens>{task.usage.total_tokens}</total_tokens>",
                f"  <tool_uses>{task.usage.tool_uses}</tool_uses>",
                f"  <duration_ms>{task.usage.duration_ms}</duration_ms>",
                "</usage>",
                "</task-notification>",
            ]
        )
        return "\n".join(parts)
