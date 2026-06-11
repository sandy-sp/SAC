"""Strategy interface for pluggable GenAI image generation backends.

Implements FR-4.1 from docs/SPEC.md. Concrete strategies (AWS Bedrock,
GCP Vertex AI, local mock) implement this interface; the pipeline depends
only on this abstraction (Open/Closed Principle, NFR-2).
"""

from abc import ABC, abstractmethod


class ImageGenerationProvider(ABC):
    """Strategy interface for pluggable GenAI image generation backends."""

    @abstractmethod
    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns raw image bytes."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier (for logging/reporting)."""
        ...
