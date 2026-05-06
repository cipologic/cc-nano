"""技能系统 —— 加载、注册和执行基于 SKILL.md 的技能。

技能是带有 YAML 前置元数据的 Markdown 文件，用于定义可复用的提示词。
它们可以是：
  1. **内置** —— 通过 ``register_skill()`` 在代码中注册
  2. **项目** —— 从 ``.cc-nano/skills/<name>/SKILL.md`` 发现
  3. **用户** —— 从 ``~/.cc-nano/skills/<name>/SKILL.md`` 发现

执行模式：
  - **inline**（默认）：提示词直接注入当前对话
  - **fork**：提示词在隔离的轮次中运行（消息会被保存/恢复）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# 技能定义
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    """单个技能定义。"""

    name: str
    description: str = ""
    when_to_use: str = ""
    user_invocable: bool = True
    disable_model_invocation: bool = False
    allowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    context: str = "inline"  # "inline" 或 "fork"
    argument_hint: str = ""
    paths: list[str] = field(default_factory=list)  # gitignore 风格的模式
    source: str = "project"  # "bundled", "project", "user"
    skill_root: str | None = None  # 用于 $SKILL_DIR 替换的基目录

    # 提示词内容（SKILL.md 中除去前置元数据之后的主体）
    _prompt_text: str = ""
    # 或用于内置技能的动态提示词生成器
    _prompt_fn: Callable[[str], str] | None = None

    def get_prompt(self, args: str = "") -> str:
        """返回最终的提示词文本，并进行变量替换。"""
        if self._prompt_fn is not None:
            return self._prompt_fn(args)
        else:
            text = self._prompt_text

        # 替换 $ARGUMENTS 和 ${ARGUMENTS}（优先长形式其实无影响，因为字符串不同）
        text = text.replace("$ARGUMENTS", args)
        text = text.replace("${ARGUMENTS}", args)

        if self.skill_root:
            # 替换 ${CC_NANO_SKILL_DIR}
            text = text.replace("${CC_NANO_SKILL_DIR}", self.skill_root)
            # 替换 $CC_NANO_SKILL_DIR（无花括号形式）
            text = text.replace("$CC_NANO_SKILL_DIR", self.skill_root)
            # 可选：替换 ${SKILL_DIR} 和 $SKILL_DIR（常见简写）
            text = text.replace("${SKILL_DIR}", self.skill_root)
            text = text.replace("$SKILL_DIR", self.skill_root)
        if args and self.argument_hint:
            hint = self.argument_hint
            # 替换 ${hint}
            text = text.replace(f"${{{hint}}}", args)
            # 替换 $hint（无花括号形式）
            text = text.replace(f"${hint}", args)
        return text


# ---------------------------------------------------------------------------
# YAML 前置元数据解析器（最小实现，不依赖 PyYAML）
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """将 ``text`` 分割为 (frontmatter_dict, body)。

    使用极简的 key: value 解析器 —— 支持字符串、布尔值和逗号分隔的列表。
    不支持嵌套 YAML。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    raw = m.group(1)
    body = text[m.end() :]
    meta: dict[str, Any] = {}

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower().replace("-", "_")
        val = val.strip()
        # 布尔值
        if val.lower() in ("true", "yes"):
            meta[key] = True
        elif val.lower() in ("false", "no"):
            meta[key] = False
        # 列表（逗号分隔）
        elif "," in val:
            meta[key] = [v.strip() for v in val.split(",") if v.strip()]
        # 带引号的字符串
        elif (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            meta[key] = val[1:-1]
        else:
            meta[key] = val

    return meta, body


def _ensure_str(val: Any, default: str = "") -> str:
    """将 *val* 强制转换为字符串 —— 如果前置解析器生成了列表，则重新拼接。"""
    if val is None:
        return default
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val)


