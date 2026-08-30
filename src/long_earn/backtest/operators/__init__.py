"""算子目录对外接口

import 本包即触发一次自动扫描，把 ``operators/<category>/*.py`` 中带
``@operator`` 装饰的类注册进 :data:`OPERATOR_REGISTRY`。

典型用法::

    from long_earn.backtest.operators import get_operator, list_operators

    op = get_operator("shift")
    series = op.apply(panel, op.params_cls(field="close", periods=20))
"""

from long_earn.backtest.operators._loader import (
    OPERATOR_REGISTRY,
    OPERATOR_RENAMES,
    PROOF_REGISTRY,
    OperatorNotFoundError,
    get_operator,
    get_operator_proof,
    list_operators,
    register_operator,
)
from long_earn.backtest.operators.base import (
    VALID_CATEGORIES,
    Operator,
    OperatorContractError,
    OperatorParams,
    operator,
    validate_contract,
)
from long_earn.backtest.operators.causality import CausalityProof, PerturbStrategy

__all__ = [
    "OPERATOR_REGISTRY",
    "OPERATOR_RENAMES",
    "PROOF_REGISTRY",
    "VALID_CATEGORIES",
    "CausalityProof",
    "Operator",
    "OperatorContractError",
    "OperatorNotFoundError",
    "OperatorParams",
    "PerturbStrategy",
    "get_operator",
    "get_operator_proof",
    "list_operators",
    "operator",
    "register_operator",
    "validate_contract",
]
