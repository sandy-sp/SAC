"""Core image processing for SAC creatives.

Implements FR-5 (multi-aspect-ratio cropping), FR-6 (dynamic text
rendering), and GR-2 (mandatory brand watermark overlay) from
docs/SPEC.md, using Pillow only.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

# FR-5: base width of 1080px; height derived from the ratio so the image
# is never stretched (center-crop via ImageOps.fit).
BASE_WIDTH = 1080

SUPPORTED_RATIOS: dict[str, tuple[int, int]] = {
    "1:1": (BASE_WIDTH, BASE_WIDTH),
    "9:16": (BASE_WIDTH, round(BASE_WIDTH * 16 / 9)),   # 1080 x 1920
    "16:9": (BASE_WIDTH, round(BASE_WIDTH * 9 / 16)),   # 1080 x 608
}

DEFAULT_WATERMARK_PATH = Path("assets/watermark.png")

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
]


class WatermarkMissingError(FileNotFoundError):
    """GR-2 hard gate: no creative may be produced without the brand watermark."""


def _load_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


class ImageProcessor:
    """Pillow-based processing: aspect-ratio crops, watermarking, text rendering."""

    def __init__(self, watermark_path: Path = DEFAULT_WATERMARK_PATH):
        self.watermark_path = Path(watermark_path)

    def crop_to_aspect_ratio(self, image: Image.Image, ratio: str) -> Image.Image:
        """Resize + center-crop to the target ratio without distortion (FR-5)."""
        if ratio not in SUPPORTED_RATIOS:
            raise ValueError(
                f"Unsupported aspect ratio {ratio!r}; expected one of {sorted(SUPPORTED_RATIOS)}"
            )
        target = SUPPORTED_RATIOS[ratio]
        # ImageOps.fit scales to cover the target box then center-crops the
        # overflow, so the source is never stretched.
        return ImageOps.fit(image, target, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    def apply_watermark(self, image: Image.Image) -> Image.Image:
        """Composite the brand watermark onto the bottom-right corner (GR-2).

        Raises WatermarkMissingError if the watermark asset is absent —
        per the SPEC this is a hard gate, never a silent skip.
        """
        if not self.watermark_path.is_file():
            raise WatermarkMissingError(
                f"Brand watermark not found at {self.watermark_path} — "
                f"GR-2 forbids producing creatives without the brand mark."
            )

        base = image.convert("RGBA")
        watermark = Image.open(self.watermark_path).convert("RGBA")

        # Scale watermark to ~22% of creative width, keep aspect.
        target_w = max(1, int(base.width * 0.22))
        scale = target_w / watermark.width
        watermark = watermark.resize(
            (target_w, max(1, int(watermark.height * scale))),
            Image.Resampling.LANCZOS,
        )

        margin = int(base.width * 0.03)
        position = (
            base.width - watermark.width - margin,
            base.height - watermark.height - margin,
        )

        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        layer.paste(watermark, position, mask=watermark)
        return Image.alpha_composite(base, layer)

    def render_text(self, image: Image.Image, text: str) -> Image.Image:
        """Draw the campaign message with wrapping and a contrast band (FR-6).

        Text is wrapped to the image width by pixel measurement and drawn
        over a semi-transparent dark rectangle so it stays legible on any
        background, in any aspect ratio.
        """
        base = image.convert("RGBA")
        font_size = max(18, base.width // 22)
        font = _load_font(font_size)

        side_padding = int(base.width * 0.06)
        max_text_width = base.width - 2 * side_padding

        draw = ImageDraw.Draw(base)
        lines = self._wrap_text(draw, text, font, max_text_width)

        line_heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_heights.append(bbox[3] - bbox[1])
        line_spacing = int(font_size * 0.35)
        block_height = sum(line_heights) + line_spacing * (len(lines) - 1)

        # Band sits in the upper area, clear of the bottom-right watermark.
        band_padding = int(font_size * 0.6)
        band_top = int(base.height * 0.06)
        band_bottom = band_top + block_height + 2 * band_padding

        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle(
            [0, band_top, base.width, band_bottom],
            fill=(0, 0, 0, 150),
        )

        y = band_top + band_padding
        for line, height in zip(lines, line_heights):
            line_width = overlay_draw.textlength(line, font=font)
            x = (base.width - line_width) // 2
            overlay_draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += height + line_spacing

        return Image.alpha_composite(base, overlay)

    @staticmethod
    def _wrap_text(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        max_width: int,
    ) -> list[str]:
        """Greedy word-wrap by measured pixel width (not character count)."""
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines
