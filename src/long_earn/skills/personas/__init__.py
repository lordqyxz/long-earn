"""大师智能节点包 — ADR-012

导入本包即触发所有内置大师的注册。
内置 4 大师（巴菲特/查理芒格/费雪/彼得林奇）+ 扩展示例（利弗莫尔）。
"""

from long_earn.skills.personas.buffett import BuffettPersona
from long_earn.skills.personas.charles_munger import CharlesMungerPersona
from long_earn.skills.personas.fiske import FiskePersona
from long_earn.skills.personas.livermore import LivermorePersona
from long_earn.skills.personas.petter import PetterPersona
from long_earn.skills.personas.protocol import (
    MasterPersona,
    PersonaContext,
    PersonaResult,
)
from long_earn.skills.personas.registry import PersonaRegistry

__all__ = [
    "BuffettPersona",
    "CharlesMungerPersona",
    "FiskePersona",
    "LivermorePersona",
    "MasterPersona",
    "PersonaContext",
    "PersonaRegistry",
    "PersonaResult",
    "PetterPersona",
]
