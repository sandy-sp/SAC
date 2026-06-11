"""Campaign brief merging and persistence helpers.

Phase 5 enterprise behavior: a brief whose campaign_id already exists in
inputs/ (as inputs/{campaign_id}.json) is merged with the stored brief
before the pipeline runs, and the merged result is written back so the
stored brief is always the cumulative source of truth for that campaign.
"""

import json
from pathlib import Path

from src.models import CampaignBrief

INPUTS_DIR = Path("inputs")


def _dedupe_preserving_order(*sequences: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for sequence in sequences:
        for item in sequence:
            if item not in seen:
                seen.add(item)
                result.append(item)
    return result


def _messages_per_product(brief: CampaignBrief) -> list[str]:
    """Expand campaign_messages to one message per product.

    Uses the pipeline's pairing convention: message[i] belongs to
    product[i], falling back to the first message when the brief has
    fewer messages than products.
    """
    last = len(brief.campaign_messages) - 1
    return [brief.campaign_messages[min(i, last)] for i in range(len(brief.products))]


def merge_briefs(existing: CampaignBrief, incoming: CampaignBrief) -> CampaignBrief:
    """Merge an incoming brief into an existing one for the same campaign.

    Rules:
    - target_regions / target_audiences: union, deduplicated, order
      preserved (existing entries first).
    - products: upsert keyed on product_id — an existing product_id gets
      its details (and paired campaign message) replaced; a new
      product_id is appended.
    - campaign_messages: kept aligned one-per-product so the
      message[i] ↔ product[i] pairing survives the merge.
    """
    if existing.campaign_id != incoming.campaign_id:
        raise ValueError(
            f"Cannot merge briefs with different campaign_ids: "
            f"{existing.campaign_id!r} vs {incoming.campaign_id!r}"
        )

    merged_products = list(existing.products)
    merged_messages = _messages_per_product(existing)
    index_by_id = {product.product_id: i for i, product in enumerate(merged_products)}

    incoming_messages = _messages_per_product(incoming)
    for product, message in zip(incoming.products, incoming_messages):
        if product.product_id in index_by_id:
            position = index_by_id[product.product_id]
            merged_products[position] = product
            merged_messages[position] = message
        else:
            index_by_id[product.product_id] = len(merged_products)
            merged_products.append(product)
            merged_messages.append(message)

    return CampaignBrief(
        campaign_id=existing.campaign_id,
        target_regions=_dedupe_preserving_order(
            existing.target_regions, incoming.target_regions
        ),
        target_audiences=_dedupe_preserving_order(
            existing.target_audiences, incoming.target_audiences
        ),
        campaign_messages=merged_messages,
        products=merged_products,
    )


def merge_and_persist_brief(
    brief: CampaignBrief, inputs_dir: Path = INPUTS_DIR
) -> tuple[CampaignBrief, bool]:
    """Merge a brief with the stored one (if any) and persist the result.

    Looks for inputs/{campaign_id}.json. When present, merges per
    merge_briefs(); when absent, the incoming brief is taken as-is.
    Either way the effective brief is written back to that path.

    Returns (effective_brief, was_merged).
    """
    inputs_dir.mkdir(parents=True, exist_ok=True)
    stored_path = inputs_dir / f"{brief.campaign_id}.json"

    was_merged = False
    effective = brief
    if stored_path.is_file():
        existing = CampaignBrief.model_validate(json.loads(stored_path.read_text()))
        effective = merge_briefs(existing, brief)
        was_merged = True

    stored_path.write_text(effective.model_dump_json(indent=2) + "\n")
    return effective, was_merged
