# Business Card Lead Extractor — Qwen VLM (React + FastAPI)

Bulk-upload business card images → extract structured leads with a real
open-source Qwen vision-language model (via API) → review/edit in the browser →
download as Excel.

**Lead fields:** First Name, Last Name, Position / Job Title, Company, Location,
Phone Number, Email Address (+ Source File).

**Verified end-to-end:** 2-card batch through `Qwen/Qwen3-VL-8B-Instruct` returned
all 7 fields exactly correct on both cards, and the exported `leads.xlsx`
contained the correct headers and both data rows.

---

## 1. Architecture & major technical decisions

```
Browser (React, dark UI) ──▶ FastAPI on EC2 t2.micro ──▶ Qwen VLM API ──▶ JSON leads ──▶ Excel
                                                         ├─ huggingface (DEFAULT, real, tested)
                                                         ├─ openrouter  (needs $ credit)
                                                         ├─ groq        (fast, free tier; vision models)
                                                         ├─ dashscope   (official Alibaba API)
                                                        ├─ ollama      (local GPU, no key)
                                                        └─ mock        (offline demo, no key)
```

**D1 — Remote VLM API instead of local hosting (most important decision).**
A free-tier EC2 `t2.micro` has 1 GB RAM; even the smallest Qwen-VL (2B) needs
≈5 GB, a 7B needs ≈16 GB. Hosting the model on the server is physically
impossible on the free tier, so the EC2 instance runs only this lightweight app
(~300 MB: FastAPI + static React) and calls Qwen over HTTPS. This keeps the
deployment free-tier-eligible while still using a state-of-the-art VLM.

**D2 — Provider abstraction (`backend/providers.py`).**
All six providers implement one interface, `extract_lead(image_bytes) -> dict`,
sharing a single extraction prompt, image preprocessing (downscale to ≤1568 px
JPEG to save bandwidth), and lenient JSON parsing. Switching models/providers is
one env var (`QWEN_PROVIDER`), no code change.

**D3 — Single-container deployment.**
The React app builds to static files served by FastAPI itself, so the whole
product is one Docker image, one port (8000), one process — trivial to run on
EC2, Elastic Beanstalk, or App Runner.

**D4 — Batch isolation.**
One card failing (bad file, API error) never fails the batch: that row is
returned with the error in its `Position` field and all other cards still
extract normally. API error bodies are surfaced verbatim so auth/billing/routing
problems are diagnosable from the UI.

**D5 — Explicit provider pin for Hugging Face.**
HF auto-routing returned *"not supported by any provider you have enabled"*
even with providers enabled, while the explicit
`Qwen/Qwen3-VL-8B-Instruct:featherless-ai` slug returned HTTP 200 immediately.
The default therefore pins the provider (see `QWEN_MODEL`).

---

## 2. Libraries, frameworks, pretrained models & external components

### Backend (`backend/requirements.txt`)
| Component | Version | Role |
|---|---|---|
| FastAPI | 0.115.6 | REST API (`/api/extract`, `/api/export-excel`, `/api/health`, `/api/config`) + serves React build |
| uvicorn | 0.34.0 | ASGI server |
| python-multipart | 0.0.20 | Bulk image upload parsing |
| Pillow | 11.1.0 | Image downscale → JPEG data-URI for the VLM |
| pandas | 2.2.3 | Lead → DataFrame |
| openpyxl | 3.1.5 | `.xlsx` generation (column widths, autofilter, frozen header) |
| httpx | 0.28.1 | Calls to VLM APIs |
| pydantic | 2.10.4 | Request validation (`Lead`, `ExportRequest`) |
| python-dotenv | 1.0.1 | `.env` config loading |

### Frontend (`frontend/`)
| Component | Version | Role |
|---|---|---|
| React | 18.3.1 | Bulk drag-drop upload, editable leads table, Excel download |
| Vite (+ @vitejs/plugin-react) | 6.0.3 | Dev server (proxies `/api` → :8000); production build emits to `backend/static` |

