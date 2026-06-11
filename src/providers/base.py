"""Strategy interface for pluggable GenAI image generation backends.

Implements FR-4.1 from docs/SPEC.md. Concrete strategies (AWS Bedrock,
GCP Vertex AI, local mock) implement this interface; the pipeline depends
only on this abstraction (Open/Closed Principle, NFR-2).
"""

from abc import ABC, abstractmethod


class ProviderGenerationError(RuntimeError):
    """Raised when any GenAI backend fails (credentials, API errors, …).

    Provider-agnostic: UI layers catch this one type regardless of which
    strategy is active. Messages MUST never contain credential values.
    """


class ImageGenerationProvider(ABC):
    """Strategy interface for pluggable GenAI image generation backends.

    Bring Your Own Key (BYOK): every provider accepts an optional
    ``credentials`` dict whose keys are provider-specific (e.g. AWS
    access keys, a GCP service-account JSON, an OpenAI API key). When
    omitted or empty, providers fall back to their environment-default
    credential chain. Implementations MUST never print or log
    credential values.
    """

    def __init__(self, credentials: dict | None = None):
        self.credentials = credentials or {}

    @abstractmethod
    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns raw image bytes."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier (for logging/reporting)."""
        ...
