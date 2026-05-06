"""进入计划模式 与 退出计划模式 工具。

对应 TypeScript 实现：
  TS: tools/EnterPlanModeTool/EnterPlanModeTool.ts + prompt.ts
  TS: tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts + prompt.ts
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cc_nano.core.tool import Tool, ToolResult

if TYPE_CHECKING:
    from cc_nano.features.plan import PlanModeManager


class EnterPlanModeTool(Tool):
    name = "EnterPlanMode"
    description = (
        "当你准备开始一项有一定复杂度的实现任务时，请主动使用此工具。"
        "在编写代码之前获得用户对你方案的确认，可以避免返工，并确保双方理解一致。"
        "此工具会将你切换至“计划模式”，在此模式下你可以探索代码库、设计实现方案，供用户审批。\n\n"
        "## 何时使用此工具\n\n"
        "对于实现类任务，除非非常简单，否则应**优先使用 EnterPlanMode**。"
        "只要满足以下任一条件，就应使用它：\n\n"
        "1. **新功能实现**：添加有实质意义的新功能\n"
        "   - 例如：“添加一个退出登录按钮”——按钮放哪里？点击后发生什么？\n"
        "   - 例如：“添加表单验证”——验证规则是什么？错误信息如何显示？\n\n"
        "2. **存在多种可行方案**：任务可以用几种不同方式解决\n"
        "   - 例如：“为 API 添加缓存”——可用 Redis、内存、文件缓存等\n"
        "   - 例如：“提升性能”——有多种优化策略可选\n\n"
        "3. **修改代码行为**：改动会影响现有行为或结构\n"
        "   - 例如：“修改登录流程”——具体要改变什么？\n"
        "   - 例如：“重构这个组件”——目标架构是什么？\n\n"
        "4. **架构决策**：任务需要在不同模式或技术之间做选择\n"
        "   - 例如：“添加实时更新”——WebSocket vs SSE vs 轮询\n"
        "   - 例如：“实现状态管理”——Redux vs Context vs 自定义方案\n\n"
        "5. **跨多文件修改**：任务可能涉及 2~3 个以上文件\n"
        "   - 例如：“重构身份认证系统”\n"
        "   - 例如：“新增一个带测试的 API 端点”\n\n"
        "6. **需求不明确**：需要先探索才能理解完整范围\n"
        "   - 例如：“让应用运行更快”——需要性能分析，定位瓶颈\n"
        "   - 例如：“修复结账时的缺陷”——需要调查根本原因\n\n"
        "7. **用户偏好影响方案**：合理的实现方式有多种\n"
        "   - 如果你原本想用 AskUserQuestion 来澄清方案，改用 EnterPlanMode 会更合适\n"
        "   - 计划模式允许你先探索，再提供带有上下文的多套选项\n\n"
        "## 何时不使用此工具\n\n"
        "仅在以下简单任务时跳过计划模式：\n"
        "- 单行或几行的修正（拼写错误、明显缺陷、小调整）\n"
        "- 添加一个需求明确的单一函数\n"
        "- 用户已给出非常具体、详细的指令\n"
        "- 纯研究/探索类任务（改用 Agent 工具并指定 explore 代理）\n\n"
        "## 计划模式下的工作流程\n\n"
        "在计划模式中，你需要：\n"
        "1. 使用 Glob、Grep 和 Read 工具全面探索代码库\n"
        "2. 理解现有的模式和架构\n"
        "3. 设计实现方案\n"
        "4. 向用户展示计划，等待审批\n"
        "5. 若需澄清方案，可使用 AskUserQuestion\n"
        "6. 准备实现时，使用 ExitPlanMode 退出计划模式\n\n"
        "## 重要说明\n\n"
        "- 此工具需要用户批准 —— 他们必须同意进入计划模式\n"
        "- 如果不确定是否应该使用，宁可选择计划 —— 事先对齐比返工要好\n"
        "- 用户会感激在对其代码库进行重大修改之前征询他们的意见"
    )
    input_schema = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, plan_manager: PlanModeManager) -> None:
        self._plan_manager = plan_manager

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs) -> str | None:
        return "正在进入计划模式…"

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(content=self._plan_manager.enter())


class ExitPlanModeTool(Tool):
    name = "ExitPlanMode"
    description = (
        "当你处于计划模式，并且已经将计划写入计划文件、准备就绪等待用户审批时，使用此工具。\n\n"
        "## 工具工作方式\n"
        "- 你应该已经将计划写入计划模式系统消息中指定的计划文件\n"
        "- 此工具不接收计划内容作为参数 —— 它会从你写入的文件中读取计划\n"
        "- 此工具仅表示你已完成计划，等待用户审阅和批准\n"
        "- 用户在审阅时会看到你计划文件中的内容\n\n"
        "## 何时使用此工具\n"
        "重要提示：仅当任务需要规划实现步骤并且最终会编写代码时，才使用此工具。"
        "对于研究类任务（收集信息、搜索文件、阅读文件或通常意义上的代码库理解），不要使用此工具。\n\n"
        "## 使用此工具之前\n"
        "确保你的计划完整且无歧义：\n"
        "- 如果对需求或方案还有未解决的问题，请先用 AskUserQuestion（在早期阶段）\n"
        "- 一旦计划最终确定，使用本工具请求批准\n\n"
        "**重要：** 不要使用 AskUserQuestion 来问“这个计划可以吗？”或“我应该继续吗？” —— "
        "那正是本工具的职责。ExitPlanMode 本质上就是请求用户批准你的计划。"
    )
    input_schema = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, plan_manager: PlanModeManager) -> None:
        self._plan_manager = plan_manager

    def get_activity_description(self, **kwargs) -> str | None:
        return "正在退出计划模式…"

    def execute(self, **kwargs) -> ToolResult:
        msg, plan_content = self._plan_manager.exit()

        # 计划文件内容验证-----
        if plan_content:
            required_sections = ["## 计划概述", "## 实现方法", "## 需要修改/创建的文件"]
            missing = [sec for sec in required_sections if sec not in plan_content]
            if missing:
                # 只警告，不阻止退出，但将警告信息附加到返回消息中
                warning = f"\n\n[警告] 计划文件缺少以下章节：{', '.join(missing)}。建议补充完整再提交审批。"
                msg += warning

        return ToolResult(content=msg)
