"""Local mock GenAI provider for offline development and testing (NFR-3).

Generates a solid-color placeholder image with Pillow instead of calling
a cloud API, so the full pipeline loop can be exercised rapidly with no
credentials or network access.
"""

import io

from PIL import Image, ImageDraw

from src.providers.base import ImageGenerationProvider

_PLACEHOLDER_GREY = (128, 128, 128)
_LABEL = "MOCK GENAI ASSET"


class MockImageProvider(ImageGenerationProvider):
    """Offline stand-in for a real GenAI image backend."""

    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        image = Image.new("RGB", (width, height), color=_PLACEHOLDER_GREY)
        draw = ImageDraw.Draw(image)

        # Center the label; default bitmap font keeps this dependency-free.
        bbox = draw.textbbox((0, 0), _LABEL)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        position = ((width - text_width) // 2, (height - text_height) // 2)
        draw.text(position, _LABEL, fill="white")

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    @property
    def provider_name(self) -> str:
        return "mock"
