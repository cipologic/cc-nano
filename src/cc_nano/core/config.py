from __future__ import annotations

import os
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .llm import (default_companion_model, default_max_tokens_for_provider,
                  default_model_for_provider, validate_provider)

load_dotenv()

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = default_model_for_provider(DEFAULT_PROVIDER)
# DeepSeek-V4 模型的最大输出限制。根据 DeepSeek V4 API 文档，max_tokens 的取值范围为 1 到 131072，
# 但最大输出长度实际可达 384K tokens。
_DEEPSEEK_V4_MAX_TOKENS = 131072
_OPENAI_FALLBACK_MAX_TOKENS = default_max_tokens_for_provider("openai")
# 模型别名映射
_MODEL_ALIASES = {
    "deepseek-v4-pro": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-pro": "deepseek-v4-pro",  # 短别名，方便使用
    "deepseek-flash": "deepseek-v4-flash",  # 短别名，方便使用
    "deepseek": "deepseek-v4-pro",  # 默认别名指向 Pro 版本
}
# 按前缀匹配，值来自官方 getModelMaxOutputTokens()
_MODEL_MAX_TOKENS = (
    ("deepseek-v4-pro", _DEEPSEEK_V4_MAX_TOKENS),
    ("deepseek-v4-flash", _DEEPSEEK_V4_MAX_TOKENS),
)
_ENV_MODEL = "CC_NANO_MODEL"
_ENV_MAX_TOKENS = "CC_NANO_MAX_TOKENS"
_ENV_MEMORY_DIR = "CC_NANO_MEMORY_DIR"
_ENV_PROVIDER = "CC_NANO_PROVIDER"
_ENV_EFFORT = "CC_NANO_EFFORT"
_ENV_BUDDY_MODEL = "CC_NANO_BUDDY_MODEL"


@dataclass(frozen=True)
class AppConfig:
    """应用配置数据类"""

    provider: str
    api_key: str | None
    base_url: str | None
    model: str
    max_tokens: int
    effort: str | None = None
    buddy_model: str | None = None
    memory_dir: Path = Path.cwd() / ".config" / "cc-nano" / "memory"
    dream_interval_hours: float = 24.0
    dream_min_sessions: int = 5
    auto_dream: bool = True


def resolve_model(model: str | None, provider: str = DEFAULT_PROVIDER) -> str:
    """解析模型名称，支持别名和默认值"""
    provider = validate_provider(provider)
    if not model:
        return default_model_for_provider(provider)
    normalized = model.strip()
    if provider != "openai":
        return normalized
    return _MODEL_ALIASES.get(normalized, normalized)


def default_max_tokens_for_model(
    model: str | None,
    provider: str = DEFAULT_PROVIDER,
) -> int:
    """返回指定模型和提供商的默认最大令牌数"""
    provider = validate_provider(provider)
    resolved = resolve_model(model, provider=provider)
    if provider == "openai":
        # 首先检查是否匹配 DeepSeek-V4 模型
        for prefix, limit in _MODEL_MAX_TOKENS:
            if resolved.startswith(prefix):
                return limit
        # 然后检查 OpenAI 的其他模型限制
        openai_limits = (
            ("gpt-5", 8192),
            ("gpt-4.1", 16384),
            ("gpt-4o", 16384),
            ("o1", 32768),
            ("o3", 32768),
            ("o4", 32768),
        )
        for prefix, limit in openai_limits:
            if resolved.startswith(prefix):
                return limit
        return _OPENAI_FALLBACK_MAX_TOKENS

    return _OPENAI_FALLBACK_MAX_TOKENS


