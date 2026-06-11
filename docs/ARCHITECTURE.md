# SAC — System Architecture

This document details the system design and enterprise scalability of the SAC pipeline — the component architecture, the provider strategy pattern, the guardrail enforcement model, and the path to production. For requirements see [`SPEC.md`](SPEC.md); for setup and usage see the root [`README.md`](../README.md).

---

## Pipeline Overview

```
┌────────────┐   ┌────────────┐   ┌──────────────┐   ┌─────────────┐   ┌───────────┐
│  Brief      │   │  Legal      │   │  Asset        │   │  Image       │   │  Output    │
│  Ingestion  │──▶│  Guardrail  │──▶│  Resolution   │──▶│  Processing  │──▶│  Writer    │
│  (JSON +    │   │  (GR-1 hard │   │  (local reuse │   │  (3 ratios,  │   │  (product/ │
│  Pydantic)  │   │  gate)      │   │  → GenAI)     │   │  text, logo) │   │  ratio)    │
└────────────┘   └────────────┘   └──────┬───────┘   └─────────────┘   └───────────┘
                                          │
                              ┌───────────▼────────────┐
                              │ ImageGenerationProvider │  ◀── Strategy interface (ABC)
                              ├────────────────────────┤
                              │ MockImageProvider       │  (offline placeholder)
                              │ AwsBedrockProvider      │  (AWS Bedrock, live)
                              │ FireflyProvider         │  (Adobe Firefly, live)
                              │ GoogleStudioProvider    │  (Google AI Studio, live)
                              └────────────────────────┘
```

### Key Components

| Path | Responsibility |
|------|----------------|
| `app.py` | **Streamlit web app** — primary entry point; brief import / manual builder, live pipeline feedback, in-browser creative gallery |
| `main.py` | CLI orchestrator — headless alternative with rich terminal UX |
| `src/utils.py` | Campaign merging — region/audience dedupe, product upsert, brief persistence, safe-id path validation |
| `src/models.py` | Pydantic brief schema; enforces ≥2 products (FR-1) |
| `src/guardrails.py` | Legal content check — prohibited-word scan, hard gate (GR-1) |
| `src/image_processor.py` | Aspect-ratio cropping, text rendering, watermark overlay (FR-5/6, GR-2) |
| `src/prompt_builder.py` | Dynamic commercial-photography prompt composition from brief data (FR-3) |
| `src/providers/base.py` | `ImageGenerationProvider` strategy interface + `ProviderGenerationError` (FR-4, FR-9) |
| `src/providers/mock.py` | Offline mock provider — Pillow placeholder, no cloud calls (NFR-3) |
| `src/providers/aws.py`, `firefly.py`, `google_studio.py` | Live GenAI strategies — AWS Bedrock, Adobe Firefly (S2S OAuth), Google AI Studio |

---

## The Provider Strategy Pattern

The pipeline depends only on the `ImageGenerationProvider` abstract base class — never on a concrete backend (Open/Closed Principle). Each strategy implements `generate_image(prompt, width, height) -> bytes` plus a `provider_name` property:

- **`MockImageProvider`** — Pillow-generated placeholder; lets the entire pipeline run offline with zero credentials (NFR-3).
- **`AwsBedrockProvider`** — boto3 `bedrock-runtime`; auto-detects the payload format for three model families (Stability next-gen `stable-image-core`/`sd3-5`/`ultra`, classic SDXL, Titan/Nova `TEXT_IMAGE`) from the model id, and snaps requested dimensions to each family's supported sizes — lossless because the downstream processor center-crops to exact campaign ratios.
- **`FireflyProvider`** — Adobe S2S OAuth: exchanges client id/secret at Adobe IMS for a short-lived token, then calls the Firefly Image API and downloads the output. Firefly is trained on licensed content and designed for commercially safe output; its brand-kit capabilities align directly with the brand-consistency goal.
- **`GoogleStudioProvider`** — Google AI Studio (Gemini) via plain API key; image bytes parsed from `inline_data` response parts.

Adding a new backend is one new class in `src/providers/` plus one routing entry — the orchestrator never changes. Provider selection is runtime-switchable: `--provider {mock,aws,firefly,google}` in the CLI, the "AI Generation Mode" selector in the web app.

All provider failures surface as a single generic `ProviderGenerationError`, so both UIs handle any backend uniformly (styled error box in Streamlit, clean message + exit 1 in the CLI).

---

## Provider-Agnostic BYOK (Bring Your Own Key)

Every provider accepts an optional `credentials: dict` whose keys are provider-specific:

| Provider | Credential keys | Fallback when empty |
|----------|-----------------|---------------------|
| AWS Bedrock | `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token` | boto3 default chain (`.env`, `~/.aws`, instance profile) |
| Adobe Firefly | `client_id`, `client_secret` | `FIREFLY_CLIENT_ID` / `FIREFLY_CLIENT_SECRET` |
| Google AI Studio | `api_key` | `GOOGLE_API_KEY` |
| Mock | — (none required) | — |

Security rules:

- Credential values are **never printed, logged, or persisted to disk**.
- In the web app, keys are entered through masked (`type="password"`) inputs, live only in `st.session_state`, and are wiped by **Start Over / Clear Session**.
- In headless mode, the CLI always passes an empty dict so providers defer to environment configuration.
- Only `.env.example` (placeholders) is committed; `.env` is gitignored.

