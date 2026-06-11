"""SAC — Social Ad Campaigns: Streamlit web front-end.

Thin UI wrapper over the locked core pipeline in src/ (models,
guardrails, image processing, providers). All business logic stays in
src/; this module handles brief import / manual building, campaign
merging, and visual presentation of the generated creatives.

Phase 5 enterprise behavior:
- Assets are sandboxed per campaign: assets/{campaign_id}/{product_id}.ext
- A brief whose campaign_id already exists in inputs/ is merged with the
  stored brief (regions/audiences union, product upsert) before running.
"""

import io
import json
import re
from pathlib import Path

import streamlit as st
import yaml
from dotenv import load_dotenv
from PIL import Image
from pydantic import ValidationError

from src.guardrails import PROHIBITED_WORDS, validate_campaign_message
from src.image_processor import SUPPORTED_RATIOS, ImageProcessor, WatermarkMissingError
from src.models import CampaignBrief
from src.prompt_builder import build_image_prompt
from src.providers import (
    AwsBedrockProvider,
    FireflyProvider,
    GoogleStudioProvider,
    ImageGenerationProvider,
    MockImageProvider,
    ProviderGenerationError,
)
from src.utils import merge_and_persist_brief, validate_safe_id

load_dotenv()

ASSETS_DIR = Path("assets")
OUTPUTS_DIR = Path("outputs")
ASSET_EXTENSIONS = (".jpg", ".jpeg", ".png")
DEFAULT_BRIEF_PATH = Path("inputs/mock_brief.json")