def load_app_config(args: Namespace, project_root: Path) -> AppConfig:
    """从项目根目录的 .cc-nano.toml 加载配置，合并环境变量和命令行参数。"""
    # 确定配置文件路径：args.config 或 project_root/.cc-nano.toml
    config_path = None
    if hasattr(args, "config") and args.config:
        config_path = Path(args.config).expanduser()
    else:
        config_path = project_root / ".cc-nano.toml"
    # 读取文件值（只读取这一个文件，不再搜索其他路径）
    file_values, _ = _load_file_values(
        str(config_path) if config_path.exists() else None
    )
    env_values = _load_env_values()

    raw_provider = (
        getattr(args, "provider", None)
        or env_values.get("provider")
        or file_values["top"].get("provider")
    )
    provider = validate_provider(
        raw_provider or _infer_provider(file_values["providers"])
    )

    selected_provider_values = file_values["providers"].get(provider, {})
    selected_env_values = _provider_env_values(env_values, provider)

    def _file_value(key: str) -> Any:
        if key in file_values["top"]:
            return file_values["top"][key]
        return selected_provider_values.get(key)

    raw_model = args.model or env_values.get("model") or _file_value("model")
    model = resolve_model(raw_model, provider=provider)

    raw_max_tokens = (
        args.max_tokens
        if args.max_tokens is not None
        else env_values.get("max_tokens", _file_value("max_tokens"))
    )
    max_tokens = _parse_max_tokens(
        raw_max_tokens,
        default=default_max_tokens_for_model(model, provider=provider),
    )

    raw_effort = getattr(args, "effort", None)
    if raw_effort is None:
        raw_effort = env_values.get("effort", _file_value("effort"))
    effort = _parse_effort(raw_effort)

    raw_buddy_model = getattr(args, "buddy_model", None)
    if raw_buddy_model is None:
        raw_buddy_model = env_values.get("buddy_model", _file_value("buddy_model"))
    buddy_model = (
        resolve_model(raw_buddy_model, provider=provider) if raw_buddy_model else None
    )

    raw_memory_dir = (
        getattr(args, "memory_dir", None)
        or env_values.get("memory_dir")
        or _file_value("memory_dir")
    )
    if raw_memory_dir:
        memory_dir = Path(raw_memory_dir).expanduser()
    else:
        memory_dir = project_root / ".config" / "cc-nano" / "memory"

    raw_dream_interval = getattr(args, "dream_interval", None)
    if raw_dream_interval is None:
        raw_dream_interval = env_values.get("dream_interval") or env_values.get(
            "dream_interval_hours"
        )
    if raw_dream_interval is None:
        raw_dream_interval = _file_value("dream_interval") or _file_value(
            "dream_interval_hours"
        )
    dream_interval = (
        float(raw_dream_interval) if raw_dream_interval is not None else 24.0
    )

    raw_dream_min = getattr(args, "dream_min_sessions", None)
    if raw_dream_min is None:
        raw_dream_min = env_values.get(
            "dream_min_sessions", _file_value("dream_min_sessions")
        )
    dream_min_sessions = int(raw_dream_min) if raw_dream_min is not None else 5
    auto_dream = True
    raw_auto_dream = env_values.get("auto_dream", _file_value("auto_dream"))
    if raw_auto_dream is not None:
        auto_dream = str(raw_auto_dream).lower() not in ("false", "0", "no")
    else:
        auto_dream = True  # 默认值
    if getattr(args, "no_auto_dream", False):
        auto_dream = False

    return AppConfig(
        provider=provider,
        api_key=args.api_key
        or selected_env_values.get("api_key")
        or _file_value("api_key"),
        base_url=args.base_url
        or selected_env_values.get("base_url")
        or _file_value("base_url"),
        model=model,
        max_tokens=max_tokens,
        effort=effort,
        buddy_model=buddy_model or default_companion_model(provider, model),
        memory_dir=memory_dir,
        dream_interval_hours=dream_interval,
        dream_min_sessions=dream_min_sessions,
        auto_dream=auto_dream,
    )


def _load_file_values(
    explicit_path: str | None,
) -> tuple[dict[str, Any], tuple[Path, ...]]:
    """从指定的 TOML 文件加载配置，如果 explicit_path 为 None 则返回空。"""
    values: dict[str, Any] = {
        "top": {},
        "providers": {"openai": {}, "anthropic": {}},
    }
    loaded_paths: list[Path] = []

    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.exists():
            raise ValueError(f"配置文件未找到: {path}")
        _merge_file_values(values, _read_config_file(path))
        loaded_paths.append(path)

    return values, tuple(loaded_paths)


