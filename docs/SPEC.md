# SAC — Social Ad Campaigns

## Technical Specification (v1.0 — Proof of Concept)

| | |
|---|---|
| **Status** | Draft — Pending Approval |
| **Date** | 2026-06-11 |
| **Scope** | Python CLI PoC pipeline |
| **Audience** | Engineering, Product, Creative Operations |

---

## 1. Background & Business Context

### 1.1 Scenario

Creative automation for scalable social ad campaigns.

### 1.2 Client Profile

A global consumer goods company launching **hundreds of localized social ad campaigns per month** across multiple regions, audiences, and product lines.

### 1.3 Business Goals

| # | Goal | How SAC Addresses It |
|---|------|----------------------|
| G1 | Accelerate campaign velocity | Automated end-to-end creative generation from a single declarative brief |
| G2 | Ensure brand consistency | Programmatic brand compliance (logo/watermark overlay) on every output |
| G3 | Maximize relevance & personalization | Per-product, per-region, per-audience creative variants driven by brief data |
| G4 | Optimize marketing ROI | Asset reuse where possible; GenAI generation only when assets are missing |
| G5 | Gain actionable insights | Rich, structured execution logging of every pipeline run |

### 1.4 Pain Points Addressed

- **Manual content creation overload** — creative teams hand-produce every variant today.
- **Bottlenecks scaling localized creatives** — each new region/aspect-ratio combination multiplies manual effort.

---

## 2. System Overview

SAC is a **Python-based CLI pipeline** that ingests a structured campaign brief, resolves or generates product imagery, applies guardrails, and emits ready-to-publish social ad creatives in multiple aspect ratios, organized on local disk.

### 2.1 High-Level Pipeline

```
┌────────────┐   ┌────────────┐   ┌─────────────┐   ┌────────────┐   ┌───────────┐
│  Brief      │   │  Legal      │   │  Asset       │   │  Image      │   │  Output    │
│  Ingestion  │──▶│  Guardrail  │──▶│  Resolution  │──▶│  Processing │──▶│  Writer    │
│  (JSON/YAML)│   │  (msg check)│   │  (local→GenAI)│  │  (ratios,   │   │  (organized│
│  + validate │   │             │   │              │   │  text, logo)│   │  folders)  │
└────────────┘   └────────────┘   └─────────────┘   └────────────┘   └───────────┘
```

---

## 3. Functional Requirements

### FR-1: Campaign Brief Ingestion (Inputs)

The system SHALL accept a campaign brief file in **JSON or YAML** format, located in the `inputs/` directory.

The brief SHALL contain, at minimum:

| Field | Constraint |
|-------|-----------|
| `products` | **At least two (2)** distinct products |
| `target_regions` | One or more target regions/markets (e.g., `US`, `DE`, `JP`) |
| `target_audiences` | One or more target audience descriptors |
| `campaign_messages` | Campaign message(s) to render on the final creatives |

- Briefs SHALL be validated on load using **Pydantic** models. Invalid briefs SHALL fail fast with clear, human-readable error reporting.

### FR-2: Local Asset Reuse

- The system SHALL look for user-provided input assets in the local mock folder `assets/`.
- When a matching asset exists for a product, the system SHALL **reuse it** instead of generating a new one.
- Asset matching convention (PoC): filename match on product identifier (e.g., `assets/<product_id>.png`).

### FR-3: GenAI Asset Generation (Fallback)

- When no local asset is found for a product, the system SHALL generate a new image using a **GenAI image model**.
- Generation SHALL be driven by a prompt composed from the product description and campaign context in the brief.

### FR-4: Provider-Agnostic GenAI Architecture *(Architectural Requirement)*

GenAI image generation MUST be **provider-agnostic**, implemented via a **pluggable strategy pattern**.

#### 4.1 Interface Contract

All providers SHALL implement a common abstract interface:

```python
from abc import ABC, abstractmethod

class ImageGenerationProvider(ABC):
    """Strategy interface for pluggable GenAI image generation backends."""

    @abstractmethod
    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns raw image bytes."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier (for logging/reporting)."""
        ...
```

