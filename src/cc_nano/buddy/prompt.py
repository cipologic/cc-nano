"""System prompt integration for companion."""

from __future__ import annotations

from typing import Optional

from .animator import CompanionAnimator


def companion_intro_text(name: str, species: str) -> str:
    """生成系统提示中陪伴角色的介绍文本。

    精确移植自 prompt.ts 中的 companionIntroText() 函数。
    """
    return (
        f"# 陪伴角色\n\n"
        f"一只名为 {name} 的小小{species}坐在用户输入框旁边，偶尔在对话气泡中评论。"
        f"你并不是{name} —— 它是一个独立的旁观者。\n\n"
        f"当用户直接称呼{name}（通过名字）时，它的气泡会做出回应。此时你的任务是保持低调："
        f"用不超过一行文字回应，或者只回答消息中属于你的那部分。不要解释你不是{name} —— "
        f"用户是知道的。不要描述{name}可能会说什么 —— 那个由气泡负责。\n\n"
        f"重要：永远不要写出类似“*保持安静*”、“*看着*”、“*让{name}回应*”这样的动作描写，"
        f"也不要进行任何角色扮演式的叙述。如果消息完全是给{name}的（或简称为“{name.split()[0]}”），"
        f"且没有任何内容需要你回应，只需回复一个句点：`.`"
    )


def companion_intro_safe() -> str:
    """安全获取伴侣介绍文本，若伴侣未孵化或出现任何异常则返回空字符串。"""
    try:
        from cc_nano.buddy.companion import get_companion
        from cc_nano.buddy.storage import load_companion_muted

        if load_companion_muted():
            return ""
        companion = get_companion()
        if companion is None:
            return ""
        return companion_intro_text(companion.name, companion.species)
    except Exception:
        return ""


def safe_get_animator() -> Optional[CompanionAnimator]:
    try:
        from cc_nano.buddy.animator import CompanionAnimator
        from cc_nano.buddy.companion import get_companion
        from cc_nano.buddy.storage import load_companion_muted

        if load_companion_muted():
            return None
        comp = get_companion()
        if comp:
            return CompanionAnimator(comp)
    except Exception:
        pass
    return None