The UI is table-driven: a single field-mapping (`_BYOK_FIELDS`) plus one expander branch per provider, so a future backend (e.g. a GCP Vertex service-account JSON uploader) is an `elif` away.

---

## Guardrails (Defense in Depth)

Both guardrails are **hard gates** — no creative ships unchecked:

- **GR-1 · Legal content (text level):** every campaign message is scanned against a prohibited-word list using case-insensitive **whole-word** regex matching (`\b` boundaries — "procured" does not trip on "cure"). A violation skips the entire product, names the offending word(s), and is visibly logged in both UIs.
- **GR-2 · Brand compliance (image level):** every creative gets the brand watermark composited bottom-right before it is written. The mark resolves through a **campaign brand-kit fallback hierarchy**: `assets/{campaign_id}/watermark.png` → `assets/watermark.png` → `WatermarkMissingError` (which aborts rather than silently skipping, listing every path checked).
- **Input hardening:** `campaign_id` / `product_id` are validated as safe single path segments (no separators, no `..`) before any filesystem write, closing path-traversal via crafted briefs.

---

## Enterprise Features

- **Campaign Sandboxing (Asset Isolation):** every campaign gets its own asset namespace — local base images live at `assets/{campaign_id}/{product_id}.ext`, created on demand. Campaigns can never pick up each other's imagery, so hundreds of concurrent campaigns stay cleanly isolated. (The global brand watermark remains at `assets/watermark.png` as the fallback mark.)
- **Intelligent Campaign Merging (Upserting):** submitting a brief whose `campaign_id` already exists in `inputs/` triggers an automatic merge with the stored brief before the run: `target_regions` and `target_audiences` are unioned and deduplicated, and `products` are **upserted** by `product_id` — existing products get their details (and paired campaign message) updated, new products are appended. The merged result is written back to `inputs/{campaign_id}.json`, making the stored brief the cumulative source of truth for that campaign.
- **Dynamic prompt building:** GenAI prompts are composed per product from the brief (product name/description, first target audience and region) into a commercial-photography template, keeping generated assets campaign-relevant.

### Design Decisions

- **Asset reuse before generation:** sandboxed local assets are reused when present; GenAI is the fallback, not the default — controlling cost and preserving approved imagery.
- **No distortion:** ratio conversion uses scale-to-cover + center-crop (`ImageOps.fit`), never stretching.
- **Validation-first:** all briefs pass Pydantic validation before any processing; invalid input fails fast with readable errors.

### Brief Format & Output Layout

```json
{
  "campaign_id": "summer-winter-glow-2026",
  "target_regions": ["US", "DE", "JP"],
  "target_audiences": ["Young professionals 25-34"],
  "campaign_messages": ["Message for product 1.", "Message for product 2."],
  "products": [
    { "product_id": "summer-skincare-bundle", "name": "...", "description": "..." },
    { "product_id": "winter-hydration-kit", "name": "...", "description": "..." }
  ]
}
```

At least **two products** are required; `campaign_messages[i]` pairs with `products[i]` (falling back to the first message). Outputs land at:

```
outputs/
└── <campaign_id>/
    └── <product_id>/
        ├── 1x1_final.png      (1080 × 1080 — feed posts)
        ├── 9x16_final.png     (1080 × 1920 — stories / reels)
        └── 16x9_final.png     (1080 × 608  — landscape / display)
```

---

## Path to Production / Future Enhancements

### GenAI Backends — Shipped and Next

The strategy pattern proved itself three times over: **AWS Bedrock**, **Adobe Firefly**, and **Google AI Studio** are all live, each with a different authentication flow (SDK credential chain, S2S OAuth, plain API key), with zero orchestrator changes. Next candidates:

- **GCP Vertex AI** (`VertexImageProvider`, via the `google-genai` SDK) — Imagen for organizations standardized on service-account auth rather than AI Studio API keys.
- **SDK migration:** `google-generativeai` is deprecated by Google; migrate `GoogleStudioProvider` to the `google-genai` successor SDK.

### Image-Level Brand Safety (Vision API)

Today's legal guardrail operates at the **text level** (prohibited words in campaign messages). A production deployment would add a second, **image-level** gate: after generation, each creative is scanned by a Vision API — e.g. **GPT-4o** (vision-language reasoning: "does this image contain off-brand, unsafe, or non-compliant content?") or **AWS Rekognition** (moderation labels, unsafe-content detection). This catches what text checks cannot — problematic imagery emerging from the GenAI model itself — and completes a defense-in-depth guardrail stack: text in, pixels out.

### Additional Roadmap

- **Localization pipeline:** per-region message translation and culturally adapted imagery, driven by `target_regions`.
- **Smart cropping:** subject-aware focal-point detection instead of center-crop, keeping products framed in every ratio.
- **Campaign analytics:** structured run logs (JSON) shipped to an analytics store, linking creative variants to downstream ad performance for ROI insight.
- **Brand kit configuration:** fonts, color palettes, logo placement rules, and prohibited-word lists per brand, versioned in config rather than code.
- **Parallel generation:** per-product concurrency for hundreds-of-campaigns scale.
- **CI + tests:** promote the smoke checks into a pytest suite with golden-image comparisons.
