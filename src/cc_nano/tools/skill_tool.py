"""SkillTool - 允许 Worker 动态调用内置技能。

该工具由 Worker 引擎持有，协调者通过 allowed_skills 白名单和系统提示控制 Worker 的技能能力。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cc_nano.core.tool import Tool, ToolResult
from cc_nano.features.skills import get_skill

if TYPE_CHECKING:
    from cc_nano.core.engine import Engine

# 防止递归调用的最大深度
_MAX_SKILL_DEPTH = 3
_depth_counter = 0


class SkillTool(Tool):
    """Worker 调用内置技能的工具。

    每个 Worker 实例持有一个 SkillTool。协调者通过 allowed_skills 白名单
    限制 Worker 可调用的技能，并通过系统提示告知 Worker 可用技能列表。
    """

    name = "Skill"
    description = (
        "执行一个内置技能并返回结果。技能是一组预定义的指令，可以帮助你完成特定任务。\n\n"
        "使用示例：\n"
        '- Skill(name="simplify", args="focus=auth") - 执行简化技能，聚焦于 auth 模块\n'
        '- Skill(name="test") - 运行测试技能\n'
        '- Skill(name="review", args="security") - 执行安全审查技能\n\n'
        "技能执行完成后，其输出将作为工具结果返回。你可以根据结果决定下一步行动。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "技能名称，如 'simplify', 'test', 'review', 'commit'",
            },
            "args": {
                "type": "string",
                "description": "传递给技能的参数（可选）",
                "default": "",
            },
        },
        "required": ["name"],
    }

    def __init__(self, allowed_skills: list[str] | None = None):
        """初始化 SkillTool。

        Args:
            allowed_skills: 白名单技能列表。None 表示允许所有已注册技能。
        """
        self._allowed_skills = set(allowed_skills) if allowed_skills else None
        self._engine: Engine | None = None

    def set_engine(self, engine: Engine) -> None:
        """注入 Worker 引擎实例，用于执行技能提示词。

        必须在 Worker 引擎创建后调用此方法。
        """
        self._engine = engine

    def get_activity_description(self, **kwargs) -> str | None:
        name = kwargs.get("name", "")
        return f"正在执行技能：{name}" if name else None

    def execute(self, name: str, args: str = "") -> ToolResult:
        """执行指定的技能。

        Args:
            name: 技能名称（必须与注册表中的名称一致）
            args: 传递给技能的参数，会被替换到技能提示词的 $ARGUMENTS 中

        Returns:
            ToolResult 包含技能执行输出或错误信息
        """
        global _depth_counter

        # 1. 递归深度防护
        if _depth_counter >= _MAX_SKILL_DEPTH:
            return ToolResult(
                content=f"错误：技能调用深度超过限制（{_MAX_SKILL_DEPTH}），可能存在递归循环。",
                is_error=True,
            )

        # 2. 白名单检查（层面二的强制执行）
        if self._allowed_skills is not None and name not in self._allowed_skills:
            allowed_str = ", ".join(sorted(self._allowed_skills))
            return ToolResult(
                content=f"Worker 无权调用技能 '{name}'。允许的技能：{allowed_str}",
                is_error=True,
            )

        # 3. 获取技能定义
        skill = get_skill(name)
        if not skill:
            return ToolResult(
                content=f"未知技能 '{name}'。请使用 /skills 查看可用技能。",
                is_error=True,
            )

        # 4. 获取技能提示词（替换 $ARGUMENTS 变量）
        prompt = skill.get_prompt(args)
        if not prompt:
            return ToolResult(
                content=f"技能 '{name}' 未生成有效提示词。", is_error=True
            )

        # 5. 确保 engine 已注入
        if self._engine is None:
            return ToolResult(
                content="内部错误：SkillTool 未绑定到 Worker 引擎。", is_error=True
            )

        # 6. 在当前 Worker 引擎中执行技能提示词
        output_parts: list[str] = []
        _depth_counter += 1
        try:
            for event in self._engine.submit(prompt):
                if event[0] == "text":
                    output_parts.append(event[1])
                elif event[0] == "error":
                    output_parts.append(f"\n[技能执行错误] {event[1]}")
                # 忽略其他事件类型（waiting, tool_call, tool_result 等），只收集文本输出
        except Exception as e:
            return ToolResult(content=f"技能执行失败：{e}", is_error=True)
        finally:
            _depth_counter -= 1

        result = "".join(output_parts).strip()
        if not result:
            result = f"技能 '{name}' 执行完成，无输出。"
        return ToolResult(content=result)