st.set_page_config(
    page_title="SAC: Creative Automation",
    page_icon="🎨",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def campaign_assets_dir(campaign_id: str) -> Path:
    """Per-campaign asset sandbox, created on demand."""
    directory = ASSETS_DIR / campaign_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_base_image(
    campaign_id: str, product_id: str, prompt: str, provider: ImageGenerationProvider
) -> tuple[Image.Image, bool]:
    """Reuse a sandboxed local asset when present, otherwise generate (FR-2/FR-3)."""
    directory = campaign_assets_dir(campaign_id)
    for extension in ASSET_EXTENSIONS:
        candidate = directory / f"{product_id}{extension}"
        if candidate.is_file():
            return Image.open(candidate), True
    image_bytes = provider.generate_image(prompt, 1080, 1080)
    return Image.open(io.BytesIO(image_bytes)), False


def save_uploaded_asset(campaign_id: str, product_id: str, uploaded_file) -> Path:
    """Persist a manually uploaded base image into the campaign sandbox."""
    validate_safe_id(campaign_id, "campaign_id")
    validate_safe_id(product_id, "product_id")
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ASSET_EXTENSIONS:
        extension = ".png"
    destination = campaign_assets_dir(campaign_id) / f"{product_id}{extension}"
    destination.write_bytes(uploaded_file.getvalue())
    return destination


def split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Sidebar: session controls + brief sources
# ---------------------------------------------------------------------------

def render_import_mode() -> bool:
    """Import-brief sidebar section. Returns True when a run was requested."""
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

    run_requested = False
    if "brief_payload" in st.session_state:
        st.sidebar.divider()
        st.sidebar.subheader("2 · Execution")
        run_requested = st.sidebar.button(
            "🚀 Run Pipeline", type="primary", width="stretch"
        )
    return run_requested


def render_manual_builder() -> bool:
    """Manual brief builder form. Returns True when Save & Run was submitted.

    On submit: saves any uploaded base images into the campaign asset
    sandbox and stores the built payload in session state.
    """
    with st.sidebar.form("manual_builder", clear_on_submit=False):
        st.subheader("Campaign")
        campaign_id = st.text_input("Campaign ID", placeholder="spring-launch-2026")
        regions_raw = st.text_input("Regions (comma-separated)", placeholder="US, DE, JP")
        audiences_raw = st.text_input(
            "Audiences (comma-separated)", placeholder="Parents, Students"
        )

        product_inputs = []
        for slot in (1, 2):
            st.divider()
            st.subheader(f"Product {slot}")
            product_inputs.append(
                {
                    "product_id": st.text_input(f"Product {slot} · ID", key=f"p{slot}_id"),
                    "name": st.text_input(f"Product {slot} · Name", key=f"p{slot}_name"),
                    "description": st.text_area(
                        f"Product {slot} · Description", key=f"p{slot}_desc"
                    ),
                    "message": st.text_input(
                        f"Product {slot} · Campaign Message", key=f"p{slot}_msg"
                    ),
                    "image": st.file_uploader(
                        f"Product {slot} · Base image (optional)",
                        type=["jpg", "jpeg", "png"],
                        key=f"p{slot}_img",
                    ),
                }
            )

        submitted = st.form_submit_button("💾 Save & Run", type="primary", width="stretch")

    if not submitted:
        return False

    payload = {
        "campaign_id": campaign_id.strip(),
        "target_regions": split_csv(regions_raw),
        "target_audiences": split_csv(audiences_raw),
        "campaign_messages": [entry["message"].strip() for entry in product_inputs],
        "products": [
            {
                "product_id": entry["product_id"].strip(),
                "name": entry["name"].strip(),
                "description": entry["description"].strip(),
            }
            for entry in product_inputs
        ],
    }
    st.session_state["brief_payload"] = payload
    st.session_state["brief_source"] = "manual builder"

    # Save uploaded base images into the campaign sandbox so asset
    # resolution finds and reuses them.
    if payload["campaign_id"]:
        for entry in product_inputs:
            if entry["image"] is not None and entry["product_id"].strip():
                try:
                    saved = save_uploaded_asset(
                        payload["campaign_id"], entry["product_id"].strip(), entry["image"]
                    )
                except ValueError as exc:
                    st.sidebar.error(f"Asset not saved: {exc}")
                    continue
                st.sidebar.info(f"Saved asset: `{saved}`", icon="🖼️")

    return True


def render_sidebar() -> bool:
    """Full sidebar. Returns True when a pipeline run was requested."""
    st.sidebar.title("🎨 SAC")
    st.sidebar.caption("Creative Automation for Social Ad Campaigns")

    if st.sidebar.button("🔄 Start Over / Clear Session", width="stretch"):
        st.session_state.clear()
        st.rerun()

    st.sidebar.divider()
    st.sidebar.subheader("⚙️ AI Generation Mode")
    st.sidebar.selectbox(
        "AI Generation Mode",
        [
            "Mock (offline placeholder)",
            "AWS Bedrock (live GenAI)",
            "Adobe Firefly (live GenAI)",
            "Google AI Studio (live GenAI)",
        ],
        key="provider_mode",
        help="Mock needs no credentials; live providers use your BYOK keys "
        "below, falling back to environment defaults (see .env.example).",
    )
    render_byok_inputs()

    st.sidebar.divider()
    st.sidebar.subheader("1 · Campaign Brief")
    mode = st.sidebar.radio(
        "Brief source",
        ["Import Brief", "Manual Builder"],
        horizontal=True,
    )

    if mode == "Import Brief":
        return render_import_mode()
    return render_manual_builder()


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

def render_brief_summary(brief: CampaignBrief) -> None:
    with st.expander("📄 Brief overview", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Campaign", brief.campaign_id)
        col2.metric("Products", len(brief.products))
        col3.metric("Regions", ", ".join(brief.target_regions))
        col4.metric("Audiences", len(brief.target_audiences))


def selected_provider_kind() -> str:
    """Short provider key from the sidebar toggle: mock | aws | firefly | google."""
    mode = str(st.session_state.get("provider_mode", ""))
    if mode.startswith("AWS Bedrock"):
        return "aws"
    if mode.startswith("Adobe Firefly"):
        return "firefly"
    if mode.startswith("Google AI Studio"):
        return "google"
    return "mock"


def render_byok_inputs() -> None:
    """Provider-specific Bring Your Own Key UI (sidebar).

    Dispatches on the selected provider. To support a new backend later,
    add an elif branch rendering its credential widgets (e.g. a GCP
    service-account JSON uploader for a Vertex provider) and collect the
    values in collect_credentials(). Mock needs no keys, so no expander.
    Values live only in st.session_state (cleared by Start Over) and are
    never written to disk or logged.
    """
    kind = selected_provider_kind()
    if kind == "aws":
        with st.sidebar.expander("🔑 Bring Your Own Key (AWS)"):
            st.text_input("AWS Access Key ID", type="password", key="byok_aws_access_key_id")
            st.text_input(
                "AWS Secret Access Key", type="password", key="byok_aws_secret_access_key"
            )
            st.text_input(
                "AWS Session Token (optional)", type="password", key="byok_aws_session_token"
            )
            st.caption(
                "Leave empty to use the environment default (.env / AWS profile). "
                "Keys stay in this session only."
            )
    elif kind == "firefly":
        with st.sidebar.expander("🔑 Bring Your Own Key (Adobe Firefly)"):
            st.text_input("Client ID", type="password", key="byok_firefly_client_id")
            st.text_input("Client Secret", type="password", key="byok_firefly_client_secret")
            st.caption(
                "Adobe Developer Console S2S credentials. Exchanged for a "
                "short-lived IMS token at run time; stored in this session only."
            )
    elif kind == "google":
        with st.sidebar.expander("🔑 Bring Your Own Key (Google AI Studio)"):
            st.text_input("API Key", type="password", key="byok_google_api_key")
            st.caption(
                "Gemini API key from aistudio.google.com. Stored in this "
                "session only."
            )
    # mock: no credentials required — no expander.


_BYOK_FIELDS: dict[str, dict[str, str]] = {
    # provider kind -> {credentials dict key: session_state key}
    "aws": {
        "aws_access_key_id": "byok_aws_access_key_id",
        "aws_secret_access_key": "byok_aws_secret_access_key",
        "aws_session_token": "byok_aws_session_token",
    },
    "firefly": {
        "client_id": "byok_firefly_client_id",
        "client_secret": "byok_firefly_client_secret",
    },
    "google": {
        "api_key": "byok_google_api_key",
    },
}


def collect_credentials() -> dict:
    """Pack the BYOK session values into a provider-agnostic credentials dict."""
    fields = _BYOK_FIELDS.get(selected_provider_kind(), {})
    return {
        cred_key: str(st.session_state.get(state_key, "")).strip()
        for cred_key, state_key in fields.items()
        if str(st.session_state.get(state_key, "")).strip()
    }


def make_provider() -> ImageGenerationProvider:
    """Instantiate the GenAI backend chosen in the sidebar toggle."""
    kind = selected_provider_kind()
    if kind == "aws":
        return AwsBedrockProvider(credentials=collect_credentials())
    if kind == "firefly":
        return FireflyProvider(credentials=collect_credentials())
    if kind == "google":
        return GoogleStudioProvider(credentials=collect_credentials())
    return MockImageProvider()


def run_pipeline(brief: CampaignBrief, provider: ImageGenerationProvider) -> None:
    """Execute the locked core pipeline product-by-product with live UI feedback."""
    st.caption(f"GenAI provider: `{provider.provider_name}`")
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

        prompt = build_image_prompt(brief, product)
        with st.spinner(f"Resolving base asset for {product.product_id}…"):
            base_image, reused = resolve_base_image(
                brief.campaign_id, product.product_id, prompt, provider
            )
        assets_reused += int(reused)
        assets_generated += int(not reused)
        if reused:
            st.info(
                f"↺ Reused sandboxed asset `assets/{brief.campaign_id}/{product.product_id}`",
                icon="📁",
            )
        else:
            st.info(
                f"✦ No local asset — generated via provider `{provider.provider_name}`",
                icon="🤖",
            )
            with st.expander("🪄 Generation prompt"):
                st.code(prompt, language=None, wrap_lines=True)

        output_paths: dict[str, Path] = {}
        product_dir = campaign_dir / product.product_id
        for ratio in SUPPORTED_RATIOS:
            with st.spinner(f"Composing {product.product_id} @ {ratio}…"):
                creative = processor.crop_to_aspect_ratio(base_image, ratio)
                creative = processor.render_text(creative, message)
                # GR-2 hard gate; campaign brand kit falls back to global mark
                creative = processor.apply_watermark(creative, brief.campaign_id)

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

    run_requested = render_sidebar()
    payload = st.session_state.get("brief_payload")

    if payload is None:
        st.info(
            "⬅️ Import a campaign brief (JSON/YAML) or build one manually "
            "in the sidebar to get started.",
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

    if not run_requested:
        render_brief_summary(brief)
        st.success(
            "Brief validated. Hit **Run Pipeline** (Import) or **Save & Run** "
            "(Manual Builder) in the sidebar when ready.",
            icon="✅",
        )
        return

    # Campaign merging: combine with the stored brief for this campaign_id
    # (if any) and persist the result before executing.
    try:
        brief, was_merged = merge_and_persist_brief(brief)
    except ValueError as exc:
        st.error(f"**Brief rejected:** {exc}", icon="🚫")
        return
    st.session_state["brief_payload"] = brief.model_dump()
    if was_merged:
        st.info(
            f"⇄ Campaign `{brief.campaign_id}` already exists — merged with the "
            f"stored brief and saved to `inputs/{brief.campaign_id}.json` "
            f"(regions/audiences deduplicated, products upserted).",
            icon="🧬",
        )

    render_brief_summary(brief)

    try:
        run_pipeline(brief, make_provider())
    except WatermarkMissingError as exc:
        st.error(f"**Brand compliance failure (GR-2):** {exc}", icon="🛑")
    except ProviderGenerationError as exc:
        st.error(
            f"**GenAI provider failure:** {exc}\n\n"
            f"Check your BYOK credentials / environment configuration "
            f"(see `.env.example`) — or switch back to Mock mode in the sidebar.",
            icon="☁️",
        )


if __name__ == "__main__":
    main()
