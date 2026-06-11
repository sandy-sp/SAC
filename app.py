"""SAC — Social Ad Campaigns: Streamlit web front-end.

Thin UI wrapper over the locked core pipeline in src/ (models,
guardrails, image processing, providers). All business logic stays in
src/; this module only handles brief upload, orchestration, and visual
presentation of the generated creatives.
"""

import io
import json
import re
from pathlib import Path

import streamlit as st
import yaml
from PIL import Image
from pydantic import ValidationError

from src.guardrails import PROHIBITED_WORDS, validate_campaign_message
from src.image_processor import SUPPORTED_RATIOS, ImageProcessor, WatermarkMissingError
from src.models import CampaignBrief
from src.providers import MockImageProvider

ASSETS_DIR = Path("assets")
OUTPUTS_DIR = Path("outputs")
ASSET_EXTENSIONS = (".jpg", ".jpeg", ".png")
DEFAULT_BRIEF_PATH = Path("inputs/mock_brief.json")

st.set_page_config(
    page_title="SAC: Creative Automation",
    page_icon="🎨",
    layout="wide",
)


def parse_brief_payload(raw: bytes, filename: str) -> dict:
    """Decode an uploaded brief file (JSON or YAML) into a dict."""
    text = raw.decode("utf-8")
    if filename.lower().endswith((".yaml", ".yml")):
        return yaml.safe_load(text)
    return json.loads(text)


def find_prohibited_words(message: str) -> list[str]:
    """Mirror GR-1 matching (case-insensitive, whole word) for UI display.

    The gate itself is src.guardrails.validate_campaign_message; this only
    recovers *which* words tripped it, since the locked API returns a bool.
    """
    return [
        word
        for word in PROHIBITED_WORDS
        if re.search(rf"\b{re.escape(word)}\b", message, flags=re.IGNORECASE)
    ]


def resolve_base_image(
    product_id: str, prompt: str, provider: MockImageProvider
) -> tuple[Image.Image, bool]:
    """Reuse a local asset when present, otherwise generate (FR-2/FR-3)."""
    for extension in ASSET_EXTENSIONS:
        candidate = ASSETS_DIR / f"{product_id}{extension}"
        if candidate.is_file():
            return Image.open(candidate), True
    image_bytes = provider.generate_image(prompt, 1080, 1080)
    return Image.open(io.BytesIO(image_bytes)), False


def render_sidebar() -> dict | None:
    """Brief selection UI. Returns the raw brief payload or None."""
    st.sidebar.title("🎨 SAC")
    st.sidebar.caption("Creative Automation for Social Ad Campaigns")
    st.sidebar.divider()

    st.sidebar.subheader("1 · Campaign Brief")
    uploaded = st.sidebar.file_uploader(
        "Upload a brief (JSON or YAML)",
        type=["json", "yaml", "yml"],
        help="Must satisfy the CampaignBrief schema — at least two products.",
    )

    if uploaded is not None:
        try:
            payload = parse_brief_payload(uploaded.getvalue(), uploaded.name)
            st.session_state["brief_payload"] = payload
            st.session_state["brief_source"] = uploaded.name
        except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            st.sidebar.error(f"Could not parse {uploaded.name}: {exc}")
    else:
        if st.sidebar.button("📋 Load Default Mock Brief", width="stretch"):
            st.session_state["brief_payload"] = json.loads(DEFAULT_BRIEF_PATH.read_text())
            st.session_state["brief_source"] = str(DEFAULT_BRIEF_PATH)

    if "brief_source" in st.session_state:
        st.sidebar.success(f"Brief loaded: `{st.session_state['brief_source']}`")

    return st.session_state.get("brief_payload")


