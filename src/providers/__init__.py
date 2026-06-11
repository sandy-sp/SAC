"""Pluggable GenAI image generation providers (strategy pattern, FR-4)."""

from src.providers.base import ImageGenerationProvider
from src.providers.mock import MockImageProvider

__all__ = ["ImageGenerationProvider", "MockImageProvider"]