### Pretrained model (the VLM)
- **Qwen/Qwen3-VL-8B-Instruct** (Alibaba Qwen team, open-source weights on
  Hugging Face) — current-generation 8B multimodal Instruct model with strong
  OCR/document understanding. Served server-side by the **featherless-ai**
  Inference Provider; the app never downloads weights.
- Alternatives selectable via env: `qwen3-vl-8b-instruct` / `qwen2.5-vl-72b-instruct`
  (OpenRouter), `qwen-vl-max` (DashScope), `qwen2-vl` (Ollama),
  `meta-llama/llama-4-scout-17b-16e-instruct` / `qwen/qwen3.6-27b` (Groq).

### External components / services
- **Hugging Face Inference Providers** (`router.huggingface.co/v1/chat/completions`,
  OpenAI-compatible) — live model serving; needs `HF_TOKEN` + enabled provider.
- **OpenRouter / DashScope / Groq / Ollama** — alternative serving paths (same interface).
- **AWS** — EC2 `t2.micro` (free tier), Elastic Beanstalk, or App Runner for hosting;
  ECR for images; Secrets Manager/SSM recommended for keys in production.
- **Docker** — multi-stage image (`node:20-slim` build → `python:3.11-slim` runtime).

---

## 3. Setup (local)

Prerequisites: Python 3.11+, Node 20+.

```powershell
# 1. Backend
pip install -r backend\requirements.txt
copy .env.example .env
# Edit .env for the REAL model:
#   QWEN_PROVIDER=huggingface
#   HF_TOKEN=hf-your-token
# Token: https://huggingface.co/settings/tokens (fine-grained, permission
# "Make calls to Inference Providers"); enable a provider that serves the model
# (e.g. featherless-ai) at https://huggingface.co/settings/inference-providers
uvicorn main:app --app-dir backend --port 8000   # → http://localhost:8000

# 2. Frontend dev (optional — the backend already serves the built UI)
cd frontend; npm install; npm run dev            # → http://localhost:5173

# 3. Rebuild production UI after frontend changes
cd frontend; npm run build   # outputs to backend/static
```

Without any key the app runs in `mock` demo mode (clearly labeled sample data)
so upload → table → Excel can be tested offline.

Quick API test:

```powershell
curl http://localhost:8000/api/health
curl http://localhost:8000/api/config
curl -F "files=@card1.png" -F "files=@card2.png" http://localhost:8000/api/extract
```

---

## 4. Deployment (AWS — public URL in ~10 min)

### Option A — EC2 `t2.micro` free tier (recommended)

1. EC2 → Launch instance: AMI **Amazon Linux 2023**, type **`t2.micro`**
   (750 hrs/month free for 12 months on new accounts).
2. Security group: allow inbound **TCP 80** (and 8000) from `0.0.0.0/0`.
3. Advanced → User data: paste `user-data.sh` (installs Docker, builds and runs
   the app; set `QWEN_PROVIDER` + key env vars, or place a `.env` at
   `/opt/card-extractor/.env`).
4. Copy the project up and start (or set `GIT_REPO` before first boot):
   ```bash
   scp -r . ec2-user@<EC2-IP>:/opt/card-extractor
   ssh ec2-user@<EC2-IP>
   cd /opt/card-extractor && sudo docker build -t card-extractor . \
     && sudo docker run -d --restart always -p 80:8000 --env-file .env card-extractor
   ```
5. Public URL: `http://<EC2-PUBLIC-IP>/`. For production add an Elastic IP,
   ALB + ACM (HTTPS), and move keys to Secrets Manager/SSM.

Or with Docker Compose locally / on any host:

```bash
cp .env.example .env   # fill in provider + key
docker compose up --build   # → http://localhost:8000
```

### Option B — Elastic Beanstalk (Docker, free-tier eligible)

