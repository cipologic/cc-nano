"""上下文压缩 — 对旧消息进行摘要，以释放令牌预算。"""

from __future__ import annotations

from typing import Any

from cc_nano.core.llm import LLMClient

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN = 4
COMPACT_THRESHOLD_TOKENS = 100_000  # 回退阈值（无实际使用数据时）
MIN_RECENT_MESSAGES = 6  # 至少保留这么多条消息
MIN_RECENT_TOKENS = 10_000  # 至少保留这么多令牌的最近上下文
COMPACT_MAX_OUTPUT_TOKENS = 4096
AUTOCOMPACT_BUFFER_TOKENS = 13_000  # 与官方 autoCompact.ts 一致

# 模型上下文窗口（令牌数）。优先匹配第一个。
# DeepSeek V4 通过 OpenAI ChatCompletions 接口调用，base_url 保持不变，
# model 参数需要改为 deepseek-v4-pro 或 deepseek-v4-flash[reference:2][reference:3]
_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    # DeepSeek V4 系列（均支持 1M 令牌上下文窗口）[reference:4][reference:5]
    ("deepseek-v4-pro", 1_000_000),
    ("deepseek-v4-flash", 1_000_000),
    ("deepseek-v4", 1_000_000),  # 通用匹配
]
_DEFAULT_CONTEXT_WINDOW = 1_000_000  # DeepSeek V4 默认上下文窗口


def _context_window_for_model(model: str) -> int:
    """返回指定模型的上下文窗口大小。"""
    model_lower = model.lower()
    for prefix, window in _CONTEXT_WINDOWS:
        if prefix in model_lower:
            return window
    return _DEFAULT_CONTEXT_WINDOW