def _read_config_file(path: Path) -> dict[str, Any]:
    """读取单个 TOML 配置文件，返回标准化结构"""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"配置文件 {path} 中的 TOML 无效: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"无法读取配置文件 {path}: {exc}") from exc

    values: dict[str, Any] = {
        "top": {},
        "providers": {"openai": {}},
    }

    for provider in ("openai",):
        section = data.get(provider, {})
        if isinstance(section, dict):
            values["providers"][provider].update(section)

    for key in (
        "provider",
        "api_key",
        "base_url",
        "model",
        "max_tokens",
        "effort",
        "buddy_model",
        "memory_dir",
        "dream_interval_hours",
        "dream_min_sessions",
        "auto_dream",
    ):
        if key in data:
            values["top"][key] = data[key]

    return values


def _load_env_values() -> dict[str, Any]:
    """从环境变量加载配置值"""
    values: dict[str, Any] = {}
    if os.getenv(_ENV_PROVIDER):
        values["provider"] = os.environ[_ENV_PROVIDER]
    if os.getenv("OPENAI_API_KEY"):
        values["openai_api_key"] = os.environ["OPENAI_API_KEY"]
    if os.getenv("OPENAI_BASE_URL"):
        values["openai_base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.getenv("ANTHROPIC_API_KEY"):
        values["anthropic_api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.getenv("ANTHROPIC_BASE_URL"):
        values["anthropic_base_url"] = os.environ["ANTHROPIC_BASE_URL"]
    if os.getenv(_ENV_MODEL):
        values["model"] = os.environ[_ENV_MODEL]
    if os.getenv(_ENV_MAX_TOKENS):
        values["max_tokens"] = os.environ[_ENV_MAX_TOKENS]
    if os.getenv(_ENV_MEMORY_DIR):
        values["memory_dir"] = os.environ[_ENV_MEMORY_DIR]
    if os.getenv(_ENV_EFFORT):
        values["effort"] = os.environ[_ENV_EFFORT]
    if os.getenv(_ENV_BUDDY_MODEL):
        values["buddy_model"] = os.environ[_ENV_BUDDY_MODEL]
    if os.getenv("CC_NANO_DREAM_INTERVAL"):
        values["dream_interval"] = os.environ["CC_NANO_DREAM_INTERVAL"]
    if os.getenv("CC_NANO_DREAM_INTERVAL_HOURS"):
        values["dream_interval_hours"] = os.environ["CC_NANO_DREAM_INTERVAL_HOURS"]
    if os.getenv("CC_NANO_DREAM_MIN_SESSIONS"):
        values["dream_min_sessions"] = os.environ["CC_NANO_DREAM_MIN_SESSIONS"]
    return values


def _parse_max_tokens(raw_value: Any, default: int) -> int:
    """解析 max_tokens 值，必须为正整数"""
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无效的 max_tokens 值: {raw_value!r}") from exc

    if value <= 0:
        raise ValueError("max_tokens 必须是正整数")
    return value


def _parse_effort(raw_value: Any) -> str | None:
    """解析 effort 值，必须为 low/medium/high"""
    if raw_value is None:
        return None
    normalized = str(raw_value).strip().lower()
    if normalized not in ("low", "medium", "high"):
        raise ValueError("effort 必须是以下之一: low, medium, high")
    return normalized


def _infer_provider(provider_values: dict[str, dict[str, Any]]) -> str:
    """根据配置文件内容推断默认提供商"""
    openai_values = provider_values.get("openai", {})
    if openai_values:
        return "openai"
    return DEFAULT_PROVIDER


def _merge_file_values(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    """合并两个文件值字典"""
    target["top"].update(incoming.get("top", {}))
    for provider in ("openai", "anthropic"):
        target["providers"][provider].update(
            incoming.get("providers", {}).get(provider, {})
        )


def _provider_env_values(env_values: dict[str, Any], provider: str) -> dict[str, Any]:
    """根据提供商提取对应的环境变量值"""
    provider = validate_provider(provider)
    if provider == "openai":
        return {
            "api_key": env_values.get("openai_api_key"),
            "base_url": env_values.get("openai_base_url"),
        }
    # Anthropic provider
    return {
        "api_key": env_values.get("anthropic_api_key"),
        "base_url": env_values.get("anthropic_base_url"),
    }
