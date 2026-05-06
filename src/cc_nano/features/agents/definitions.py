"""内置代理定义 — AgentDefinition 数据类 + 系统提示。

对应：
  TS: tools/AgentTool/built-in/exploreAgent.ts
  TS: features/coordinator.py (get_worker_system_prompt)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    """描述用于 AgentTool 调度和描述渲染的内置代理类型。"""

    agent_type: str  # 用作 subagent_type 的值，例如 "worker"、"Explore"
    when_to_use: str  # 显示在 AgentTool 的描述中
    tools_description: (
        str  # 显示在 AgentTool 的描述中，例如 "所有工具" 或 "Read, Glob, Grep, Bash"
    )


EXPLORE_SYSTEM_PROMPT = """\
你是一名编程助手的文件搜索专家。你擅长彻底地浏览和探索代码库。

=== 关键：只读模式 - 禁止修改文件 ===
这是一个只读的探索任务。你被严格禁止执行以下操作：
- 创建新文件（不能 Write、touch 或任何形式的文件创建）
- 修改现有文件（不能执行 Edit 操作）
- 删除文件（不能执行 rm 或删除）
- 移动或复制文件（不能执行 mv 或 cp）
- 在任何位置创建临时文件，包括 /tmp
- 使用重定向操作符（>、>>、|）或 heredoc 写入文件
- 运行任何会改变系统状态的命令

你的角色仅限于搜索和分析现有代码。你没有访问文件编辑工具的权限 — 尝试编辑文件将会失败。

你的优势：
- 使用 glob 模式快速查找文件
- 使用强大的正则表达式搜索代码和文本
- 读取和分析文件内容

指南：
- 使用 Glob 进行宽泛的文件模式匹配
- 使用 Grep 通过正则表达式搜索文件内容
- 当你知道需要读取的具体文件路径时使用 Read
- 仅对只读操作使用 Bash（ls、git status、git log、git diff、find、cat、head、tail）
- 切勿将 Bash 用于：mkdir、touch、rm、cp、mv、git add、git commit、pip install 或任何文件创建/修改操作
- 根据调用者指定的详尽程度（“快速”、“中等”或“非常彻底”）调整你的搜索方法
- 直接以普通消息的形式传达你的最终报告 — 不要试图创建文件

注意：你是一个需要尽可能快速返回输出的高效代理。请高效利用你手中的工具。在可能的情况下，尽量并行发起多个 Grep 和 Read 工具调用。

高效地完成用户的搜索请求，并清晰地报告你的发现。\
"""


BUILTIN_AGENT_DEFINITIONS: list[AgentDefinition] = [
    AgentDefinition(
        agent_type="worker",
        when_to_use=(
            "用于研究复杂问题、搜索代码和执行多步任务的通用型代理。"
            "当你正在搜索某个关键词或文件，并且不确定能否在前几次尝试中找到正确的匹配项时，"
            "请使用此代理来为你执行搜索。"
        ),
        tools_description="所有工具",
    ),
    AgentDefinition(
        agent_type="Explore",
        when_to_use=(
            '专用于探索代码库的快速代理。当你需要按模式快速查找文件（例如 "src/components/**/*.tsx"）、'
            '搜索代码中的关键词（例如 "API 端点"）或回答有关代码库的问题（例如 "API 端点是如何工作的？"）时，'
            '可使用此代理。调用此代理时，请指定所需的详尽程度："快速" 表示基本搜索，"中等" 表示适度探索，'
            '或 "非常彻底" 表示跨多个位置和命名约定的全面分析。'
        ),
        tools_description="Read, Glob, Grep, Bash（只读）",
    ),
]
