"""大师智能节点包 — ADR-012 Phase 1

导入本包即触发 4 个内置大师（巴菲特/查理芒格/费雪/彼得林奇）的注册。
"""

from long_earn.skills.personas.buffett import BuffettPersona
from long_earn.skills.personas.charles_munger import CharlesMungerPersona
from long_earn.skills.personas.fiske import FiskePersona
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
    "MasterPersona",
    "PersonaContext",
    "PersonaRegistry",
    "PersonaResult",
    "PetterPersona",
]
