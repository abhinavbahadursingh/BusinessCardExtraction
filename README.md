# Business Card Lead Extractor — Qwen VLM (React + FastAPI)

Bulk-upload business card images → extract structured leads with a Qwen
vision-language model (via API) → review / edit in the browser → download as Excel.

**Lead fields:** First Name, Last Name, Position / Job Title, Company, Location,
Phone Number, Email Address (+ Source File).

**Flow:** `React UI → POST /api/extract (images) → provider abstraction → VLM API → normalized JSON leads → editable table → POST /api/export-excel → leads.xlsx`

---

## 1. Project structure

```
BusinessCardExtraction/
├── backend/
│   ├── main.py           # FastAPI app: /api/health, /api/config, /api/extract, /api/export-excel + serves UI
│   ├── providers.py      # VLM provider abstraction (6 providers, prompt, image prep, parsing, retries)
│   ├── requirements.txt  # Pinned Python deps
│   ├── runtime.txt       # python-3.11.11 (EB / PaaS pin)
│   ├── __init__.py       # Empty
│   └── static/           # Git-ignored build output of frontend (vite build --outDir ../backend/static)
├── frontend/
│   ├── src/App.jsx       # Single-page app: upload, progress, editable grid, Excel download
│   ├── src/main.jsx      # React root
│   ├── src/styles.css    # Dark-mode design system (no UI framework)
│   ├── vite.config.js    # Dev proxy /api → :8000; build outDir → backend/static
│   ├── package.json      # React 18.3.1 + Vite 6.0.3
│   └── index.html
├── .env.example          # Documented provider/key template (commit-safe)
├── .env                  # Local only, git-ignored — never commit
├── user-data.sh          # EC2 Amazon Linux 2023 bootstrap (Docker + git + build/run)
└── README.md
```

