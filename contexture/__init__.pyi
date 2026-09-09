"""Stable authoring API for Contexture applications.

Use this namespace for business declarations.  ``contexture.server`` is the
advanced hosting namespace; ``contexture.core`` is implementation detail.
"""

from .application import Contexture as Contexture
from .core.emphasis import binding_instruction as binding_instruction
from .core.errors import ContextureError as ContextureError
from .core.errors import DeclarationError as DeclarationError
from .core.errors import DuplicateNameError as DuplicateNameError
from .core.errors import ModelValidationError as ModelValidationError
from .core.errors import NodeNotFoundError as NodeNotFoundError
from .core.mcp_interface import Prompt as Prompt
from .core.mcp_interface import Resource as Resource
from .core.model.channels import Channels as Channels
from .core.model.graph_context import current_graph as current_graph
from .core.model.role import PostProcess as PostProcess
from .core.model.role import PreProcess as PreProcess
from .core.model.role import Role as Role
from .core.model.skill import Skill as Skill
from .core.model.telemetry import current_telemetry as current_telemetry
from .core.model.tool import Tool as Tool
from .core.principal import Principal as Principal
from .core.principal import current_principal as current_principal

__version__: str

__all__ = [
    "Channels",
    "Contexture",
    "ContextureError",
    "DeclarationError",
    "DuplicateNameError",
    "ModelValidationError",
    "NodeNotFoundError",
    "Principal",
    "Prompt",
    "PostProcess",
    "PreProcess",
    "Resource",
    "Role",
    "Skill",
    "Tool",
    "__version__",
    "binding_instruction",
    "current_graph",
    "current_principal",
    "current_telemetry",
]
