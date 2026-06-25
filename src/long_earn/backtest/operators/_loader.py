"""算子目录自动扫描器与契约校验

首次 ``import long_earn.backtest.operators`` 时递归扫描 ``operators/`` 下所有
``*.py``（跳过 ``_`` 前缀文件），收集带 ``@operator`` 装饰的类，逐一做契约
校验，按 ``name`` 注册进 :data:`OPERATOR_REGISTRY`。

扫描规则定死（见 ``plans/new backtest.md``）：

- 扫描范围：``operators/`` 下所有 ``*.py``（递归），跳过 ``_`` 前缀文件。
- 识别标记：模块内带 ``@operator`` 装饰器的类。
- 算子名来源：``Operator.name`` 类属性。
- 冲突处理：两个算子 ``name`` 撞了 → 启动即抛错（静默覆盖最危险）。
- 加载时机：首次 import 本模块时扫描一次，缓存进 ``OPERATOR_REGISTRY``。
- 契约校验：见 :func:`long_earn.backtest.operators.base.validate_contract`。
- 热注册：``register_operator(op)`` 写入当前进程注册表，供同进程后续使用；
  新进程靠启动扫描自然生效。

按文件路径字母序加载，保证可复现、避免顺序相关的初始化竞态。

加载方式：用模块的**规范 dotted 路径**（如 ``long_earn.backtest.operators.factor.shift``）
经 ``importlib.import_module`` 加载，保证算子类在全进程内有唯一身份——
``isinstance`` / Pydantic 校验不会因重复类定义而失效。各 ``<category>/__init__.py``
保持极简（不链式 import 算子），避免循环依赖。
"""

import importlib
from pathlib import Path
from typing import Any

from loguru import logger

from long_earn.backtest.operators.base import (
    Operator,
    OperatorContractError,
    validate_contract,
)

_REGISTRY_DIR = Path(__file__).resolve().parent
# 算子包的规范 dotted 前缀（与本文件在源码树中的位置一致）
_PACKAGE = "long_earn.backtest.operators"
# 算子注册表：name -> Operator 实例。单进程内单一事实源。
OPERATOR_REGISTRY: dict[str, Operator] = {}


class OperatorNotFoundError(KeyError):
    """引用了未注册的算子名。"""

    pass


def _dotted_name(path: Path) -> str:
    """把算子文件路径转换为规范 dotted 模块名。

    如 ``.../operators/factor/shift.py`` -> ``long_earn.backtest.operators.factor.shift``。
    """

    rel = path.relative_to(_REGISTRY_DIR).with_suffix("")
    parts = rel.parts
    return f"{_PACKAGE}.{'.'.join(parts)}"


def _load_module(dotted: str) -> Any:
    """按规范 dotted 路径加载算子模块（保证类身份唯一）。"""

    try:
        return importlib.import_module(dotted)
    except Exception as exc:
        raise OperatorContractError(
            f"加载算子模块 {dotted} 失败: {type(exc).__name__}: {exc}"
        ) from exc


def _discover_operator_classes(module: Any) -> list[type[Operator]]:
    """从模块中收集带 ``@operator`` 标记的类。"""

    found: list[type[Operator]] = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, Operator)
            and getattr(attr, "_is_operator", False)
            and attr is not Operator
        ):
            found.append(attr)
    return found


def _scan_directory(directory: Path) -> None:
    """递归扫描目录，按字母序加载算子模块并注册。"""

    py_files = sorted(directory.rglob("*.py"))
    for path in py_files:
        # 跳过 __init__.py 与 _ 前缀文件（_loader.py / _util.py 等）
        if path.name.startswith("_"):
            continue
        dotted = _dotted_name(path)
        module = _load_module(dotted)
        for cls in _discover_operator_classes(module):
            _register_class(cls)


def _register_class(cls: type[Operator]) -> None:
    """契约校验 + 冲突检测 + 实例化注册。"""

    validate_contract(cls)
    if cls.name in OPERATOR_REGISTRY:
        existing = type(OPERATOR_REGISTRY[cls.name]).__name__
        raise OperatorContractError(
            f"算子名冲突: {cls.name} 同时由 {existing} 与 {cls.__name__} 定义"
            "（算子名必须全局唯一，静默覆盖最危险）。"
        )
    try:
        instance = cls()
    except Exception as exc:
        raise OperatorContractError(
            f"算子 {cls.name} 实例化失败: {type(exc).__name__}: {exc}"
        ) from exc
    OPERATOR_REGISTRY[cls.name] = instance
    logger.debug("已注册算子: %s (%s)", cls.name, cls.category)


def register_operator(op: Operator) -> None:
    """运行期热注册一个算子实例（写盘后让当进程立即可用）。

    用于算子开发子图 ``register`` 节点：写 ``.py`` 后调用本函数内存热注册，
    无需等下次启动扫描。跨进程一致性靠下次启动收敛。
    """

    cls = type(op)
    validate_contract(cls)
    if cls.name in OPERATOR_REGISTRY and type(OPERATOR_REGISTRY[cls.name]) is not cls:
        raise OperatorContractError(
            f"热注册冲突: {cls.name} 已由 {type(OPERATOR_REGISTRY[cls.name]).__name__} 占用"
        )
    OPERATOR_REGISTRY[cls.name] = op


def get_operator(name: str) -> Operator:
    """按名取算子；不存在抛 :class:`OperatorNotFoundError`。"""

    if name not in OPERATOR_REGISTRY:
        raise OperatorNotFoundError(
            f"未知算子 '{name}'，已注册: {sorted(OPERATOR_REGISTRY)}"
        )
    return OPERATOR_REGISTRY[name]


def list_operators() -> dict[str, dict[str, Any]]:
    """返回目录清单（name -> {category, inputs, params_schema, min_history}）。

    供 LLM function calling / dashboard 展示 / 策略研发检索。
    """

    return {
        name: {
            "category": type(op).category,
            "inputs": list(type(op).inputs),
            "params_schema": type(op).param_schema(),
            "min_history": type(op).min_history,
        }
        for name, op in OPERATOR_REGISTRY.items()
    }


def _bootstrap() -> None:
    """首次 import 时扫描一次。"""

    if OPERATOR_REGISTRY:
        return
    _scan_directory(_REGISTRY_DIR)


_bootstrap()
