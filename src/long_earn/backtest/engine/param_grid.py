"""参数网格 + DSL 模板渲染

支持标量插值（${var}）和对象层变换（DSL 字段深拷贝+赋值）。
"""

from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from typing import Any

from long_earn.backtest.engine.dsl import StrategyDSL
from long_earn.core.render import render


def render_template(yaml_template: str, scalar_params: dict[str, Any]) -> str:
    """渲染 YAML 模板，仅做标量 ${var} 插值。"""
    return render(yaml_template, scalar_params)


def apply_struct_params(dsl: StrategyDSL, struct_params: dict[str, Any]) -> StrategyDSL:
    """在解析后的 DSL 对象上做字段深拷贝+赋值。

    支持点分路径访问嵌套字段，如 "risk_control.stop_loss" → dsl.risk_control.stop_loss。
    """
    dsl = copy.deepcopy(dsl)
    if not struct_params:
        return dsl
    for key, value in struct_params.items():
        parts = key.split(".")
        obj: Any = dsl
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], value)
    return dsl


@dataclass
class ParamGrid:
    """参数网格

    接受 dict[str, list]（笛卡尔积）或 list[dict]（显式组合），展开为 list[dict]。

    标量参数（scalars）在 YAML 渲染阶段做 ${var} 插值；
    结构化参数（structs）在解析后的 DSL 对象上做字段赋值。
    """

    scalars: dict[str, list] = field(default_factory=dict)
    structs: dict[str, list] = field(default_factory=dict)

    def expand_scalars(self) -> list[dict[str, Any]]:
        """展开标量参数为笛卡尔积组合列表。"""
        if not self.scalars:
            return [{}]
        keys = list(self.scalars.keys())
        value_lists = [self.scalars[k] for k in keys]
        return [
            dict(zip(keys, combo, strict=False))
            for combo in itertools.product(*value_lists)
        ]

    def expand_structs(self) -> list[dict[str, Any]]:
        """展开结构化参数为笛卡尔积组合列表。"""
        if not self.structs:
            return [{}]
        keys = list(self.structs.keys())
        value_lists = [self.structs[k] for k in keys]
        return [
            dict(zip(keys, combo, strict=False))
            for combo in itertools.product(*value_lists)
        ]

    def expand_all(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """展开标量+结构化参数的全笛卡尔积。

        返回 [(scalar_params, struct_params), ...] 列表。
        """
        scalar_combos = self.expand_scalars()
        struct_combos = self.expand_structs()
        return [(s, st) for s in scalar_combos for st in struct_combos]

    @property
    def total_combinations(self) -> int:
        """总组合数。"""
        s = 1
        for v in self.scalars.values():
            s *= len(v)
        st = 1
        for v in self.structs.values():
            st *= len(v)
        return s * st
