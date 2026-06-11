# SAC — Social Ad Campaigns

**Creative automation for scalable, localized social ad campaigns — a Streamlit web app over a Python pipeline core.**

SAC ingests a structured campaign brief, resolves or generates product imagery, enforces legal and brand guardrails, and emits ready-to-publish social ad creatives in multiple aspect ratios — turning a manual, per-variant creative workflow into a single click.

Built for a global consumer goods scenario: hundreds of localized campaigns per month, where manual content creation is the bottleneck. See [`docs/SPEC.md`](docs/SPEC.md) for the full technical specification.

---

## System Architecture

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
                              │ MockImageProvider       │  (offline, implemented)
                              │ BedrockImageProvider    │  (AWS, planned)
                              │ VertexImageProvider     │  (GCP, planned)
                              └────────────────────────┘
```

### Key Components

| Path | Responsibility |
|------|----------------|
| `app.py` | **Streamlit web app** — primary entry point; brief upload, live pipeline feedback, in-browser creative gallery |
| `main.py` | CLI orchestrator — headless alternative with rich terminal UX |
| `src/models.py` | Pydantic brief schema; enforces ≥2 products (FR-1) |
| `src/guardrails.py` | Legal content check — prohibited-word scan, hard gate (GR-1) |
| `src/image_processor.py` | Aspect-ratio cropping, text rendering, watermark overlay (FR-5/6, GR-2) |
| `src/providers/base.py` | `ImageGenerationProvider` strategy interface (FR-4) |
| `src/providers/mock.py` | Offline mock provider — Pillow placeholder, no cloud calls (NFR-3) |

### Design Decisions

- **Provider-agnostic GenAI (Strategy Pattern):** the pipeline depends only on the `ImageGenerationProvider` abstract base class. Cloud backends plug in without touching pipeline code (Open/Closed Principle).
- **Guardrails are hard gates:** a flagged campaign message skips the product entirely; a missing brand watermark aborts with an explicit error. No creative ships unchecked.
- **Asset reuse before generation:** local assets in `assets/` are reused when present (`{product_id}.jpg/.jpeg/.png`); GenAI is the fallback, not the default — controlling cost and preserving approved imagery.
- **No distortion:** ratio conversion uses scale-to-cover + center-crop (`ImageOps.fit`), never stretching.

---

## Local Setup

Requires Python 3.10+.

```bash
git clone <repo-url> && cd SAC
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

No cloud credentials are needed — the PoC runs fully offline via the mock provider.

---

## Running the Web App

```bash
streamlit run app.py
```

Then in the browser (defaults to http://localhost:8501):

1. **Load a brief** — upload a JSON/YAML brief in the sidebar, or click **Load Default Mock Brief**.
2. **Run Pipeline** — hit the button in the sidebar.
3. **Watch it work** — live progress bar, per-product status, and the finished 1:1 / 9:16 / 16:9 creatives displayed side by side for every successful product.

The bundled `inputs/mock_brief.json` demonstrates both pipeline paths:

- **Summer Skincare Bundle** — clean message → full generation across all three ratios, shown in-browser.
- **Winter Hydration Kit** — message contains a prohibited word ("guaranteed") → flagged by the legal guardrail with a prominent error box, and skipped.

### Headless / CLI Mode

The original CLI remains available for scripted or terminal-only use:

```bash
python main.py                          # uses inputs/mock_brief.json
python main.py --brief inputs/my.json   # custom brief
```

### Campaign Brief Format

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

Briefs are validated on load; at least **two products** are required. `campaign_messages[i]` pairs with `products[i]` (falling back to the first message). To reuse your own imagery, drop `assets/{product_id}.jpg` in place before running.

---

## Expected Output Structure

```
outputs/
└── summer-winter-glow-2026/
    └── summer-skincare-bundle/
        ├── 1x1_final.png      (1080 × 1080 — feed posts)
        ├── 9x16_final.png     (1080 × 1920 — stories / reels)
        └── 16x9_final.png     (1080 × 608  — landscape / display)
```

Every creative carries the rendered campaign message (wrapped, on a contrast band) and the brand watermark (bottom-right) — both are mandatory.

---

## Path to Production / Future Enhancements

### Plugging in Real GenAI Backends

The Strategy Pattern makes new providers a drop-in exercise — implement `ImageGenerationProvider`, register it, done. No pipeline changes:

- **AWS Bedrock** (`BedrockImageProvider`, via `boto3`) — Titan Image Generator / Stable Diffusion.
- **GCP Vertex AI** (`VertexImageProvider`, via `google-cloud-aiplatform`) — Imagen.
- **Adobe Firefly** (`FireflyImageProvider`, via the Firefly Services REST API) — a natural fit for this client: Firefly is trained on licensed content and designed for **commercially safe** output, and its style-reference and brand-kit capabilities align directly with the brand-consistency goal. Adding it is one new class in `src/providers/` — the orchestrator never changes.

Provider selection would move to a `--provider {mock,aws,gcp,firefly}` CLI flag backed by a simple registry.

### Image-Level Brand Safety (Vision API)

Today's legal guardrail operates at the **text level** (prohibited words in campaign messages). A production deployment would add a second, **image-level** gate: after generation, each creative is scanned by a Vision API — e.g. **GPT-4o** (vision-language reasoning: "does this image contain off-brand, unsafe, or non-compliant content?") or **AWS Rekognition** (moderation labels, unsafe-content detection). This catches what text checks cannot — problematic imagery emerging from the GenAI model itself — and completes a defense-in-depth guardrail stack: text in, pixels out.

### Additional Roadmap

- **Localization pipeline:** per-region message translation and culturally adapted imagery, driven by `target_regions`.
- **Smart cropping:** subject-aware focal-point detection instead of center-crop, keeping products framed in every ratio.
- **Campaign analytics:** structured run logs (JSON) shipped to an analytics store, linking creative variants to downstream ad performance for ROI insight.
- **Brand kit configuration:** fonts, color palettes, logo placement rules, and prohibited-word lists per brand, versioned in config rather than code.
- **Parallel generation:** per-product concurrency for hundreds-of-campaigns scale.
- **CI + tests:** promote the smoke checks into a pytest suite with golden-image comparisons.
