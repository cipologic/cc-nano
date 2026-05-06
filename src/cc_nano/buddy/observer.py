"""陪伴者观察器——每轮对话后生成反应。

两种模式：
1. 普通模式：对 CC-NANO 的回复生成一句风趣短评
2. 直接对话模式：用户通过名字呼叫陪伴者，陪伴者根据对话历史做出回复，以记住最近的交流

在后台线程中运行，避免阻塞 REPL。
使用配置的陪伴者模型。
"""

from __future__ import annotations

import threading
from typing import Callable

from cc_nano.core.llm import LLMClient

from .types import Companion

_MAX_RESPONSE_PREVIEW = 500  # 最大回复预览长度
_MAX_CHAT_HISTORY = 20  # 为陪伴者对话保留最近 N 条消息


def _is_addressed(user_msg: str, companion_name: str) -> bool:
    """检查用户是否通过名字直接呼叫陪伴者。

    匹配全名（如"格利奇·洪克"）或名字（如"格利奇"）。
    """
    msg_lower = user_msg.lower()
    if companion_name.lower() in msg_lower:
        return True
    first_name = companion_name.split()[0].lower() if companion_name else ""
    return first_name in msg_lower if first_name else False


class CompanionChat:
    """维护陪伴者直接交互时的对话历史。"""

    def __init__(self):
        self._messages: list[dict[str, str]] = []

    def add_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})
        self._trim()

    def add_assistant(self, text: str) -> None:
        self._messages.append({"role": "assistant", "content": text})
        self._trim()

    def get_messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def _trim(self) -> None:
        if len(self._messages) > _MAX_CHAT_HISTORY:
            self._messages = self._messages[-_MAX_CHAT_HISTORY:]


# 模块级对话历史——在同一会话中各轮之间持久存在
_companion_chat = CompanionChat()


def fire_companion_observer(
    last_assistant_msg: str,
    companion: Companion,
    client: LLMClient,
    callback: Callable[[str], None],
    model: str,
    user_msg: str = "",
) -> None:
    """启动后台线程生成陪伴者的反应。

    如果用户通过名字呼叫了陪伴者，则生成带有对话历史的直接回复。
    否则，生成无状态的一句话短评。
    """
    addressed = _is_addressed(user_msg, companion.name) if user_msg else False

    def _run():
        try:
            # 描述数值时加上等级说明，以便模型理解其含义
            def _describe(name: str, val: int) -> str:
                level = (
                    "极低"
                    if val < 20
                    else (
                        "低"
                        if val < 40
                        else "中等" if val < 60 else "高" if val < 80 else "极高"
                    )
                )
                return f"{name}={val}/100 ({level})"

            stats_desc = ", ".join(
                _describe(s, companion.stats.get(s, 50))
                for s in ("DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK")
            )

            system_prompt = (
                f"你是{companion.name}，一只小小的{companion.species}（{companion.rarity}稀有度），"
                f"坐在一个编程终端旁边。\n"
                f"你的性格：{companion.personality}\n\n"
                f"你的属性（每个0-100，这些属性必须影响你的说话方式）：\n"
                f"{stats_desc}\n\n"
                f"属性如何影响你的行为：\n"
                f"- 调试能力：高=给出技术见解，低=对代码一窍不通\n"
                f"- 耐心：高=冷静支持，低=容易烦躁\n"
                f"- 混乱：高=随机且难以预测，低=井然有序、稳定\n"
                f"- 智慧：高=深思熟虑、有深度，低=天真单纯\n"
                f"- 讽刺：高=尖酸刻薄、风趣，低=真诚可爱\n\n"
            )

            # 注入情绪描述
            from .mood import describe_mood

            system_prompt += describe_mood(companion.mood) + "\n\n"

            system_prompt += (
                "重要：始终使用用户所用的语言回复。"
                "如果用户写中文，就用中文回复。如果是英文，就用英文回复。"
                "你是有趣的，但绝不敌对或粗鲁。"
                "你可以被调侃，并应该幽默地配合。"
            )

            if addressed:
                # 直接对话模式——使用聊天历史
                _companion_chat.add_user(user_msg)

                response = client.create_message(
                    model=model,
                    max_tokens=80,
                    system=(
                        f"{system_prompt}\n\n"
                        f"以{companion.name}的身份用一句话回复（不超过60个字符）。"
                        f"要干脆利落、符合角色。不要啰嗦。"
                        f"不要在回复外加引号，也不要有*做动作*之类的行为描述。"
                    ),
                    messages=_companion_chat.get_messages(),
                )
                reaction = _extract_text(response).strip()
                if reaction:
                    _companion_chat.add_assistant(reaction)
                    callback(reaction)
            else:
                # 普通模式——无状态的一句话短评
                preview = last_assistant_msg[:_MAX_RESPONSE_PREVIEW]
                response = client.create_message(
                    model=model,
                    max_tokens=60,
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"{system_prompt}\n\n"
                                f'AI助手刚刚说：\n"{preview}"\n\n'
                                f"用一句简短风趣的评论（不超过60个字符）做出反应。"
                                f"保持角色。不要引号，不要表情符号，不要解释。"
                            ),
                        }
                    ],
                )
                reaction = _extract_text(response).strip()
                if reaction:
                    callback(reaction)
        except Exception:
            pass  # 非必要功能——静默吞掉所有错误

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def _extract_text(response) -> str:
    parts: list[str] = []
    for block in response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)
