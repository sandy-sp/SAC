"""Google AI Studio image generation strategy (FR-4 strategy pattern).

Uses the google-generativeai SDK with a plain API key (the simplest of
the supported auth flows — no OAuth, no service account). Image bytes
come back as inline_data parts on a Gemini image-generation model.
Credential values are never printed or logged.
"""

import os
import warnings

# google-generativeai emits a FutureWarning about its deprecation on
# import; silence it so every CLI/app run isn't polluted. Migration to
# the google-genai successor SDK is tracked in the README roadmap.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import google.generativeai as genai

from rich.console import Console

from src.providers.base import ImageGenerationProvider, ProviderGenerationError

console = Console()

DEFAULT_MODEL_NAME = "gemini-2.0-flash-preview-image-generation"


class GoogleStudioProvider(ImageGenerationProvider):
    """Live GenAI backend via Google AI Studio (Gemini API key)."""

    def __init__(self, model_name: str | None = None, credentials: dict | None = None):
        super().__init__(credentials)
        api_key = self.credentials.get("api_key") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ProviderGenerationError(
                "Google AI Studio API key missing: provide api_key via BYOK "
                "or the GOOGLE_API_KEY environment variable."
            )
        genai.configure(api_key=api_key)
        self.model_name = model_name or os.environ.get(
            "SAC_GOOGLE_IMAGE_MODEL", DEFAULT_MODEL_NAME
        )
        self._model = genai.GenerativeModel(self.model_name)

    @property
    def provider_name(self) -> str:
        return f"google-ai-studio ({self.model_name})"

    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        # Gemini image models take no explicit dimensions; hint the aspect
        # in the prompt — downstream center-crop normalizes exact ratios.
        sized_prompt = f"{prompt} Compose the image at a {width}:{height} aspect ratio."
        try:
            response = self._model.generate_content(
                sized_prompt,
                generation_config={"response_modalities": ["TEXT", "IMAGE"]},
            )
        except Exception as exc:  # SDK raises a mix of google.* error types
            console.print(
                f"[bold red]✗ Google AI Studio generation failed:[/bold red] {exc}"
            )
            raise ProviderGenerationError(
                f"Google AI Studio generation failed: {exc}"
            ) from exc

        for candidate in getattr(response, "candidates", None) or []:
            for part in getattr(candidate.content, "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                if inline is not None and getattr(inline, "data", None):
                    return inline.data

        raise ProviderGenerationError(
            f"Google AI Studio response from {self.model_name} contained no image data."
        )