#### 4.2 Required Concrete Strategies

| Strategy | Cloud | Backing Service | SDK / Transport |
|----------|-------|-----------------|-----------------|
| `AwsBedrockProvider` | AWS | Amazon Bedrock (Stability next-gen / SDXL / Titan-Nova payload families) | `boto3` |
| `GoogleStudioProvider` | Google | Google AI Studio (Gemini image generation, API-key auth) | `google-generativeai` |
| `FireflyProvider` | Adobe | Firefly Image API (S2S OAuth via Adobe IMS) | `requests` (REST) |
| `MockImageProvider` | — | Local Pillow placeholder (offline development, NFR-3) | `Pillow` |

#### 4.3 Selection & Extensibility

- The active provider SHALL be selectable at runtime (CLI flag and/or config), e.g., `--provider aws` / `--provider gcp`.
- Adding a new provider SHALL require only a new strategy class implementing `ImageGenerationProvider` — **no changes to pipeline code** (Open/Closed Principle).
- For the PoC, a local mock/stub provider MAY be included for offline development and testing.

### FR-5: Multi-Aspect-Ratio Image Processing

The system SHALL produce creatives in **at least three (3) aspect ratios** per product:

| Ratio | Canonical Use |
|-------|---------------|
| `1:1` | Feed posts (Instagram/Facebook square) |
| `9:16` | Stories / Reels / TikTok vertical |
| `16:9` | Landscape / YouTube / display |

- Source images SHALL be resized/cropped to each target ratio using **Pillow**, preserving the product as the focal subject (PoC: center-crop acceptable).

### FR-6: Dynamic Text Rendering

- The system SHALL render the campaign message from the brief **dynamically onto each final creative** (drawn onto the image, not baked into the source asset).
- Text SHALL remain legible across all aspect ratios (PoC: positioned band/area with contrast treatment acceptable).

### FR-7: Organized Output

- Final creatives SHALL be saved to the local `outputs/` directory.
- Outputs SHALL be **clearly organized by product and aspect ratio**,
  saved as flat files named `{ratio}_final.png` inside each product
  folder (not in per-ratio subdirectories):

```
outputs/
└── <campaign_id>/
    ├── <product_id>/
    │   ├── 1x1_final.png
    │   ├── 9x16_final.png
    │   └── 16x9_final.png
    └── <product_id_2>/
        └── ...
```

### FR-8: Campaign Merging (Upsert)

- A brief whose `campaign_id` already exists in `inputs/` (as
  `inputs/{campaign_id}.json`) SHALL be **merged** with the stored brief
  before the pipeline runs:
  - `target_regions` / `target_audiences`: union, deduplicated, order
    preserved (existing entries first).
  - `products`: **upsert** keyed on `product_id` — existing products get
    their details (and paired campaign message) replaced; new products
    are appended.
  - `campaign_messages` remain aligned one-per-product so the
    `message[i] ↔ product[i]` pairing survives the merge.
- The merged result SHALL be written back to `inputs/{campaign_id}.json`,
  making the stored brief the cumulative source of truth per campaign.
- `campaign_id` / `product_id` values SHALL be validated as safe single
  path segments (no separators or `..`) before any filesystem write.

### FR-9: Provider-Agnostic BYOK (Bring Your Own Key)

- The `ImageGenerationProvider` interface SHALL accept an optional
  `credentials: dict` whose keys are provider-specific (AWS access
  key/secret/session token, Adobe client id/secret, Google API key).
- When credentials are omitted or empty, providers SHALL fall back to
  their environment-default credential chain (`.env`, `~/.aws`,
  `FIREFLY_*`, `GOOGLE_API_KEY`).
- Credential values MUST never be printed, logged, or persisted to disk;
  in the web UI they live only in the Streamlit session (masked inputs)
  and are cleared by the session reset control.
- All provider failures SHALL surface as a single generic
  `ProviderGenerationError` so UI layers handle any backend uniformly.

---

