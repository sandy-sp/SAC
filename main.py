"""SAC — Social Ad Campaigns: CLI orchestration pipeline.

Entry point implementing the end-to-end flow from docs/SPEC.md:
brief ingestion (FR-1) → legal guardrail (GR-1) → asset resolution
(FR-2/FR-3) → multi-ratio composition with text and watermark
(FR-5/FR-6/GR-2) → organized output (FR-7), with a rich terminal
experience throughout (Section 5).
"""

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from src.guardrails import validate_campaign_message
from src.image_processor import SUPPORTED_RATIOS, ImageProcessor, WatermarkMissingError
from src.models import CampaignBrief
from src.providers import MockImageProvider

ASSETS_DIR = Path("assets")
OUTPUTS_DIR = Path("outputs")
ASSET_EXTENSIONS = (".jpg", ".jpeg", ".png")

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sac",
        description="SAC — generate localized social ad creatives from a campaign brief.",
    )
    parser.add_argument(
        "--brief",
        type=Path,
        default=Path("inputs/mock_brief.json"),
        help="Path to the campaign brief JSON file (default: inputs/mock_brief.json)",
    )
    return parser.parse_args()


def load_brief(path: Path) -> CampaignBrief:
    """Parse and validate the campaign brief (FR-1). Exits cleanly on failure."""
    if not path.is_file():
        console.print(f"[bold red]✗ Brief not found:[/bold red] {path}")
        sys.exit(1)
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        console.print(f"[bold red]✗ Brief is not valid JSON:[/bold red] {exc}")
        sys.exit(1)
    try:
        return CampaignBrief.model_validate(payload)
    except ValidationError as exc:
        console.print("[bold red]✗ Brief failed validation (FR-1):[/bold red]")
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            console.print(f"   [yellow]{location}[/yellow]: {error['msg']}")
        sys.exit(1)


def resolve_base_image(
    product_id: str, prompt: str, provider: MockImageProvider
) -> tuple[Image.Image, bool]:
    """Return the product's base image and whether a local asset was reused (FR-2/FR-3)."""
    for extension in ASSET_EXTENSIONS:
        candidate = ASSETS_DIR / f"{product_id}{extension}"
        if candidate.is_file():
            console.log(f"[green]↺ Reusing local asset[/green] {candidate}")
            return Image.open(candidate), True

    console.log(
        f"[cyan]✦ No local asset for[/cyan] [bold]{product_id}[/bold] "
        f"[cyan]— generating via provider[/cyan] [bold]{provider.provider_name}[/bold]"
    )
    image_bytes = provider.generate_image(prompt, 1080, 1080)
    return Image.open(io.BytesIO(image_bytes)), False


def main() -> None:
    args = parse_args()

    console.print(
        Panel.fit(
            "[bold]SAC — Social Ad Campaigns[/bold]\n"
            "Creative automation pipeline (PoC)",
            border_style="magenta",
        )
    )

    brief = load_brief(args.brief)
    console.print(
        f"[bold green]✔ Brief validated:[/bold green] [bold]{brief.campaign_id}[/bold] — "
        f"{len(brief.products)} products, regions: {', '.join(brief.target_regions)}, "
        f"audiences: {len(brief.target_audiences)}"
    )

    provider = MockImageProvider()
    processor = ImageProcessor()
    campaign_dir = OUTPUTS_DIR / brief.campaign_id

    creatives_produced = 0
    products_completed = 0
    products_skipped = 0
    assets_reused = 0
    assets_generated = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        pipeline_task = progress.add_task(
            "[magenta]Campaign pipeline", total=len(brief.products) * len(SUPPORTED_RATIOS)
        )

        for index, product in enumerate(brief.products):
            # Convention: message[i] pairs with product[i]; fall back to the
            # first message when the brief has fewer messages than products.
            message = brief.campaign_messages[min(index, len(brief.campaign_messages) - 1)]

            console.rule(f"[bold]{product.name}[/bold] ({product.product_id})")

            # GR-1: hard gate — flagged message skips this product entirely.
            if not validate_campaign_message(message):
                console.log(
                    f"[bold yellow]⏭ Skipping product[/bold yellow] "
                    f"[bold]{product.product_id}[/bold] due to legal guardrail."
                )
                products_skipped += 1
                progress.advance(pipeline_task, len(SUPPORTED_RATIOS))
                continue

            prompt = f"Product photo: {product.name}. {product.description}"
            base_image, reused = resolve_base_image(product.product_id, prompt, provider)
            assets_reused += int(reused)
            assets_generated += int(not reused)

            product_dir = campaign_dir / product.product_id
            for ratio in SUPPORTED_RATIOS:
                progress.update(
                    pipeline_task,
                    description=f"[magenta]{product.product_id} @ {ratio}",
                )
                creative = processor.crop_to_aspect_ratio(base_image, ratio)
                creative = processor.render_text(creative, message)
                creative = processor.apply_watermark(creative)  # GR-2 hard gate

                product_dir.mkdir(parents=True, exist_ok=True)
                output_path = product_dir / f"{ratio.replace(':', 'x')}_final.png"
                creative.convert("RGB").save(output_path)
                console.log(f"[green]✔ Saved[/green] {output_path}")
                creatives_produced += 1
                progress.advance(pipeline_task)

            products_completed += 1

    summary = Table(title="Execution Summary", border_style="magenta", show_lines=False)
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Total Products", str(len(brief.products)))
    summary.add_row("Successful Generations", f"[green]{products_completed}[/green]")
    summary.add_row("Skipped (Legal Guardrail)", f"[yellow]{products_skipped}[/yellow]")
    summary.add_row("Creatives Produced", str(creatives_produced))
    summary.add_row("Local Assets Reused", str(assets_reused))
    summary.add_row("GenAI Assets Generated", str(assets_generated))
    summary.add_row("Output Path", str(campaign_dir))
    console.print(summary)


if __name__ == "__main__":
    try:
        main()
    except WatermarkMissingError as exc:
        console.print(f"[bold red]✗ Brand compliance failure (GR-2):[/bold red] {exc}")
        sys.exit(1)
