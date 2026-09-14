"""
FastAPI backend: bulk business-card upload -> Qwen VLM extraction -> Excel export.
Also serves the React production build (backend/static) when present.
"""

import io
import os

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from .providers import LEAD_KEYS, active_provider, extract_lead, provider_configured

MAX_FILES = 50
MAX_FILE_MB = 10
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}

EXCEL_COLUMNS = [
    "First Name",
    "Last Name",
    "Position / Job Title",
    "Company",
    "Location",
    "Phone Number",
    "Email Address",
    "Source File",
]

app = FastAPI(title="Business Card Lead Extractor (Qwen VLM)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Lead(BaseModel):
    first_name: str = ""
    last_name: str = ""
    position: str = ""
    company: str = ""
    location: str = ""
    phone: str = ""
    email: str = ""
    source_file: str = ""


class ExportRequest(BaseModel):
    leads: list[Lead]


def leads_to_dataframe(leads: list[dict]) -> pd.DataFrame:
    rows = [
        {
            "First Name": l.get("first_name", ""),
            "Last Name": l.get("last_name", ""),
            "Position / Job Title": l.get("position", ""),
            "Company": l.get("company", ""),
            "Location": l.get("location", ""),
            "Phone Number": l.get("phone", ""),
            "Email Address": l.get("email", ""),
            "Source File": l.get("source_file", ""),
        }
        for l in leads
    ]
    return pd.DataFrame(rows, columns=EXCEL_COLUMNS)


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Leads")
        ws = writer.sheets["Leads"]
        widths = [14, 14, 26, 22, 22, 18, 26, 20]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
        ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = "A2"
    buf.seek(0)
    return buf.read()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "provider": active_provider(),
        "configured": provider_configured(),
        "lead_keys": LEAD_KEYS,
    }


@app.get("/api/config")
def config():
    provider = active_provider()
    models = {
        "dashscope": "qwen-vl-max (default, override with QWEN_MODEL)",
        "openrouter": "qwen3-vl-8b-instruct (needs a few $ credit, ~$0.0002/card; override with QWEN_MODEL)",
        "huggingface": "Qwen3-VL-8B-Instruct pinned to featherless-ai (override with QWEN_MODEL)",
        "ollama": "qwen2-vl (default, override with QWEN_MODEL)",
        "mock": "offline demo data (no key needed)",
    }
    return {"provider": provider, "configured": provider_configured(), "model": models.get(provider, "")}


@app.post("/api/extract")
async def extract(files: list[UploadFile] = File(...)):
    if not files:
        return JSONResponse({"error": "No files uploaded"}, status_code=400)
    if len(files) > MAX_FILES:
        return JSONResponse({"error": f"Too many files (max {MAX_FILES})"}, status_code=400)

    leads: list[dict] = []
    errors: list[dict] = []
    for f in files:
        content = await f.read()
        if len(content) > MAX_FILE_MB * 1024 * 1024:
            errors.append({"file": f.filename, "error": f"File too large (max {MAX_FILE_MB} MB)"})
            continue
        if f.content_type not in ALLOWED_TYPES:
            # Still attempt — browsers sometimes send octet-stream for HEIC.
            ext = (f.filename or "").lower().rsplit(".", 1)[-1]
            if ext not in {"jpg", "jpeg", "png", "webp", "heic", "heif"}:
                errors.append({"file": f.filename, "error": f"Unsupported type: {f.content_type}"})
                continue
        lead = extract_lead(content, filename=f.filename or "upload")
        leads.append(lead)

    return {"provider": active_provider(), "leads": leads, "errors": errors}


@app.post("/api/export-excel")
def export_excel(req: ExportRequest):
    df = leads_to_dataframe([l.model_dump() for l in req.leads])
    data = dataframe_to_excel_bytes(df)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=leads.xlsx"},
    )


# ---- Serve React build (if present) ----
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


@app.get("/")
def root():
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {
        "app": "Business Card Lead Extractor (Qwen VLM)",
        "message": "API is running. Build the React frontend into backend/static to serve the UI from here.",
        "endpoints": ["GET /api/health", "POST /api/extract", "POST /api/export-excel"],
    }
