"""被排除命令的匹配逻辑。

对应 shouldUseSandbox.ts 中的 containsExcludedCommand() (第21-128行)。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum


class RuleType(Enum):
    PREFIX = "prefix"
    EXACT = "exact"
    WILDCARD = "wildcard"


@dataclass
class MatchRule:
    type: RuleType
    pattern: str


def parse_rule(pattern: str) -> MatchRule:
    """将排除模式解析为匹配规则。

    规则判定（对应 bashPermissionRule）：
    - 包含 '*' 或 '?' -> 通配符
    - 包含空格 -> 前缀（"npm run" 可匹配 "npm run xxx"）
    - 否则 -> 精确匹配（"git" 仅匹配 "git"）
    """
    stripped = pattern.strip()
    if "*" in stripped or "?" in stripped:
        return MatchRule(RuleType.WILDCARD, stripped)
    if " " in stripped:
        return MatchRule(RuleType.PREFIX, stripped)
    return MatchRule(RuleType.EXACT, stripped)


def matches_rule(rule: MatchRule, command: str) -> bool:
    """检查一条命令是否匹配某条规则。"""
    if rule.type == RuleType.EXACT:
        return command == rule.pattern
    if rule.type == RuleType.PREFIX:
        return command == rule.pattern or command.startswith(rule.pattern + " ")
    if rule.type == RuleType.WILDCARD:
        return fnmatch.fnmatch(command, rule.pattern)
    return False


def _split_compound_command(command: str) -> list[str]:
    """在 '&&' 上拆分复合命令。

    对应 splitCommand_DEPRECATED()。
    简单拆分 —— 不处理引号内的 '&&'。
    """
    return [part.strip() for part in command.split("&&") if part.strip()]


def _strip_env_prefix(command: str) -> str:
    """去除命令开头处的环境变量赋值。

    例如 "FOO=bar BAZ=1 npm test" -> "npm test"
    对应 shouldUseSandbox.ts 中的环境变量剥离逻辑。
    """
    parts = command.split()
    i = 0
    while i < len(parts) and "=" in parts[i]:
        i += 1
    return " ".join(parts[i:]) if i < len(parts) else command


def contains_excluded_command(
    command: str,
    excluded_patterns: list[str],
) -> bool:
    """判断一个命令是否应被排除在沙箱之外。

    对应 containsExcludedCommand (shouldUseSandbox.ts:21-128)。
    逻辑：
    1. 在 '&&' 上拆分为若干子命令
    2. 对每个子命令，尝试去除环境变量前缀
    3. 与每条排除模式进行匹配
    4. 只要有一个子命令匹配任意模式，就返回 True
    """
    if not excluded_patterns:
        return False

    rules = [parse_rule(p) for p in excluded_patterns]
    subcommands = _split_compound_command(command)

    for subcmd in subcommands:
        candidates = [subcmd, _strip_env_prefix(subcmd)]
        candidates = list(dict.fromkeys(candidates))  # 去重并保持顺序

        for rule in rules:
            for cand in candidates:
                if matches_rule(rule, cand):
                    return True
    return False
