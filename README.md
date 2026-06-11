# SAC — Social Ad Campaigns

**Creative automation for scalable, localized social ad campaigns — a Streamlit web app over a Python pipeline core.**

SAC turns a single campaign brief (JSON/YAML) into ready-to-publish social ad creatives: it reuses or AI-generates product imagery, renders the campaign message, enforces legal and brand guardrails, and outputs every product in three aspect ratios (1:1, 9:16, 16:9) — one click instead of a manual per-variant workflow.

---

## Setup & Installation

Requires Python 3.10+.

```bash
git clone https://github.com/sandy-sp/SAC.git && cd SAC
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Environment (optional — only for live GenAI providers):**

```bash
cp .env.example .env   # then fill in the credentials you need
```

No cloud credentials are required to try SAC — the default **Mock** provider runs fully offline. Live providers (AWS Bedrock, Adobe Firefly, Google AI Studio) read credentials from `.env` / your environment, or accept them directly in the web UI (BYOK).

---

## Run the Web App

```bash
streamlit run app.py
```

Then in the browser (http://localhost:8501):

1. **Load a brief** — upload a JSON/YAML brief, click **Load Default Mock Brief**, or build one in the **Manual Builder**.
2. **Run Pipeline** — watch the live progress, guardrail verdicts, and the finished creatives appear side by side.

The bundled mock brief demonstrates both paths: one product generates cleanly across all three ratios; the other contains a prohibited word and is visibly blocked by the legal guardrail.

---

## Run the CLI (Headless)

```bash
python main.py                                              # default mock brief, offline
python main.py --brief inputs/mock_brief.json --provider mock
python main.py --brief inputs/my_campaign.yaml --provider aws   # live GenAI via Bedrock
```

`--provider` accepts `mock`, `aws`, `firefly`, or `google`; live providers read credentials from the environment. Outputs land in `outputs/<campaign_id>/<product_id>/{ratio}_final.png`.

---

## Assumptions and Limitations

- Input campaign messages are assumed to be in English.
- Output image resolution and generation speed are strictly bounded by the selected GenAI provider's API limits.
- For local asset reuse, the user-provided filenames must exactly match the `product_id` (e.g., `product_id.jpg`) and reside in the correct campaign sandbox directory.

---

## Documentation

- [`docs/SPEC.md`](docs/SPEC.md) — full technical specification: functional requirements, guardrails, acceptance criteria.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design and enterprise scalability: the provider strategy pattern, BYOK, campaign sandboxing & merging, guardrail deep-dive, and production deployment strategies.
