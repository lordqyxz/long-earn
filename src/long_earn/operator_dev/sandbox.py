"""算子源码安全沙箱 —— AST 审计 + 受限加载。

两道关卡：
1. :func:`audit_source` —— AST 白名单审计。只允许 ``import polars`` /
   ``import numpy`` / ``from long_earn.backtest.* import``；禁止
   ``os`` / ``subprocess`` / ``socket`` / ``open`` / ``eval`` / ``exec`` /
   ``__import__`` / dunder 访问。采用**允许列表**思想：未明确允许的危险调用
   一律拒。
2. :func:`load_operator_class` —— 在隔离的模块命名空间编译执行审计通过的源码，
   提取出唯一一个 ``@operator`` 类，做契约校验后返回类对象。

**注意**：AST 审计保证代码安全（不窃取数据 / 不执行系统命令）；**无未来函数**
由 :mod:`long_earn.backtest.operators.causality` 的因果性证明在 test/validate
节点保证——两者正交，共同构成算子上线的硬约束。
"""

from __future__ import annotations

import ast
import sys
import uuid

from long_earn.backtest.operators.base import (
    Operator,
    OperatorContractError,
    validate_contract,
)


class OperatorLoadError(Exception):
    """算子源码审计 / 加载失败。"""

    pass


# ── 允许的 import 模块前缀 ──────────────────────────────────────────────
_ALLOWED_IMPORT_MODULES: frozenset[str] = frozenset(
    {"polars", "pl", "numpy", "np", "math", "typing", "dataclasses", "enum"}
)
_ALLOWED_IMPORT_PREFIXES: tuple[str, ...] = ("long_earn.backtest.",)


# ── 禁止的名称（属性 / 调用 / import）──────────────────────────────────
_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "socketserver",
        "http",
        "urllib",
        "requests",
        "shutil",
        "pathlib",
        "io",
        "pickle",
        "marshal",
        "ctypes",
        "importlib",
        "builtins",
        "globals",
        "locals",
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "breakpoint",
        "exit",
        "quit",
        "__import__",
    }
)


def _check_import(node: ast.Import | ast.ImportFrom) -> None:
    """校验 import 语句是否在白名单内。"""

    if isinstance(node, ast.Import):
        for alias in node.names:
            mod = alias.name.split(".")[0]
            _assert_module_allowed(alias.name, mod)
    else:  # ast.ImportFrom
        mod = node.module or ""
        root = mod.split(".")[0]
        _assert_module_allowed(mod, root)


def _assert_module_allowed(full: str, root: str) -> None:
    if root in _ALLOWED_IMPORT_MODULES:
        return
    if any(full.startswith(p) or root + "." == p for p in _ALLOWED_IMPORT_PREFIXES):
        return
    if root.startswith("long_earn.backtest"):
        return
    raise OperatorLoadError(
        f"禁止 import '{full}'：算子源码仅允许 polars/numpy/math/"
        "long_earn.backtest.*（允许列表策略，其余一律拒）"
    )


def _check_name(node: ast.Name) -> None:
    if node.id in _FORBIDDEN_NAMES:
        raise OperatorLoadError(f"禁止使用名称 '{node.id}'")


def _check_attribute(node: ast.Attribute) -> None:
    # 禁止 dunder 属性访问（__globals__/__class__/__subclasses__ 等逃逸路径）
    if node.attr.startswith("__") and node.attr.endswith("__"):
        raise OperatorLoadError(f"禁止访问 dunder 属性 '{node.attr}'")
    if node.attr in _FORBIDDEN_NAMES:
        raise OperatorLoadError(f"禁止访问属性 '{node.attr}'")


def _check_call(node: ast.Call) -> None:
    # 禁止直接调用 eval/exec/__import__/open 等
    if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_NAMES:
        raise OperatorLoadError(f"禁止调用 '{node.func.id}()'")
    if isinstance(node.func, ast.Attribute) and node.func.attr in _FORBIDDEN_NAMES:
        raise OperatorLoadError(f"禁止调用属性方法 '{node.func.attr}()'")


class _AuditVisitor(ast.NodeVisitor):
    """遍历 AST，对每个节点做白名单/黑名单校验。"""

    def visit_Import(self, node: ast.Import) -> None:
        _check_import(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        _check_import(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        _check_name(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        _check_attribute(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        _check_call(node)
        self.generic_visit(node)


def audit_source(source: str) -> list[str]:
    """AST 审计算子源码。通过返回发现的算子类名列表；违规则抛 OperatorLoadError。

    审计项：import 白名单、禁止名称、禁止 dunder、禁止危险调用。
    审计**不**判断因果性——那是 :mod:`causality` 的职责。
    """

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise OperatorLoadError(f"源码语法错误: {exc}") from exc

    _AuditVisitor().visit(tree)

    # 收集带 _is_operator 标记意图的类名（粗扫：所有 class 定义）
    class_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    return class_names


def load_operator_class(source: str, expected_name: str = "") -> type[Operator]:
    """审计 + 隔离编译 + 提取唯一 @operator 类 + 契约校验。

    Args:
        source: 算子源码字符串（含 ``@operator`` 装饰的类）。
        expected_name: 期望的 ``Operator.name``，若给则校验一致。

    Returns:
        审计通过的 Operator 子类（未实例化）。

    Raises:
        OperatorLoadError: 审计失败 / 无算子类 / 多个算子类 / 契约不符。
    """

    audit_source(source)  # 先审计，违规则抛

    # 在隔离的模块命名空间编译执行
    module_name = f"_opdev_sandbox_{uuid.uuid4().hex[:12]}"
    mod_globals: dict[str, object] = {"__name__": module_name}
    try:
        exec(compile(source, f"<opdev:{module_name}>", "exec"), mod_globals)
    except Exception as exc:
        raise OperatorLoadError(f"源码执行失败: {type(exc).__name__}: {exc}") from exc

    # 收集 @operator 标记的类
    op_classes = [
        v
        for v in mod_globals.values()
        if isinstance(v, type)
        and issubclass(v, Operator)
        and getattr(v, "_is_operator", False)
        and v is not Operator
    ]
    if not op_classes:
        raise OperatorLoadError("源码未找到 @operator 装饰的算子类")
    if len(op_classes) > 1:
        raise OperatorLoadError(
            f"源码含多个 @operator 类（{[c.__name__ for c in op_classes]}），"
            "一个文件只允许一个算子"
        )

    cls = op_classes[0]
    try:
        validate_contract(cls)
    except OperatorContractError as exc:
        raise OperatorLoadError(f"契约校验失败: {exc}") from exc

    if expected_name and cls.name != expected_name:
        raise OperatorLoadError(
            f"算子名不符：spec 期望 '{expected_name}'，源码产出 '{cls.name}'"
        )

    # 清理隔离模块
    sys.modules.pop(module_name, None)
    return cls
