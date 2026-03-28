"""Base tool interface — every tool inherits from this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import TsunamiConfig


@dataclass
class ToolResult:
    content: str
    is_error: bool = False

    def __str__(self) -> str:
        return self.content


class BaseTool(ABC):
    """Abstract base for all Tsunami tools.

    Tools are limbs, not features. They are how the agent
    interacts with reality.
    """

    name: str = ""
    description: str = ""

    def __init__(self, config: TsunamiConfig):
        self.config = config

    @abstractmethod
    def parameters_schema(self) -> dict:
        """Return JSON Schema for this tool's parameters."""
        ...

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool and return a result."""
        ...
