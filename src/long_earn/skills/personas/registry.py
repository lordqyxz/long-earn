"""大师注册表 — ADR-012 Phase 1

全局单例式注册表，所有大师类通过 ``@PersonaRegistry.register`` 装饰器
在导入时自动注册。``skills.personas.__init__`` 导入 4 个内置大师模块
即触发注册。
"""

from __future__ import annotations

from typing import Any, ClassVar


class PersonaRegistry:
    """大师 Persona 注册表。

    使用类变量 ``_personas`` 维护 name -> persona_class 映射，
    提供 register 装饰器、get / all / create_all / clear 方法。
    """

    _personas: ClassVar[dict[str, type]] = {}

    @classmethod
    def register(cls, persona_class: type) -> type:
        """装饰器：注册大师类，返回原类。

        Args:
            persona_class: 必须具有 ``name`` 类属性

        Returns:
            原始 persona_class（保持装饰器透明性）
        """
        cls._personas[persona_class.name] = persona_class
        return persona_class

    @classmethod
    def get(cls, name: str) -> type:
        """按 name 获取已注册的大师类。

        Raises:
            KeyError: 未注册
        """
        return cls._personas[name]

    @classmethod
    def all(cls) -> dict[str, type]:
        """返回所有已注册大师的 name -> class 映射（浅拷贝）。"""
        return dict(cls._personas)

    @classmethod
    def create_all(cls, llm: Any) -> dict[str, Any]:
        """创建所有已注册大师的实例。

        Args:
            llm: 底层 ChatModel 实例（与 RuntimeContext.require_llm().get_llm() 一致）

        Returns:
            name -> persona_instance 映射
        """
        return {name: persona_cls(llm) for name, persona_cls in cls._personas.items()}

    @classmethod
    def clear(cls) -> None:
        """清空注册表（测试用）。"""
        cls._personas.clear()
