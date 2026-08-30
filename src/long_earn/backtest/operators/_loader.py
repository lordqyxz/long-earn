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
- 因果性门：注册是 fail-closed 唯一入口，:func:`prove_registration_causality`
  数值证明不过即注册失败（AUDIT-P3-10）；通过的证明对象留存进
  :data:`PROOF_REGISTRY`，作为"注册附带 prove_causality 报告"的可审计事实。
- 热注册：``register_operator(op)`` 写入当前进程注册表，供同进程后续使用；
  新进程靠启动扫描自然生效。

按文件路径字母序加载，保证可复现、避免顺序相关的初始化竞态。

加载方式：用模块的**规范 dotted 路径**（如 ``long_earn.backtest.operators.factor.shift``）
经 ``importlib.import_module`` 加载，保证算子类在全进程内有唯一身份——
``isinstance`` / Pydantic 校验不会因重复类定义而失效。各 ``<category>/__init__.py``
保持极简（不链式 import 算子），避免循环依赖。
"""

import hashlib
import importlib
from pathlib import Path
from typing import Any

from loguru import logger

from long_earn.backtest.operators.base import (
    Operator,
    OperatorContractError,
    validate_contract,
)
from long_earn.backtest.operators.causality import (
    CausalityProof,
    prove_registration_causality,
    validate_causality_proof,
)

_REGISTRY_DIR = Path(__file__).resolve().parent
# 算子包的规范 dotted 前缀（与本文件在源码树中的位置一致）
_PACKAGE = "long_earn.backtest.operators"
# 算子注册表：name -> Operator 实例。单进程内单一事实源。
OPERATOR_REGISTRY: dict[str, Operator] = {}
# 因果性证明注册表：name -> CausalityProof。与 OPERATOR_REGISTRY 并行维护，
# 记录每个已注册算子在注册期通过/验证的因果性证明（AUDIT-P3-10：注册强制
# 附带 prove_causality 报告，报告以结构化 CausalityProof 对象留存可查）。
PROOF_REGISTRY: dict[str, CausalityProof] = {}

# 已退役算子名 → 新名（名实不符清理）。get_operator 对旧名抛明确迁移错误，
# 不静默别名，避免 YAML 继续引用误导性 ID。
OPERATOR_RENAMES: dict[str, str] = {
    "roe_quality": "return_quality",
    "gross_margin_stability": "price_stability",
}

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
    """契约校验 + 冲突检测 + 实例化 + 因果性证明 + 注册。

    注册是 fail-closed 的唯一入口：证明缺失/无效（数值证明不通过）即抛错，
    绝不让缺证明的算子进入注册表。证明对象同时留存进 :data:`PROOF_REGISTRY`，
    使"注册附带 prove_causality 报告"成为可审计事实。
    """

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
    try:
        proof = prove_registration_causality(instance)
    except ValueError as exc:
        raise OperatorContractError(str(exc)) from exc
    OPERATOR_REGISTRY[cls.name] = instance
    PROOF_REGISTRY[cls.name] = proof
    logger.debug(f"已注册算子: {cls.name} ({cls.category})")


def register_operator(
    op: Operator,
    source_code: str = "",
    category: str = "",
    *,
    causality_proof: CausalityProof | None = None,
) -> None:
    """运行期热注册一个算子实例（写盘后让当进程立即可用）。

    用于算子开发子图 ``register`` 节点：可选写盘 + 内存热注册，
    无需等下次启动扫描。跨进程一致性靠下次启动收敛。

    Args:
        op: 已实例化的算子实例
        source_code: 算子源码。非空时写入 ``operators/<category>/<name>.py``，
            下次启动 ``_bootstrap`` 扫描会发现该文件并自动注册。
            为空时只做内存热注册（重启丢失）。
        category: 算子类别（factor/filter/rank/compose/technical），
            决定写盘子目录。``source_code`` 非空时必填。
        causality_proof: 可选的实现绑定证明。operator_dev 可复用验证节点产出的证明，
            避免重复数值验证；缺省时本函数同步执行完整注册证明。
            无论走哪条路径，通过的证明都会留存进 :data:`PROOF_REGISTRY`。
    """

    cls = type(op)
    validate_contract(cls)
    try:
        if causality_proof is None:
            proof = prove_registration_causality(op)
        else:
            validate_causality_proof(op, causality_proof)
            proof = causality_proof
    except ValueError as exc:
        raise OperatorContractError(str(exc)) from exc
    if cls.name in OPERATOR_REGISTRY and type(OPERATOR_REGISTRY[cls.name]) is not cls:
        raise OperatorContractError(
            f"热注册冲突: {cls.name} 已由 {type(OPERATOR_REGISTRY[cls.name]).__name__} 占用"
        )

    # 写盘：把 LLM 生成的算子源码落到 operators/<category>/<name>.py
    # 下次进程启动时 _bootstrap() 扫描会自动发现并注册，实现跨进程持久化。
    if source_code and category:
        target_dir = _REGISTRY_DIR / category
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{cls.name}.py"
        if not target_file.exists():
            target_file.write_text(source_code, encoding="utf-8")
            logger.info(
                f"算子 {cls.name} 源码已写盘: {target_file}（下次启动自动扫描注册）"
            )
        else:
            existing = target_file.read_text(encoding="utf-8")
            disk_hash = hashlib.sha256(existing.encode()).hexdigest()[:12]
            new_hash = hashlib.sha256(source_code.encode()).hexdigest()[:12]
            if existing != source_code:
                logger.warning(
                    f"算子 {cls.name} 源码文件已存在且内容不同，跳过写盘"
                    f"（磁盘指纹 {disk_hash} ≠ 新源码 {new_hash}）；"
                    "内存热注册与磁盘实现可能漂移，请人工合并或删除旧文件"
                )
            else:
                logger.warning(
                    f"算子 {cls.name} 源码文件已存在，跳过写盘: {target_file}"
                )

    OPERATOR_REGISTRY[cls.name] = op
    PROOF_REGISTRY[cls.name] = proof


def get_operator(name: str) -> Operator:
    """按名取算子；不存在抛 :class:`OperatorNotFoundError`。

    若 ``name`` 属于 :data:`OPERATOR_RENAMES`，抛错并提示应改用的新名
    （须同步改策略 YAML 的 ``op`` 字段，见 AGENTS.md 算子更名约定）。
    """

    if name in OPERATOR_RENAMES:
        new_name = OPERATOR_RENAMES[name]
        raise OperatorNotFoundError(
            f"算子 '{name}' 已更名为 '{new_name}'（名实不符清理）；"
            f"请将策略 YAML 中 op: {name} 改为 op: {new_name}"
        )
    if name not in OPERATOR_REGISTRY:
        raise OperatorNotFoundError(
            f"未知算子 '{name}'，已注册: {sorted(OPERATOR_REGISTRY)}"
        )
    return OPERATOR_REGISTRY[name]


def get_operator_proof(name: str) -> CausalityProof | None:
    """按名取注册期因果性证明；未注册或无证明返回 None。

    AUDIT-P3-10：注册强制附带 prove_causality 报告，证明以结构化
    :class:`CausalityProof` 对象留存于 :data:`PROOF_REGISTRY`，供运行时
    审计/复核（如校验实现指纹是否仍与注册时一致）。
    """

    return PROOF_REGISTRY.get(name)


def list_operators() -> dict[str, dict[str, Any]]:
    """返回目录清单（name -> {category, inputs, field_params, params_schema, min_history}）。

    供 LLM function calling / dashboard 展示 / 策略研发检索 / 连接器按需取数。
    ADR-014 任务3：新增 ``field_params``（params 中承载列名的键）。
    """

    return {
        name: {
            "category": type(op).category,
            "inputs": list(type(op).inputs),
            "field_params": list(type(op).field_params),
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