def _skill_from_frontmatter(
    meta: dict[str, Any],
    body: str,
    name: str,
    source: str,
    skill_root: str | None = None,
) -> Skill:
    """根据解析出的前置元数据和正文构建 ``Skill`` 对象。"""
    allowed = meta.get("allowed_tools", [])
    if isinstance(allowed, str):
        allowed = [t.strip() for t in allowed.split(",") if t.strip()]

    paths = meta.get("paths", [])
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split(",") if p.strip()]

    return Skill(
        name=_ensure_str(meta.get("name"), name),
        description=_ensure_str(meta.get("description")),
        when_to_use=_ensure_str(meta.get("when_to_use")),
        user_invocable=meta.get("user_invocable", True),
        disable_model_invocation=meta.get("disable_model_invocation", False),
        allowed_tools=allowed,
        model=meta.get("model"),
        context=_ensure_str(meta.get("context"), "inline"),
        argument_hint=_ensure_str(meta.get("arguments")),
        paths=paths,
        source=source,
        skill_root=skill_root,
        _prompt_text=body.strip(),
    )


# ---------------------------------------------------------------------------
# 技能注册表
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Skill] = {}


def register_skill(skill: Skill) -> None:
    """将技能添加到全局注册表。"""
    _REGISTRY[skill.name] = skill


def get_skill(name: str) -> Skill | None:
    """根据名称查找技能。"""
    return _REGISTRY.get(name)


def list_skills(user_invocable_only: bool = True) -> list[Skill]:
    """返回所有已注册的技能，可选过滤。"""
    skills = list(_REGISTRY.values())
    if user_invocable_only:
        skills = [s for s in skills if s.user_invocable]
    return sorted(skills, key=lambda s: (s.source != "bundled", s.name))


def clear_skills(source: str | None = None) -> None:
    """从注册表中移除技能。如果指定了 *source*，则只移除该来源的技能。"""
    if source is None:
        _REGISTRY.clear()
    else:
        to_remove = [k for k, v in _REGISTRY.items() if v.source == source]
        for k in to_remove:
            del _REGISTRY[k]


# ---------------------------------------------------------------------------
# 从磁盘发现技能
# ---------------------------------------------------------------------------


def load_skills_from_dir(skills_dir: Path, source: str = "project") -> list[Skill]:
    """扫描 *skills_dir* 中的 ``<name>/SKILL.md``，并注册每个技能。

    目录格式加载：仅识别包含 ``SKILL.md`` 文件的目录。
    """
    loaded: list[Skill] = []
    if not skills_dir.is_dir():
        return loaded

    for entry in sorted(skills_dir.iterdir()):
        skill = None
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                # 回退：查找目录中的任意 .md 文件
                md_files = list(entry.glob("*.md"))
                if md_files:
                    skill_md = md_files[0]
                else:
                    continue
            try:
                text = skill_md.read_text(encoding="utf-8")
            except Exception:
                continue
            meta, body = _parse_frontmatter(text)
            skill = _skill_from_frontmatter(
                meta,
                body,
                name=entry.name,
                source=source,
                skill_root=str(entry),
            )
        elif entry.suffix == ".md" and entry.is_file():
            # 遗留的单文件格式
            try:
                text = entry.read_text(encoding="utf-8")
            except Exception:
                continue
            meta, body = _parse_frontmatter(text)
            skill = _skill_from_frontmatter(
                meta,
                body,
                name=entry.stem,
                source=source,
                skill_root=str(entry.parent),
            )

        if skill and skill._prompt_text:
            register_skill(skill)
            loaded.append(skill)

    return loaded


def discover_skills(cwd: str | None = None) -> list[Skill]:
    """从标准位置发现并注册技能。

    搜索顺序：
      1. 内置技能（已通过 ``register_bundled_skills()`` 注册）
      2. 项目技能：    ``{cwd}/.cc-nano/skills/``

    返回新加载的技能（排除已注册的内置技能）。
    """
    loaded: list[Skill] = []

    # 项目级技能
    if cwd:
        project_dir = Path(cwd) / ".cc-nano" / "skills"
        loaded.extend(load_skills_from_dir(project_dir, source="project"))

    return loaded


# ---------------------------------------------------------------------------
# 系统提示词段落
# ---------------------------------------------------------------------------


def build_skills_prompt_section() -> str:
    """为系统提示词构建技能列表段落。

    列出可用技能，以便模型知道可以通过 ``/skill-name`` 调用哪些技能。
    """
    skills = list_skills(user_invocable_only=False)
    if not skills:
        return ""

    lines = ["# 可用技能", ""]
    for s in skills:
        desc = s.description or "（无描述）"
        line = f"- /{s.name}: {desc}"
        if s.when_to_use:
            line += f" — {s.when_to_use}"
        lines.append(line)

    return "\n".join(lines)
