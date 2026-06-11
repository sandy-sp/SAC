"""Pydantic data models for SAC campaign briefs.

Implements FR-1 (Campaign Brief Ingestion) from docs/SPEC.md.
Briefs are validated on load and fail fast with clear errors (NFR-4).
"""

from pydantic import BaseModel, Field, field_validator


class Product(BaseModel):
    """A single product entry within a campaign brief."""

    product_id: str = Field(..., min_length=1, description="Unique product identifier (also used for local asset matching, see FR-2)")
    name: str = Field(..., min_length=1, description="Display name of the product")
    description: str = Field(..., min_length=1, description="Product description (used to compose GenAI prompts, see FR-3)")


class CampaignBrief(BaseModel):
    """A validated campaign brief (JSON/YAML input).

    Per FR-1, a brief must declare at least two products, one or more
    target regions/markets, target audiences, and campaign messages.
    """

    campaign_id: str = Field(..., min_length=1, description="Unique campaign identifier (used as top-level output folder, see FR-7)")
    target_regions: list[str] = Field(..., min_length=1, description="Target regions/markets (e.g. 'US', 'DE', 'JP')")
    target_audiences: list[str] = Field(..., min_length=1, description="Target audience descriptors")
    campaign_messages: list[str] = Field(..., min_length=1, description="Campaign messages to render on final creatives (FR-6)")
    products: list[Product] = Field(..., description="Products covered by this campaign")

    @field_validator("products")
    @classmethod
    def enforce_minimum_two_products(cls, products: list[Product]) -> list[Product]:
        """FR-1: a brief must contain at least two (2) distinct products."""
        if len(products) < 2:
            raise ValueError(
                f"Campaign brief must contain at least two (2) products (FR-1); got {len(products)}."
            )
        return products