## 4. Guardrails

### GR-1: Legal Content Check (Pre-Processing)

- Before any processing begins, the system SHALL run a **simple legal content check** on each campaign message.
- PoC implementation: scan against a configurable prohibited-word list (e.g., `"free"`, `"guaranteed"`, `"cure"`, regulated claims).
- On violation: the system SHALL **flag the message**, report which word(s) triggered the check, and halt (or skip) processing for that message with a clear log entry.

### GR-2: Brand Compliance (Watermark Overlay)

- Every final creative SHALL have the **brand logo/watermark programmatically overlaid** before being written to `outputs/`.
- The watermark asset SHALL live in `assets/` (e.g., `assets/brand_logo.png`).
- Overlay SHALL be applied consistently (PoC: fixed corner position with alpha blending).
- No creative SHALL be written to `outputs/` without the watermark — this is a hard gate.

---

## 5. CLI / UX Requirements

- The CLI SHALL use the **`rich`** library for terminal output.
- Required UX elements:
  - **Progress bars** for long-running stages (asset generation, image processing).
  - **Detailed execution logging** — per-stage status, per-product/per-ratio results, guardrail outcomes.
  - Styled summary table at end of run (counts: assets reused, assets generated, creatives produced, guardrail flags).
- Errors SHALL be presented clearly and attractively (no raw tracebacks for expected failure modes such as invalid briefs or guardrail violations).

---

## 6. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | **Language/Runtime:** Python 3 CLI application; all logic under `src/` |
| NFR-2 | **Extensibility:** New GenAI providers added without modifying pipeline code (see FR-4) |
| NFR-3 | **Offline-friendly PoC:** Pipeline runnable end-to-end with local assets only (no cloud calls) when assets cover all products |
| NFR-4 | **Validation-first:** All external inputs (briefs) validated via Pydantic before use |
| NFR-5 | **Determinism of layout:** Identical brief + assets produce identically organized outputs |

---

## 7. Project Structure

```
SAC/
├── docs/           # This specification and future design docs
├── src/            # All Python application logic
├── inputs/         # Mock campaign briefs (JSON/YAML)
├── assets/         # User-provided mock images + brand watermark
├── outputs/        # Generated creatives (product/ratio organized)
└── requirements.txt
```

---

## 8. Dependencies

| Package | Purpose |
|---------|---------|
| `Pillow` | Image processing: resize/crop to aspect ratios, text rendering, watermark overlay |
| `rich` | CLI UX: progress bars, styled logging, summary tables |
| `streamlit` | Web front-end (brief import/builder, live pipeline feedback, creative gallery) |
| `pydantic` | Brief schema validation |
| `boto3` | AWS Bedrock image generation strategy |
| `google-generativeai` | Google AI Studio (Gemini) image generation strategy |
| `requests` | Adobe Firefly REST integration (S2S OAuth + Image API) |
| `python-dotenv` | Environment/credential loading from `.env` |
| `PyYAML` | YAML brief parsing (implied by FR-1 JSON/YAML support) |

---

## 9. Out of Scope (PoC)

- Publishing to social platforms / ad networks
- Web UI or API server
- Persistent storage beyond local filesystem
- Advanced ML-based smart cropping or layout
- Localization/translation of campaign messages (messages arrive pre-localized in brief)

---

## 10. Acceptance Criteria

1. Given a valid JSON or YAML brief with ≥2 products, the pipeline produces creatives for every product in **all three aspect ratios**, saved as `outputs/<campaign>/<product>/{ratio}_final.png`.
2. Given a product with a matching local asset in `assets/`, that asset is **reused** (no GenAI call).
3. Given a product with no local asset, an image is **generated** via the configured provider strategy.
4. Switching `--provider` between `mock`, `aws`, `firefly`, and `google` requires **zero pipeline code changes**.
5. A campaign message containing a prohibited word is **flagged and blocked** before image processing.
6. Every output creative contains the **rendered campaign message** and the **brand watermark**.
7. The CLI run displays **progress bars** and ends with a **styled execution summary**.
