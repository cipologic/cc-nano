"""Agent, SendMessage, TaskStop 工具。

支持 subagent_type 和可选的 role 参数（用于团队协作模式）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from cc_nano.core.tool import Tool, ToolResult
from cc_nano.features.agents import BUILTIN_AGENT_DEFINITIONS
from cc_nano.features.agents.worker_manager import WorkerManager

if TYPE_CHECKING:
    from cc_nano.features.plan import PlanModeManager


def _build_agent_description() -> str:
    """构建 Agent 工具的描述文本"""
    agent_list = "\n".join(
        f"- {d.agent_type}: {d.when_to_use} (可用工具: {d.tools_description})"
        for d in BUILTIN_AGENT_DEFINITIONS
    )
    return (
        "启动一个新的代理（agent）来处理复杂的多步骤任务。每种代理类型都有其特定的能力和可用的工具。\n\n"
        f"可用的代理类型及其可以使用的工具：\n{agent_list}\n\n"
        "使用 Agent 工具时，可以通过 subagent_type 参数选择要使用的代理类型。如果省略该参数，将使用通用代理。\n\n"
        "何时不应该使用 Agent 工具：\n"
        "- 如果目标已经明确，请使用直接工具：对于已知路径使用 Read 工具，对于特定符号或字符串使用 Grep 工具。"
        "请将此工具保留用于跨代码库的开放性问题，或与某可用代理类型匹配的任务。\n\n"
        "使用说明：\n"
        "- 始终附带一个简短的描述，概述代理将要做什么\n"
        "- 尽可能同时启动多个代理以提高性能；为此，请在单条消息中包含多个工具使用块\n"
        "- 当代理完成时，它将向您返回一条消息。代理返回的结果对用户不可见。要向用户显示结果，"
        "您应当向用户发送一条文本消息，简明扼要地总结结果。\n"
        "- 您可以选择使用 run_in_background 参数在后台运行代理。当代理在后台运行时，"
        "它完成时您会自动收到通知 —— 请勿主动 sleep、轮询或主动检查其进度。"
        "请继续处理其他工作或响应用户。\n"
        "- **前台 vs 后台**：当您需要先获得代理的结果才能继续时（例如，研究类代理的发现会影响您的下一步操作），"
        "请使用前台模式（默认）。当您确实有可以并行完成的独立工作时，使用后台模式。\n"
        "- 要恢复之前生成的代理，请使用 SendMessage 工具，将代理的 ID 或名称作为 `to` 字段 —— 这将在完整上下文中恢复它。"
        "新调用 Agent 会启动一个全新的代理，不记得之前的运行，因此提示词必须是自包含的。\n"
        "- 明确告诉代理您期望它编写代码还是仅进行研究（搜索、文件读取、网络抓取等），因为它不知道用户的意图\n"
        "- 如果代理描述中提到应该主动使用它，那么您应当尽量在用户没有主动要求的情况下就使用它。\n"
        "- 如果用户指定要“并行”运行代理，您必须发送一条包含多个 Agent 工具使用内容块的消息。"
        "例如，如果需要同时启动一个构建验证代理和一个测试运行代理，请发送一条包含两个工具调用的消息。\n\n"
        "## 编写提示词\n\n"
        "像给一位刚走进房间的聪明同事下达任务一样给代理做简报 —— 它没有看过之前的对话，不知道您尝试过什么，"
        "也不理解这个任务为什么重要。\n"
        "- 解释您想要达成什么目标以及为什么。\n"
        "- 描述您已经了解或排除的信息。\n"
        "- 提供足够的上下文，让代理能够做出判断，而不仅仅是遵循狭窄的指令。\n"
        "- 如果您需要简短的回答，请明确说明（例如“200 字以内报告”）。\n"
        "- 查找任务：直接给出具体的命令。调查任务：给出问题 —— 当前提错误时，预定义的步骤会成为累赘。\n\n"
        "过于简短的命令式提示词会产生浅薄、通用的结果。\n\n"
        "**切勿将理解任务委托给代理。** 不要写“基于你的发现，修复 bug”或“基于研究，实现它”。"
        "这类短语将综合工作推给代理，而不是由您自己完成。请编写能证明您已经理解的提示词："
        "包括文件路径、行号、具体要更改的内容。"
    )


class AgentTool(Tool):
    name = "Agent"
    description = _build_agent_description()
    input_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "代理任务的简短标签（3-5 个词）",
            },
            "prompt": {"type": "string", "description": "自包含的代理指令"},
            "subagent_type": {
                "type": "string",
                "enum": ["worker", "Explore"],
                "default": "worker",
                "description": "要使用的代理类型。'worker' 用于通用任务；'Explore' 用于快速的只读代码库探索。",
            },
            "role": {
                "type": "string",
                "description": "（团队协作模式）指定 worker 扮演的角色：Architect, TechLead, PlanReviewer, Implementer, Reviewer, QA",
                "enum": [
                    "Architect",
                    "TechLead",
                    "PlanReviewer",
                    "Implementer",
                    "Reviewer",
                    "QA",
                ],
            },
        },
        "required": ["description", "prompt"],
    }

    def get_activity_description(self, **kwargs) -> str | None:
        desc = kwargs.get("description", "")
        return f"正在运行代理：{desc}" if desc else "正在运行代理…"

    def __init__(
        self, manager: WorkerManager, plan_manager: "PlanModeManager | None" = None
    ):
        self._manager = manager
        self._plan_manager = plan_manager

    def execute(
        self,
        description: str,
        prompt: str,
        subagent_type: str = "worker",
        role: str | None = None,
    ) -> ToolResult:
        # 计划模式下限制子代理类型
        if self._plan_manager and self._plan_manager.is_active:
            if subagent_type != "Explore":
                return ToolResult(
                    content=(
                        "错误：计划模式下只能使用 Explore 子代理类型。"
                        "请将 subagent_type 设置为 'Explore' 以进行只读探索。"
                    ),
                    is_error=True,
                )

        try:
            payload = self._manager.spawn(
                description=description,
                prompt=prompt,
                subagent_type=subagent_type,
                role=role,
            )
        except ValueError as exc:
            return ToolResult(content=f"错误：{exc}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))


class SendMessageTool(Tool):
    name = "SendMessage"
    description = "通过任务 ID 继续一个已空闲的工作线程。当工作线程已经返回结果，而您希望它再执行一步时使用。"
    input_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "要继续的工作线程任务 ID"},
            "message": {"type": "string", "description": "下一步的自包含指令"},
        },
        "required": ["to", "message"],
    }

    def __init__(self, manager: WorkerManager):
        self._manager = manager

    def execute(self, to: str, message: str) -> ToolResult:
        try:
            payload = self._manager.continue_task(task_id=to, message=message)
        except ValueError as exc:
            return ToolResult(content=f"错误：{exc}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))


class TaskStopTool(Tool):
    name = "TaskStop"
    description = "通过任务 ID 停止一个正在运行的工作线程。"
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "工作线程任务 ID"},
        },
        "required": ["task_id"],
    }

    def __init__(self, manager: WorkerManager):
        self._manager = manager

    def execute(self, task_id: str) -> ToolResult:
        try:
            payload = self._manager.stop_task(task_id=task_id)
        except ValueError as exc:
            return ToolResult(content=f"错误：{exc}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))