def render_brief_summary(brief: CampaignBrief) -> None:
    with st.expander("📄 Brief overview", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Campaign", brief.campaign_id)
        col2.metric("Products", len(brief.products))
        col3.metric("Regions", ", ".join(brief.target_regions))
        col4.metric("Audiences", len(brief.target_audiences))


def run_pipeline(brief: CampaignBrief) -> None:
    """Execute the locked core pipeline product-by-product with live UI feedback."""
    provider = MockImageProvider()
    processor = ImageProcessor()
    campaign_dir = OUTPUTS_DIR / brief.campaign_id

    progress = st.progress(0.0, text="Starting pipeline…")
    total_steps = len(brief.products) * len(SUPPORTED_RATIOS)
    completed_steps = 0

    products_completed = 0
    products_skipped = 0
    creatives_produced = 0
    assets_reused = 0
    assets_generated = 0

    for index, product in enumerate(brief.products):
        # Same convention as the CLI: message[i] pairs with product[i],
        # falling back to the first message.
        message = brief.campaign_messages[min(index, len(brief.campaign_messages) - 1)]

        st.header(f"🧴 {product.name}", divider="rainbow")
        st.caption(f"`{product.product_id}` — {product.description}")

        # GR-1 hard gate: flagged message skips the whole product.
        if not validate_campaign_message(message):
            violations = find_prohibited_words(message)
            st.error(
                f"⛔ **Legal guardrail violation (GR-1)** — product skipped.\n\n"
                f"Campaign message: *“{message}”*\n\n"
                f"Prohibited word(s) detected: **{', '.join(violations)}**",
                icon="🚨",
            )
            products_skipped += 1
            completed_steps += len(SUPPORTED_RATIOS)
            progress.progress(
                completed_steps / total_steps,
                text=f"Skipped {product.product_id} (legal guardrail)",
            )
            continue

        prompt = f"Product photo: {product.name}. {product.description}"
        with st.spinner(f"Resolving base asset for {product.product_id}…"):
            base_image, reused = resolve_base_image(product.product_id, prompt, provider)
        assets_reused += int(reused)
        assets_generated += int(not reused)
        if reused:
            st.info(f"↺ Reused local asset for `{product.product_id}`", icon="📁")
        else:
            st.info(
                f"✦ No local asset — generated via provider `{provider.provider_name}`",
                icon="🤖",
            )

        output_paths: dict[str, Path] = {}
        product_dir = campaign_dir / product.product_id
        for ratio in SUPPORTED_RATIOS:
            with st.spinner(f"Composing {product.product_id} @ {ratio}…"):
                creative = processor.crop_to_aspect_ratio(base_image, ratio)
                creative = processor.render_text(creative, message)
                creative = processor.apply_watermark(creative)  # GR-2 hard gate

                product_dir.mkdir(parents=True, exist_ok=True)
                output_path = product_dir / f"{ratio.replace(':', 'x')}_final.png"
                creative.convert("RGB").save(output_path)
                output_paths[ratio] = output_path

            creatives_produced += 1
            completed_steps += 1
            progress.progress(
                completed_steps / total_steps,
                text=f"{product.product_id} @ {ratio} done",
            )

        columns = st.columns(len(output_paths))
        for column, (ratio, path) in zip(columns, output_paths.items()):
            with column:
                st.image(str(path), caption=f"{ratio} — {path.name}", width="stretch")

        products_completed += 1

    progress.progress(1.0, text="Pipeline complete ✅")

    st.header("📊 Execution Summary", divider="gray")
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Products", len(brief.products))
    col2.metric("Successful", products_completed)
    col3.metric("Skipped (Guardrail)", products_skipped)
    col4.metric("Creatives Produced", creatives_produced)
    col5.metric("Assets Reused / Generated", f"{assets_reused} / {assets_generated}")
    st.caption(f"Output path: `{campaign_dir}`")


def main() -> None:
    st.title("SAC: Creative Automation")
    st.caption(
        "Localized social ad creatives from a single campaign brief — "
        "guardrailed, branded, multi-ratio."
    )

    payload = render_sidebar()

    if payload is None:
        st.info(
            "⬅️ Upload a campaign brief (JSON/YAML) in the sidebar, "
            "or load the default mock brief to get started.",
            icon="👋",
        )
        return

    try:
        brief = CampaignBrief.model_validate(payload)
    except ValidationError as exc:
        st.error("**Brief failed validation (FR-1):**", icon="🚫")
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            st.markdown(f"- `{location}`: {error['msg']}")
        return

    render_brief_summary(brief)

    st.sidebar.divider()
    st.sidebar.subheader("2 · Execution")
    run_clicked = st.sidebar.button("🚀 Run Pipeline", type="primary", width="stretch")
    if not run_clicked:
        st.success("Brief validated. Hit **Run Pipeline** in the sidebar when ready.", icon="✅")
        return

    try:
        run_pipeline(brief)
    except WatermarkMissingError as exc:
        st.error(f"**Brand compliance failure (GR-2):** {exc}", icon="🛑")


if __name__ == "__main__":
    main()