> Note: there is currently **no `Dockerfile` / `docker-compose.yml`** in the repo,
> although `user-data.sh` and earlier docs reference `docker build/run`.
> See [Known limitations](#4-known-limitations--future-improvements).

---

## 2. Architecture & major technical decisions

```
Browser (React + Vite, dark UI)
  │  POST /api/extract (multipart files, 1 file/request from UI, N files/request supported by API)
  ▼
FastAPI (uvicorn :8000)
  ├── validate (≤50 files, ≤10 MB each, jpg/jpeg/png/webp/heic/heif)
  ├── providers.extract_lead(image_bytes, filename) per file
  │     ├── Pillow → downscale ≤1568px → JPEG q88 → base64 data URI
  │     ├── OpenAI-compatible chat/completions (system + user prompt, temperature 0, max_tokens 512)
  │     ├── lenient JSON parse + normalize to canonical lead dict
  │     └── on error: return blank lead with Position = "ERROR (<provider>): <message>"
  └── pandas + openpyxl → leads.xlsx
  │  GET /api/config → drives UI status pill (provider + configured?)
  ▼
Remote VLM API (selected by QWEN_PROVIDER): huggingface / openrouter / dashscope / groq / ollama / mock
```

### D1 — Remote VLM API instead of local hosting
A free-tier EC2 `t2.micro` has ~1 GB RAM; even Qwen-VL 2B needs ~5 GB and 7–8B
needs ~16 GB. The server therefore runs only the lightweight app
(~300 MB: FastAPI + static React) and calls Qwen over HTTPS. This keeps the
deployment free-tier-eligible while using a state-of-the-art VLM.

### D2 — Provider abstraction (`backend/providers.py`)
All providers share one interface — `extract_lead(image_bytes, filename) -> dict` —
plus one `SYSTEM_PROMPT` / `USER_PROMPT`, one image preprocessor
(`image_to_data_uri`), one lenient parser (`_parse_json_lenient`), and one
normalizer (`_normalize_lead`). Switching model/vendor is one env var
(`QWEN_PROVIDER` / `QWEN_MODEL`), no code change. OpenAI-compatible providers
share `_openai_compatible_extract`; Ollama uses its native `/api/chat` format.

### D3 — Single-process serving
FastAPI serves the React production build from `backend/static` (if present) via
`StaticFiles(html=True)`. One port (8000), one process — same backend serves API
+ UI in prod, while Vite dev server proxies `/api → :8000` locally.

### D4 — Batch isolation (fail-one, keep-rest)
`extract_lead()` catches all provider exceptions per card. A bad file / API error
never aborts the batch: that row returns with the verbatim error in its
`Position` field (`ERROR (<provider>): ...`) and remaining cards still process.
`errors[]` separately reports oversize / unsupported-type rejections.

### D5 — Sequential frontend uploads + server-side 429 retries
`App.jsx` sends **one card per `POST /api/extract` sequentially** and appends each
lead as soon as it arrives (progress `done/total · current-file`). This avoids
parallel rate-limit hits (notably Groq). Server side, `_openai_compatible_extract`
retries HTTP 429 with `Retry-After` + exponential backoff (2s, 4s, … capped 30s),
tunable via `PROVIDER_MAX_RETRIES` (default 4, max 10).

### D6 — Strict prompt + tolerant parsing
The system prompt demands *only* a JSON object with exactly
`first_name, last_name, position, company, location, phone, email` (empty string
for missing). The parser then tolerates real-world VLM output: ```json fences,
leading/trailing prose, `content` as string or `[{type:text}]` blocks, `null` /
non-string values, and a fallback `name → first/last` split.

### D7 — Mock-by-default
`QWEN_PROVIDER` defaults to `mock` (deterministic sample lead, no key/network) so
upload → table → Excel works offline and CI never needs secrets.

---

## 3. Libraries, frameworks, pretrained models & external components

### Backend (`backend/requirements.txt`, Python 3.11)
| Component | Version | Role |
|---|---|---|
| FastAPI | 0.115.6 | REST API + serves React build |
| uvicorn `[standard]` | 0.34.0 | ASGI server |
| python-multipart | 0.0.20 | Multipart image upload parsing |
| Pillow | 11.1.0 | Downscale → RGB JPEG data-URI for VLM |
| pandas | 2.2.3 | Leads → DataFrame |
| openpyxl | 3.1.5 | `.xlsx` (widths, autofilter, frozen header) |
| httpx | 0.28.1 | VLM API calls (90s remote / 120s Ollama timeout) |
| pydantic | 2.10.4 | `Lead` / `ExportRequest` validation |
| python-dotenv | 1.0.1 | `.env` loading |

### Frontend (`frontend/package.json`)
| Component | Version | Role |
|---|---|---|
| React + react-dom | 18.3.1 | Drag-drop upload, previews, editable leads grid, Excel download |
| Vite + @vitejs/plugin-react | 6.0.3 / 4.3.4 | Dev server (`:5173`, proxies `/api → :8000`); build emits to `backend/static` |
| Plain CSS | — | `styles.css` dark theme; no Tailwind/MUI |

### Pretrained models (served remotely, weights never downloaded)
Default provider is `mock`; set `QWEN_PROVIDER` + key for live models:

| Provider | Default model (code) | Notes |
|---|---|---|
| `huggingface` | `Qwen/Qwen2.5-VL-7B-Instruct` | Alibaba Qwen 7B multimodal Instruct, strong OCR. Code explicitly rewrites legacy `Qwen/Qwen3-VL-8B-Instruct:featherless-ai` pin back to this default |
| `openrouter` | `qwen/qwen3-vl-8b-instruct` → fallback `qwen/qwen2.5-vl-72b-instruct` | Paid credit required; no free Qwen-VL. `QWEN_MODEL` set → single model only |
| `dashscope` | `qwen-vl-max` | Official Alibaba API, `compatible-mode/v1/chat/completions` |
| `groq` | `meta-llama/llama-4-scout-17b-16e-instruct` → `qwen/qwen3.6-27b` → `...maverick...` | Fast + free tier; note defaults are Llama Scout/Maverick (vision), Qwen optional via `QWEN_MODEL` |
| `ollama` | `qwen2-vl` | Local GPU via `OLLAMA_HOST` (default `http://localhost:11434`), native `/api/chat` + `format:json` |
| `mock` | — | Deterministic demo lead for offline testing |

### External components / services
- **Hugging Face Inference Providers** (`router.huggingface.co/v1/chat/completions`, OpenAI-compatible) — needs `HF_TOKEN` + enabled provider. Optional `HF_PROVIDER` → `X-HF-Provider` header.
- **OpenRouter** (`openrouter.ai/api/v1/chat/completions`), **DashScope** (`dashscope.aliyuncs.com/compatible-mode/...`), **Groq** (`api.groq.com/openai/v1/chat/completions`) — all OpenAI-compatible.
- **AWS** — EC2 `t2.micro` (free tier), Elastic Beanstalk / App Runner / ECS + ECR for serverless; Secrets Manager / SSM recommended for keys in prod.
- **Docker** — referenced by `user-data.sh` but no Dockerfile is currently checked in.

---

## 4. Setup & configuration

Prerequisites: Python 3.11+, Node 20+.

```powershell
# 1. Backend
pip install -r backend\requirements.txt
copy .env.example .env
# Edit .env — e.g. for Groq (fast, free tier):
#   QWEN_PROVIDER=groq
#   GROQ_API_KEY=gsk-...
#   QWEN_MODEL=qwen/qwen3.6-27b   # optional
# Or Hugging Face:
#   QWEN_PROVIDER=huggingface
#   HF_TOKEN=hf-...
uvicorn main:app --app-dir backend --port 8000   # → http://localhost:8000

# 2. Frontend dev (optional — backend already serves built UI if backend/static exists)
cd frontend; npm install; npm run dev            # → http://localhost:5173

# 3. Rebuild production UI after frontend changes
cd frontend; npm run build   # outputs to backend/static (emptyOutDir: true)
```

Without any key the app runs in `mock` demo mode (clearly bannered sample data).

Key env vars (see `.env.example` for full template):

| Var | Default | Purpose |
|---|---|---|
| `QWEN_PROVIDER` | `mock` | `huggingface / openrouter / dashscope / groq / ollama / mock` (unknown → `mock`) |
| `QWEN_MODEL` | per-provider (above) | Override model slug |
| `HF_TOKEN` / `OPENROUTER_API_KEY` / `DASHSCOPE_API_KEY` / `GROQ_API_KEY` | — | Provider credentials |
| `HF_PROVIDER` / `GROQ_BASE_URL` / `DASHSCOPE_BASE_URL` / `OLLAMA_HOST` | — | Routing / self-host overrides |
| `PROVIDER_MAX_RETRIES` | `4` | 429 retries (0–10) |
| `APP_URL` | `http://localhost:8000` | `HTTP-Referer` header for OpenRouter |
| `PORT` | `8000` | Informational (uvicorn / PaaS port) |

Quick API test:

```powershell
curl http://localhost:8000/api/health
curl http://localhost:8000/api/config
curl -F "files=@card1.png" -F "files=@card2.png" http://localhost:8000/api/extract
curl -X POST http://localhost:8000/api/export-excel -H "Content-Type: application/json" -d '{"leads":[]}' --output leads.xlsx
```

### API reference
| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | `{ status, provider, configured, lead_keys }` |
| GET | `/api/config` | `{ provider, configured, model }` — drives UI status pill |
| POST | `/api/extract` | multipart `files` (≤50, ≤10 MB each) → `{ provider, leads[], errors[] }` |
| POST | `/api/export-excel` | `{ leads[] }` → `leads.xlsx` download (8 columns, widths + filter + frozen header) |
| GET | `/` | React UI when `backend/static` built; else JSON hint |

UI workflow: ① Upload (drag-drop / browse, ≤50, thumbnails + remove) → ② Extract (sequential, live progress, per-cell editing, add/delete rows) → Download Excel.

---

## 5. Deployment (AWS — public URL in ~10 min)

### Option A — EC2 `t2.micro` free tier, manual (works today, no Dockerfile needed)
1. Launch: AMI **Amazon Linux 2023**, type **`t2.micro`**. Security group: inbound **TCP 80 + 8000** from `0.0.0.0/0`.
2. SSH + install + run:
```bash
sudo dnf update -y && sudo dnf install -y python3.11 git nodejs
git clone <YOUR-REPO-URL> /opt/card-extractor && cd /opt/card-extractor
pip install -r backend/requirements.txt
# .env with QWEN_PROVIDER + key, or export env vars
nohup uvicorn main:app --app-dir backend --host 0.0.0.0 --port 8000 &
# Public URL: http://<EC2-PUBLIC-IP>:8000/
```
3. For prod: Elastic IP + ALB + ACM (HTTPS), systemd unit, keys in Secrets Manager/SSM.

### Option B — `user-data.sh` (one-boot Docker path — requires adding a Dockerfile first)
`user-data.sh` installs Docker + git, clones `$GIT_REPO` (or uses pre-`scp`'d
`/opt/card-extractor`), sources `.env`, then `docker build -t card-extractor .` +
`docker run -p 80:8000 --env-file .env`. It is currently a no-op without a
`Dockerfile` — add a multi-stage image (`node:20-slim` build → `python:3.11-slim`
runtime, `COPY backend/static`, `CMD uvicorn main:app`) to enable it, or use Option A.

Local Docker equivalent (once Dockerfile exists):
```bash
cp .env.example .env   # fill provider + key
docker compose up --build   # → http://localhost:8000 (compose file also to be added)
```

### Option C — Elastic Beanstalk (Docker) / App Runner / ECS Fargate
```bash
# Beanstalk (needs Dockerfile)
eb init -p docker card-extractor --region us-east-1
eb create card-extractor-env --instance-type t2.micro --single
eb setenv QWEN_PROVIDER=groq GROQ_API_KEY=gsk-... 
eb open
# App Runner: push image to ECR, create service on port 8000 → public URL
```

---

## 6. Known limitations / future improvements

- **Missing container assets:** no `Dockerfile` / `docker-compose.yml` despite `user-data.sh` + old docs referencing them. Docker/EB/App Runner deploys are blocked until added.
- **Doc/code drift:** prior README pinned `Qwen3-VL-8B-Instruct:featherless-ai`; current `providers.py` defaults to `Qwen2.5-VL-7B-Instruct` and rewrites that pin. HF auto-routing vs explicit provider behavior needs re-verification.
- **Groq defaults aren't Qwen** (Llama-4 Scout/Maverick) — branding says "Qwen" but default path uses Meta models unless `QWEN_MODEL` overridden.
- **No auth, open CORS (`*`), no rate-limiting** — anyone with the URL can spend your VLM quota. Harden `CORSMiddleware`, add API key/OAuth + per-IP throttle before public prod.
- **No persistence** — leads live only in browser state; refresh loses them. Add DB (Postgres/SQLite) + job history + S3 for images.
- **Throughput:** frontend uploads strictly sequentially (50 cards = 50 round trips); backend loop is also serial. Add server-side parallel fan-out with semaphore + single bulk request support in UI.
- **Error channel hack:** provider errors stuffed into `Position` string; add structured `error` / `confidence` fields and UI error styling.
- **HEIC/HEIF** depends on local Pillow build; browsers report `octet-stream` — ext fallback helps but conversion can still fail on minimal images.
- **Quality knobs:** fixed 1568px / JPEG q88 downscale may hurt dense/small-print cards; no deskew/contrast, no multi-crop, no confidence scores, no dedup, no language detection.
- **Excel minimal:** single sheet, no validation/drop-downs, no image thumbnails.
- **No tests / CI / lint** (`__inti__.py` typo, no pytest/vitest). Add unit tests for `_parse_json_lenient`, `_normalize_lead`, `image_to_data_uri`, API contract tests, frontend tests.
- **Secrets hygiene:** local `.env` holds real keys (correctly git-ignored, but rotate if ever shared); move to Secrets Manager/SSM + IAM roles.
- **No HTTPS, no observability** — add ALB+ACM, structured logging, request IDs, latency/cost metrics per provider/model.

---

## 7. **"AI Usage"**

**Which AI tools were used.**
All implementation work in this repo was done with an AI coding agent —
**Muse Spark** (Meta), operating inside **OpenCode** — driven by the repo owner
through short natural-language instructions. Web search/fetch was used by the
agent for live verification (model availability, endpoint shapes, error-code
docs). This README itself was regenerated by Muse Spark from full file reads
(`main.py`, `providers.py`, `App.jsx`, configs, git log). No other AI tools
(Copilot / ChatGPT / Claude direct use, AI image generation) were involved.

**What they were used for.**
- Greenfield scaffolding: FastAPI backend, provider abstraction, React frontend,
  EC2 user-data, requirements pinning, this README.
- Live API debugging: real requests to OpenRouter (`GET /api/v1/models`,
  error-body inspection) and Hugging Face
  (`GET /api/models/...?expand[]=inferenceProviderMapping`,
  `router.../v1/models/...`) to ground each fix in observed behavior.
- Verification: synthetic card images → real extraction → field-by-field checks →
  Excel round-trip checks.

**Significant AI-generated code / architecture adopted.**
- Remote-API-over-local-hosting (D1) and single shared-prompt provider
  abstraction with lenient parsing/normalization (D2) — proposed by agent, kept.
- Batch isolation with verbatim API errors in `Position` + separate `errors[]` —
  proposed after reading live error bodies, kept.
- 429 retry with `Retry-After` + backoff (`PROVIDER_MAX_RETRIES`) and sequential
  frontend uploads to avoid rate limits — proposed by agent, kept.

**AI recommendations rejected or modified.**
- *Streamlit UI*: agent proposed Streamlit for speed; owner overrode ("use React")
  → React + Vite built instead.
- *Default model `Qwen/Qwen2.5-VL-7B-Instruct` → `Qwen3-VL-8B-Instruct:featherless-ai` pin*:
  agent's later pin worked briefly (pinned → 200 vs auto → 400) but current code
  reverts to `Qwen2.5-VL-7B` and rewrites the featherless pin — retained as a
  drift item to re-verify rather than blindly keeping the pin.
- *OpenRouter `:free` slugs*: agent initially assumed free Qwen-VL variants
  existed; **corrected after live `/models` listing proved none exist** — dead
  slugs removed, docs updated to state paid credit is required.
- *Temporary tunnel URL for "public deployment"*: considered, **rejected** —
  fragile; shipped one-command EC2/manual deploy assets + local verified URL instead.
