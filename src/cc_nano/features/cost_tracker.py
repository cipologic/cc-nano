"""Token 用量与成本追踪。

已适配 DeepSeek V4 系列的定价与模型。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 每 token 定价（人民币/token）
# ---------------------------------------------------------------------------

# DeepSeek-V4-Flash 定价（单位：元/百万 token）
_PRICE_V4_FLASH_INPUT = 1.0 / 1_000_000  # 缓存未命中输入，1 元/百万 token [reference:0]
_PRICE_V4_FLASH_OUTPUT = 2.0 / 1_000_000  # 输出，2 元/百万 token
_PRICE_V4_FLASH_CACHE_READ = (
    0.02 / 1_000_000
)  # 缓存命中，0.02 元/百万 token [reference:1]
_PRICE_V4_FLASH_CACHE_WRITE = 1.0 / 1_000_000  # 缓存写入（与输入同价）

# DeepSeek-V4-Pro 定价（单位：元/百万 token）
_PRICE_V4_PRO_INPUT = 12.0 / 1_000_000  # 缓存未命中输入，12 元/百万 token [reference:2]
_PRICE_V4_PRO_OUTPUT = 24.0 / 1_000_000  # 输出，24 元/百万 token
_PRICE_V4_PRO_CACHE_READ = (
    0.025 / 1_000_000
)  # 缓存命中，限时 0.025 元/百万 token [reference:3]
_PRICE_V4_PRO_CACHE_WRITE = 12.0 / 1_000_000  # 缓存写入（与输入同价）

# 原始定价（未折扣），用于记录或优惠期结束后使用
_PRICE_V4_PRO_CACHE_READ_ORIGINAL = (
    0.1 / 1_000_000
)  # 原始缓存命中价，0.1 元/百万 token [reference:4]
_PRICE_V4_PRO_INPUT_ORIGINAL = 12.0 / 1_000_000
_PRICE_V4_PRO_OUTPUT_ORIGINAL = 24.0 / 1_000_000


@dataclass(frozen=True)
class _PricingTier:
    input: float
    output: float
    cache_write: float
    cache_read: float


# DeepSeek V4 系列的定价档位
_PRICE_DS_V4_FLASH = _PricingTier(
    input=_PRICE_V4_FLASH_INPUT,
    output=_PRICE_V4_FLASH_OUTPUT,
    cache_write=_PRICE_V4_FLASH_CACHE_WRITE,
    cache_read=_PRICE_V4_FLASH_CACHE_READ,
)

_PRICE_DS_V4_PRO = _PricingTier(
    input=_PRICE_V4_PRO_INPUT,
    output=_PRICE_V4_PRO_OUTPUT,
    cache_write=_PRICE_V4_PRO_CACHE_WRITE,
    cache_read=_PRICE_V4_PRO_CACHE_READ,
)


# ---------------------------------------------------------------------------
# 模型注册表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelInfo:
    """已知模型的元数据。"""

    id: str
    provider: str
    pricing_tier: _PricingTier
    deprecated: bool = False
    deprecation_message: str = ""
    hugging_face_id: str = ""


# 模型注册表：只包含 DeepSeek V4 系列
MODEL_REGISTRY: dict[str, ModelInfo] = {
    "deepseek-v4-flash": ModelInfo(
        id="deepseek-v4-flash",
        provider="deepseek",
        pricing_tier=_PRICE_DS_V4_FLASH,
        hugging_face_id="deepseek-ai/DeepSeek-V4-Flash",
    ),
    "deepseek-v4-pro": ModelInfo(
        id="deepseek-v4-pro",
        provider="deepseek",
        pricing_tier=_PRICE_DS_V4_PRO,
        hugging_face_id="deepseek-ai/DeepSeek-V4-Pro",
    ),
}


class ModelRegistry:
    """管理模型信息和定价的类"""

    # 模型前缀到默认定价档位的映射（用于未在 MODEL_REGISTRY 中注册的模型）
    _PRICING_PREFIXES: list[tuple[str, _PricingTier]] = [
        ("deepseek-v4-flash", _PRICE_DS_V4_FLASH),
        ("deepseek-v4-pro", _PRICE_DS_V4_PRO),
        ("deepseek-chat", _PRICE_DS_V4_FLASH),  # 兼容旧模型名 deepseek-chat
        ("deepseek-reasoner", _PRICE_DS_V4_PRO),  # 兼容旧模型名 deepseek-reasoner
    ]

    @classmethod
    def get_model_info(cls, model: str) -> ModelInfo | None:
        """通过精确匹配从注册表中查找模型信息。"""
        model_lower = model.lower()
        # 首先尝试精确匹配
        if model_lower in MODEL_REGISTRY:
            return MODEL_REGISTRY[model_lower]
        # 如果没有精确匹配，则使用前缀匹配
        for prefix, pricing_tier in cls._PRICING_PREFIXES:
            if model_lower.startswith(prefix):
                return ModelInfo(
                    id=model_lower, provider="deepseek", pricing_tier=pricing_tier
                )
        return None

    @classmethod
    def calculate_cost(cls, model: str, usage: dict) -> float:
        """返回单次 API 调用的成本（元）。"""
        model_info = cls.get_model_info(model)
        if model_info is None:
            return 0.0

        tier = model_info.pricing_tier
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)

        # DeepSeek API：input_tokens 已排除缓存的 token。
        # cache_read 和 cache_write 按各自的费率单独计费。
        cost = (
            inp * tier.input
            + out * tier.output
            + cache_read * tier.cache_read
            + cache_write * tier.cache_write
        )
        return cost


# ---------------------------------------------------------------------------
# 用量数据
# ---------------------------------------------------------------------------


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0  # 单位为人民币（元）
    api_duration_s: float = 0.0
    pricing_known: bool = True


# ---------------------------------------------------------------------------
# 格式化辅助函数
# ---------------------------------------------------------------------------


def _fmt_tokens(n: int) -> str:
    """与官方 CLI 风格一致，使用 k/m 后缀格式化 token 数量。"""
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}m" if v != int(v) else f"{int(v)}m"
    if n >= 1_000:
        v = n / 1_000
        return f"{v:.1f}k" if v != int(v) else f"{int(v)}k"
    return str(n)


def _fmt_duration(seconds: float) -> str:
    """将秒数格式化为 'Xh Ym Zs'、'Ym Zs' 或 'Xs'。"""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# CostTracker（成本追踪器）
# ---------------------------------------------------------------------------


class CostTracker:
    """累积多次 API 调用的 token 用量和成本。"""

    def __init__(self) -> None:
        self._total_cost_usd: float = 0.0  # 单位为人民币（元）
        self._total_api_duration_s: float = 0.0
        self._model_usage: dict[str, ModelUsage] = {}
        self._wall_start: float = time.monotonic()
        self._lines_added: int = 0
        self._lines_removed: int = 0
        self._last_input_tokens: int = 0

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost_usd

    @property
    def last_input_tokens(self) -> int:
        """最近一次 API 调用的 input_tokens（反映上下文大小）。"""
        return self._last_input_tokens

    @staticmethod
    def calculate_cost(model: str, usage: dict) -> float:
        """返回单次 API 调用的成本（元）。"""
        return ModelRegistry.calculate_cost(model, usage)

    def add_usage(self, model: str, usage: dict, api_duration_s: float = 0.0) -> float:
        """记录 token 数量，并返回本次调用的成本。"""
        cost = self.calculate_cost(model, usage)

        self._total_cost_usd += cost
        self._total_api_duration_s += api_duration_s
        self._last_input_tokens = usage.get("input_tokens", 0)

        mu = self._model_usage.setdefault(model, ModelUsage())
        mu.input_tokens += usage.get("input_tokens", 0)
        mu.output_tokens += usage.get("output_tokens", 0)
        mu.cache_read_input_tokens += usage.get("cache_read_input_tokens", 0)
        mu.cache_creation_input_tokens += usage.get("cache_creation_input_tokens", 0)
        mu.cost_usd += cost
        mu.api_duration_s += api_duration_s
        if ModelRegistry.get_model_info(model) is None:
            mu.pricing_known = False
        return cost

    def add_lines_changed(self, added: int, removed: int) -> None:
        """记录来自编辑/写入工具的代码变更（行数增减）。"""
        self._lines_added += added
        self._lines_removed += removed

    def format_cost(self) -> str:
        """人类可读的成本摘要。"""
        if not self._model_usage:
            return "未记录到 API 用量。"

        wall_s = time.monotonic() - self._wall_start
        unknown_pricing = any(not mu.pricing_known for mu in self._model_usage.values())
        lines: list[str] = []
        lines.append(f"总成本：               {self._total_cost_usd:.4f} 元")
        if unknown_pricing:
            lines.append("定价提示：             由于使用了未知模型，成本可能不准确")
        lines.append(
            f"API 总耗时：           {_fmt_duration(self._total_api_duration_s)}"
        )
        lines.append(f"实际总耗时：           {_fmt_duration(wall_s)}")
        la = self._lines_added
        lr = self._lines_removed
        lines.append(f"代码总变更：            {la} 行新增，" f"{lr} 行删除")

        # 按模型分类的用量 —— 每个模型一行紧凑显示
        lines.append("各模型用量：")

        # 右对齐模型名称
        max_name = max(len(m) for m in self._model_usage)
        for model, mu in sorted(self._model_usage.items()):
            parts: list[str] = []
            parts.append(f"{_fmt_tokens(mu.input_tokens)} 输入")
            parts.append(f"{_fmt_tokens(mu.output_tokens)} 输出")
            if mu.cache_read_input_tokens:
                parts.append(f"{_fmt_tokens(mu.cache_read_input_tokens)} 缓存读取")
            if mu.cache_creation_input_tokens:
                parts.append(f"{_fmt_tokens(mu.cache_creation_input_tokens)} 缓存写入")
            detail = ", ".join(parts)
            if not mu.pricing_known:
                detail += ", 定价不可用"
            name_pad = model.rjust(max_name)
            lines.append(f"  {name_pad}:  {detail} ({mu.cost_usd:.4f} 元)")

        return "\n".join(lines)