def _auto_compact_threshold(model: str) -> int:
    """自动压缩阈值 = 上下文窗口 - 最大输出预留 - 缓冲（与官方一致）。"""
    cw = _context_window_for_model(model)
    max_out_reserve = min(20_000, cw // 5)  # 为摘要输出预留
    return cw - max_out_reserve - AUTOCOMPACT_BUFFER_TOKENS


COMPACT_PROMPT = """\
请对我们的对话历史提供一份详细的摘要。这份摘要将 *替换* 之前的消息，以释放上下文空间，
因此它必须保留无缝继续工作所需的所有细节。

请按照以下结构组织你的回答：

## 主要请求
用户试图实现的总体目标。

## 关键技术概念
所涉及的重要技术细节、模式、框架或已确定的约束条件。

## 文件和代码
讨论过或修改过的关键文件，简要说明对每个文件做了哪些改动。

## 错误与修复
遇到的任何错误以及如何解决。

## 当前工作
最近正在处理的工作及其当前状态。

## 待办任务
尚未完成的未竟工作或后续步骤。

重点保留继续工作所需的信息。请具体化 —— 包含文件路径、函数名、错误消息和具体决策，
而非含糊的概括。\
"""

COMPACT_SYSTEM = "你是一个对话摘要助手。请按照用户要求的格式生成结构化的详细摘要。"

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _text_of(content: Any) -> str:
    """从消息内容中提取纯文本（支持字符串、块列表等）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                # 文本块、tool_result、tool_use 等
                parts.append(block.get("text", ""))
                parts.append(
                    block.get("content", "")
                    if isinstance(block.get("content"), str)
                    else ""
                )
                parts.append(str(block.get("input", "")))
            elif hasattr(block, "text"):
                parts.append(getattr(block, "text", ""))
            elif hasattr(block, "input"):
                parts.append(str(getattr(block, "input", "")))
        return " ".join(parts)
    return str(content) if content else ""


def estimate_tokens(messages: list[dict]) -> int:
    """粗略估算令牌数：总字符数 / CHARS_PER_TOKEN。"""
    total_chars = 0
    for msg in messages:
        total_chars += len(_text_of(msg.get("content", "")))
    return total_chars // CHARS_PER_TOKEN


def should_compact(
    messages: list[dict], model: str | None = None, last_input_tokens: int | None = None
) -> bool:
    """判断对话是否应进行自动压缩。

    如果提供了 *last_input_tokens*（来自 API 响应），则基于模型感知的阈值进行判断
    （与官方 ``autoCompact.ts`` 一致）。否则回退到基于字符的估算。
    """
    if last_input_tokens and model:
        return last_input_tokens >= _auto_compact_threshold(model)
    return estimate_tokens(messages) > COMPACT_THRESHOLD_TOKENS


# ---------------------------------------------------------------------------
# 消息拆分
# ---------------------------------------------------------------------------


def _split_recent(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """将 *messages* 拆分为 (待摘要的历史消息, 保留的最近消息)。

    向后遍历，累积最近的消息，直到同时满足 MIN_RECENT_MESSAGES 和 MIN_RECENT_TOKENS。
    不会拆分 tool_use / tool_result 对。
    """
    if len(messages) <= MIN_RECENT_MESSAGES:
        return [], list(messages)

    keep_start = len(messages)
    kept_tokens = 0
    kept_msgs = 0

    for i in range(len(messages) - 1, -1, -1):
        kept_tokens += len(_text_of(messages[i].get("content", ""))) // CHARS_PER_TOKEN
        kept_msgs += 1
        keep_start = i

        if kept_msgs >= MIN_RECENT_MESSAGES and kept_tokens >= MIN_RECENT_TOKENS:
            break

    # 不要拆分 tool_use 和它的 tool_result。
    # 如果 keep_start 落在一条纯 tool_result 的用户消息上，则包含前一条助手消息（其中包含 tool_use）。
    if keep_start > 0:
        msg = messages[keep_start]
        content = msg.get("content", "")
        if (
            msg.get("role") == "user"
            and isinstance(content, list)
            and all(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )
        ):
            keep_start -= 1

    history = messages[:keep_start]
    recent = messages[keep_start:]
    return history, recent


# ---------------------------------------------------------------------------
# 压缩服务
# ---------------------------------------------------------------------------


class CompactService:
    """通过 API 摘要压缩对话上下文。"""

    def __init__(self, client: LLMClient, model: str, effort: str | None = None):
        self._client = client
        self._model = model
        self._effort = effort

    def compact(
        self,
        messages: list[dict],
        system_prompt: str,
        custom_instructions: str = "",
    ) -> tuple[list[dict], str]:
        """摘要 *messages* 并返回 ``(new_messages, summary_text)``。

        返回的消息列表结构为::

            [user: 摘要] [assistant: 确认] [保留的最近消息 …]
        """
        history, recent = _split_recent(messages)

        if not history:
            return list(messages), "(无内容可压缩)"

        # 构建压缩请求
        prompt = COMPACT_PROMPT
        if custom_instructions:
            prompt += f"\n\n额外指令：{custom_instructions}"

        # 移除历史消息中的图片/文档以节省令牌
        cleaned = _strip_media(history)
        cleaned.append({"role": "user", "content": prompt})

        # 确保消息列表以 user 角色开头
        if cleaned and cleaned[0].get("role") != "user":
            cleaned.insert(0, {"role": "user", "content": "(对话开始)"})

        # 确保角色交替
        cleaned = _fix_alternation(cleaned)

        response = self._client.create_message(
            model=self._model,
            max_tokens=COMPACT_MAX_OUTPUT_TOKENS,
            system=COMPACT_SYSTEM,
            messages=cleaned,
            effort=self._effort,
        )

        summary_text = ""
        for block in response.content:
            if isinstance(block, dict) and block.get("type") == "text":
                summary_text += block.get("text", "")
            elif hasattr(block, "text"):
                summary_text += block.text

        if not summary_text.strip():
            summary_text = "(压缩生成的摘要为空)"

        # 构建新消息列表
        new_messages: list[dict] = [
            {
                "role": "user",
                "content": (
                    "[这是迄今为止对话的摘要 —— "
                    "原始消息已被压缩以节省上下文空间。]\n\n" + summary_text
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "明白了。我已经阅读了上面的对话摘要，可以从中断的地方继续。"
                    "接下来你想做什么？"
                ),
            },
        ]
        new_messages.extend(recent)

        return new_messages, summary_text


# ---------------------------------------------------------------------------
# 媒体内容剥离
# ---------------------------------------------------------------------------


def _strip_media(messages: list[dict]) -> list[dict]:
    """返回 *messages* 的副本，将图片/文档替换为文本标记。"""
    out: list[dict] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            new_blocks: list[Any] = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "image":
                        new_blocks.append({"type": "text", "text": "[图片]"})
                    elif btype == "document":
                        new_blocks.append({"type": "text", "text": "[文档]"})
                    else:
                        new_blocks.append(block)
                elif hasattr(block, "type"):
                    btype = getattr(block, "type", "")
                    if btype == "image":
                        new_blocks.append({"type": "text", "text": "[图片]"})
                    elif btype == "document":
                        new_blocks.append({"type": "text", "text": "[文档]"})
                    elif hasattr(block, "model_dump"):
                        new_blocks.append(block.model_dump())
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)
            out.append({"role": msg["role"], "content": new_blocks})
        else:
            out.append(dict(msg))
    return out


def _fix_alternation(messages: list[dict]) -> list[dict]:
    """确保严格遵循 API 要求的 user/assistant 交替。"""
    if not messages:
        return messages
    fixed: list[dict] = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == fixed[-1]["role"]:
            # 合并到前一条消息
            prev_content = fixed[-1].get("content", "")
            cur_content = msg.get("content", "")
            if isinstance(prev_content, str) and isinstance(cur_content, str):
                fixed[-1]["content"] = prev_content + "\n" + cur_content
            else:

                def _as_list(c: Any) -> list:
                    if isinstance(c, list):
                        return list(c)
                    return [{"type": "text", "text": str(c)}]

                fixed[-1]["content"] = _as_list(prev_content) + _as_list(cur_content)
        else:
            fixed.append(msg)
    return fixed
