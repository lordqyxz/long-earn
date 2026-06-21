"""算子基类与装饰器

算子目录的核心抽象。每个算子是一个实现 :class:`Operator` 接口的类，用
``@operator`` 装饰后放入 ``operators/<category>/`` 目录即被自动扫描注册。

设计要点
--------
- **因果性 (causality) 一等公民**：每个算子必须声明 ``causal=True``，表示其
  在任意时刻 ``t`` 的输出仅依赖 ``timestamp <= t`` 的数据，绝不窥探未来。
  这是量化回测金融级可信的根基——算子目录通过 ``causal`` 类属性 + 因果性
  证明测试 (见 ``test_operators/test_causality.py``) 在架构层面杜绝未来函数。
- **参数用 Pydantic**：LLM 引用算子时目录能直接吐出 JSON Schema；填错参数在
  解析期被拦下，根本进不到回测，从而退役 refine 循环。
- **输入输出均为 polars**：与引擎主循环一致，避免 pandas↔polars 胶水。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel

if TYPE_CHECKING:
    import polars as pl


class OperatorParams(BaseModel):
    """算子参数基类。

    每个算子定义自己的子类，声明字段名 / 类型 / 默认值 / 约束。
    解析策略 DSL 时用其校验 LLM 填入的 params。
    """

    model_config = {"extra": "forbid"}


class OperatorContractError(Exception):
    """算子契约违反（缺少必要类属性、类型不符、causal=False 等）。"""

    pass


class Operator(ABC):
    """算子基类。

    所有算子必须满足以下契约（由 :mod:`long_earn.backtest.operators._loader`
    在加载期强制校验，违反即启动抛错）：

    - ``name`` : ``str``，全局唯一，与目录引用名一致。
    - ``category`` : ``str``，取值见 :data:`VALID_CATEGORIES`。
    - ``inputs`` : ``list[str]``，依赖的输入列名（如 ``["close"]``）。
    - ``params_cls`` : :class:`OperatorParams` 子类。
    - ``causal`` : ``bool``，恒为 ``True``。算子目录禁止非因果算子；任何需要
      未来数据的逻辑都不允许进入目录（金融级可信硬约束）。
    - ``apply`` : 实现具体计算。

    算子按"面板 (panel)"语义工作：输入 ``pl.DataFrame`` 含 ``timestamp`` /
    ``symbol`` 两列及若干数据列。时序算子应在 ``over("symbol")`` 下按
    ``timestamp`` 升序做位移 / 滚动，保证只回溯历史。
    """

    # ── 类属性契约 ──────────────────────────────────────────────────────
    name: ClassVar[str] = ""
    category: ClassVar[str] = ""
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = OperatorParams
    causal: ClassVar[bool] = True
    """恒为 True：算子目录禁止非因果算子。"""

    # 算子所需的最小历史窗口长度（用于策略层判断数据是否充足），0 表示无要求。
    min_history: ClassVar[int] = 0

    @abstractmethod
    def apply(
        self, panel: "pl.DataFrame", params: OperatorParams
    ) -> "pl.Series | pl.DataFrame":
        """执行算子。

        Args:
            panel: 面板数据，至少含 ``timestamp`` / ``symbol`` 及
                :attr:`inputs` 声明的列。时序算子必须在 ``over("symbol")`` 下
                按 ``timestamp`` 升序回溯，**禁止读取 timestamp > 当前行时刻
                的数据**（因果性硬约束）。
            params: 已校验的参数对象。

        Returns:
            - factor / technical 类：返回 :class:`pl.Series`（与 panel 等长，
              对齐到 panel 行序），新增一列。
            - filter 类：返回布尔 :class:`pl.Series`（行选择掩码）。
            - rank 类：返回带 ``rank`` 列的 :class:`pl.DataFrame`（按当前截面
              排序）。
        """
        raise NotImplementedError

    # ── 公共辅助 ────────────────────────────────────────────────────────
    @classmethod
    def param_schema(cls) -> dict:
        """返回参数 JSON Schema（供 LLM function calling / 目录展示）。"""
        return cls.params_cls.model_json_schema()


VALID_CATEGORIES: frozenset[str] = frozenset(
    {"factor", "filter", "rank", "compose", "technical"}
)


def operator(cls: type[Operator]) -> type[Operator]:
    """算子装饰器：标记一个类为可被自动扫描注册的算子。

    仅做标记 + 基本结构校验；完整契约校验在 ``_loader`` 加载期统一执行
    （避免装饰期触发 import 副作用导致的循环依赖）。
    """

    if not issubclass(cls, Operator):
        raise OperatorContractError(f"@operator 只能装饰 Operator 子类，得到 {cls!r}")
    cls._is_operator = True  # type: ignore[attr-defined]
    return cls


def validate_contract(cls: type[Operator]) -> None:
    """对单个算子类执行完整契约校验（加载期调用）。

    任何违反均抛 :class:`OperatorContractError`，让问题在启动时立即暴露，
    而非在回测中静默退化。
    """

    if not cls.name:
        raise OperatorContractError(f"{cls.__name__} 缺少 name 类属性")
    if cls.category not in VALID_CATEGORIES:
        raise OperatorContractError(
            f"{cls.__name__}.category={cls.category!r} 非法，"
            f"允许: {sorted(VALID_CATEGORIES)}"
        )
    if not isinstance(cls.inputs, list) or not all(
        isinstance(x, str) for x in cls.inputs
    ):
        raise OperatorContractError(f"{cls.__name__}.inputs 必须是 list[str]")
    if not (
        isinstance(cls.params_cls, type) and issubclass(cls.params_cls, OperatorParams)
    ):
        raise OperatorContractError(
            f"{cls.__name__}.params_cls 必须是 OperatorParams 子类"
        )
    if not cls.causal:
        raise OperatorContractError(
            f"{cls.__name__}.causal=False：算子目录禁止非因果算子"
            "（未来函数是金融级可信的硬红线）。"
            "任何需要未来数据的逻辑都不允许进入算子目录。"
        )
    if not isinstance(cls.min_history, int) or cls.min_history < 0:
        raise OperatorContractError(f"{cls.__name__}.min_history 必须是非负整数")
