"""Dynamic GenAI prompt construction (FR-3 from docs/SPEC.md).

Composes a commercial-photography prompt from the campaign brief so
generated base images are product-accurate and tuned to the campaign's
audience and market.
"""

from src.models import CampaignBrief, Product

PROMPT_TEMPLATE = (
    "High quality commercial product photography of {product_name}. "
    "{product_description} "
    "Target audience: {audience} in {region}. "
    "Clean background, studio lighting, highly detailed, 4k."
)


def build_image_prompt(campaign: CampaignBrief, product: Product) -> str:
    """Build the image-generation prompt for one product in a campaign."""
    description = product.description.strip()
    if not description.endswith((".", "!", "?")):
        description += "."

    return PROMPT_TEMPLATE.format(
        product_name=product.name,
        product_description=description,
        audience=campaign.target_audiences[0],
        region=campaign.target_regions[0],
    )