```bash
eb init -p docker card-extractor --region us-east-1
eb create card-extractor-env --instance-type t2.micro --single
eb setenv QWEN_PROVIDER=huggingface HF_TOKEN=hf-your-token
eb open   # → public HTTPS URL
```

### Option C — App Runner / ECS Fargate (serverless)

```bash
aws ecr create-repository --repository-name card-extractor
docker build -t card-extractor .
docker tag card-extractor <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/card-extractor:latest
docker push <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/card-extractor:latest
# App Runner console → Create service → ECR image, port 8000 → public URL
```

---

## 5. API reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | `{ status, provider, configured, lead_keys }` |
| GET | `/api/config` | Active provider + model description (drives the UI status pill) |
| POST | `/api/extract` | multipart `files` (≤50 files, ≤10 MB each) → `{ provider, leads[], errors[] }` |
| POST | `/api/export-excel` | `{ leads[] }` → `leads.xlsx` download |
| GET | `/` | React UI (when `backend/static` is built) |

---

## 6. AI Usage

**Which AI tools were used.**
All implementation work in this repo was done with an AI coding agent —
**Muse Spark** (Meta), operating inside **OpenCode** — driven by the repo owner
through short natural-language instructions (e.g. "use React", "dark mode",
"use Hugging Face"). Web search/fetch was used by the agent for live
verification (model availability, API behavior, error-code docs). No other AI
tools (no Copilot/ChatGPT/Claude direct use, no AI image generation) were
involved.

**What they were used for.**
- Greenfield scaffolding: FastAPI backend, Qwen provider abstraction, React
  frontend, Dockerfile/compose, EC2 user-data, this README.
- Debugging live API failures end-to-end: the agent issued real requests to
  OpenRouter (`GET /api/v1/models`, error-body inspection) and Hugging Face
  (`GET /api/models/...?expand[]=inferenceProviderMapping`,
  `GET router.../v1/models/...`) to ground each fix in observed behavior.
- Verification: synthetic business-card images → real extraction → field-by-field
  assertions → Excel round-trip checks.

**Significant AI-generated code/architecture adopted.**
- Remote-API-over-local-hosting architecture (D1) and the provider
  abstraction with shared prompt/parsing (D2) — proposed by the agent, kept.
- Free-first OpenRouter fallback chain and verbatim API-error surfacing in the
  `Position` field — proposed by the agent after reading live error bodies, kept.
- The `:featherless-ai` provider pin — diagnosed by the agent from a live
  pinned-vs-auto routing experiment (pinned → 200, auto → 400), kept as default.

**AI recommendations rejected or modified.**
- *Streamlit UI*: agent proposed Streamlit for speed; owner overrode with
  "use React" → built React + Vite instead.
- *Default model `Qwen/Qwen2.5-VL-7B-Instruct`*: agent's first pick from HF docs;
  **rejected after live testing** — the Hub mapping showed its sole provider in
  `error` state and the router returned `model_not_supported`. Replaced with the
  verified-live `Qwen/Qwen3-VL-8B-Instruct:featherless-ai`.
- *OpenRouter `:free` slugs*: agent initially assumed free Qwen-VL variants
  existed; **corrected after a live `/models` listing proved none exist** (dead
  slugs removed, docs updated to state paid credit is required).
- *Temporary tunnel URL for "public deployment"*: considered, **rejected** —
  owner had no AWS credentials, so the agent shipped one-command deploy assets
  plus a local verified URL instead of a fragile tunnel link.

---

## 7. Notes & limits

- Extraction quality depends on image clarity and the serving model; the UI table
  is editable so any field can be corrected before Excel export.
- `mock` mode returns clearly labeled demo data — never mistaken for real output.
- Images are downscaled to ≤1568 px JPEG before being sent to the VLM.
- Production hardening still to do: restrict CORS in `backend/main.py`, add
  auth/rate-limiting, move keys to Secrets Manager/SSM, serve behind HTTPS.
#   B u s i n e s s C a r d E x t r a c t i o n  
 