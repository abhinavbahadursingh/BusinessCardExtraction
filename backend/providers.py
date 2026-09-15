"""
Qwen VLM provider abstraction.

Business-card image -> Qwen Vision-Language Model -> normalized lead dict.

Supported providers (selected via QWEN_PROVIDER):

  - dashscope
      Official Alibaba DashScope API.
      Env:
        DASHSCOPE_API_KEY
        QWEN_MODEL (optional, default: qwen-vl-max)
      Endpoint:
        https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions

  - openrouter
      OpenRouter API.
      Env:
        OPENROUTER_API_KEY
        QWEN_MODEL (optional)
      Endpoint:
        https://openrouter.ai/api/v1/chat/completions

  - huggingface
      Hugging Face Inference Providers.
      Env:
        HF_TOKEN
        QWEN_MODEL (optional)
        HF_PROVIDER (optional)
      Endpoint:
        https://router.huggingface.co/v1/chat/completions

  - groq
      Groq API (OpenAI-compatible, fast inference).
      Env:
        GROQ_API_KEY
        QWEN_MODEL (optional, default: meta-llama/llama-4-scout-17b-16e-instruct;
          override with e.g. qwen/qwen3.6-27b for a Qwen vision model)
        GROQ_BASE_URL (optional)
      Endpoint:
        https://api.groq.com/openai/v1/chat/completions

  - ollama
      Local Ollama server.
      Env:
        OLLAMA_HOST (optional, default: http://localhost:11434)
        QWEN_MODEL (optional, default: qwen2-vl)

  - mock
      No network/API key.
      Useful for testing the frontend and Excel export.

All remote providers use an OpenAI-compatible chat-completions request
containing a base64 image data URI.
"""

import base64
import io
import json
import os
import re
from typing import Any

import httpx
from PIL import Image


# ---------------------------------------------------------------------------
# Canonical lead schema
# ---------------------------------------------------------------------------

LEAD_KEYS = [
    "first_name",
    "last_name",
    "position",
    "company",
    "location",
    "phone",
    "email",
]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a business card information extraction system. "
    "Read the provided business card image carefully. "
    "Return ONLY one valid JSON object with exactly these keys: "
    "first_name, last_name, position, company, location, phone, email. "
    "Do not return markdown. "
    "Do not return code fences. "
    "Do not explain your answer. "
    "Do not add any other keys. "
    'Use an empty string "" for information that is missing or unreadable. '
    "Split the person's name into first_name and last_name. "
    "Use the person's actual job title for position. "
    "Use the organization name for company. "
    "Use the address, city, state, or country shown on the card for location. "
    "Use the primary business phone number for phone. "
    "Use the primary email address for email."
)

USER_PROMPT = (
    "Extract the contact information from this business card image. "
    "Return only the requested JSON object."
)


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def _blank_lead(source_file: str = "") -> dict:
    """Return an empty lead using the canonical schema."""
    return {
        "first_name": "",
        "last_name": "",
        "position": "",
        "company": "",
        "location": "",
        "phone": "",
        "email": "",
        "source_file": source_file,
    }


def _normalize_lead(data: Any, source_file: str = "") -> dict:
    """
    Normalize model output into the canonical lead schema.

    The model may occasionally return:
      - null values
      - non-string values
      - a `name` field instead of split first/last name

    This function makes the result predictable for the FastAPI/Excel layer.
    """
    lead = _blank_lead(source_file)

    if not isinstance(data, dict):
        return lead

    for key in LEAD_KEYS:
        value = data.get(key, "")

        if value is None:
            value = ""

        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)

        lead[key] = str(value).strip()

    # Handle models that return `name` instead of first_name/last_name.
    if (
        not lead["first_name"]
        and not lead["last_name"]
        and isinstance(data.get("name"), str)
    ):
        parts = data["name"].strip().split()

        if parts:
            lead["first_name"] = parts[0]

            if len(parts) > 1:
                lead["last_name"] = " ".join(parts[1:])

    lead["source_file"] = source_file

    return lead


def _parse_json_lenient(text: str) -> dict:
    """
    Parse JSON returned by a VLM.

    Handles:
      - normal JSON
      - ```json ... ``` fences
      - extra text before/after a JSON object
    """
    if not text:
        return {}

    text = str(text).strip()

    # Remove markdown code fences.
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    # First try the entire response.
    try:
        obj = json.loads(text)

        if isinstance(obj, dict):
            return obj

    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Then find the first JSON object.
    try:
        start = text.index("{")
        end = text.rindex("}") + 1

        obj = json.loads(text[start:end])

        if isinstance(obj, dict):
            return obj

    except (ValueError, json.JSONDecodeError, TypeError):
        pass

    return {}


# ---------------------------------------------------------------------------
# Image handling
# ---------------------------------------------------------------------------

def image_to_data_uri(
    image_bytes: bytes,
    max_side: int = 1568,
) -> str:
    """
    Convert an uploaded image to a JPEG base64 data URI.

    Downscaling keeps request size reasonable while retaining enough
    resolution for business-card OCR/VLM extraction.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise RuntimeError(
            f"Unable to read uploaded image: {exc}"
        ) from exc

    # Handle images with transparency.
    img = img.convert("RGB")

    width, height = img.size

    if width <= 0 or height <= 0:
        raise RuntimeError("Uploaded image has invalid dimensions")

    scale = min(1.0, max_side / max(width, height))

    if scale < 1.0:
        new_size = (
            max(1, int(width * scale)),
            max(1, int(height * scale)),
        )

        img = img.resize(
            new_size,
            Image.Resampling.LANCZOS,
        )

    output = io.BytesIO()

    img.save(
        output,
        format="JPEG",
        quality=88,
        optimize=True,
    )

    encoded = base64.b64encode(
        output.getvalue()
    ).decode("utf-8")

    return f"data:image/jpeg;base64,{encoded}"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _format_http_error(
    response: httpx.Response,
    provider: str,
    model: str,
) -> str:
    """
    Produce a useful provider error instead of a generic HTTP error.
    """
    status = response.status_code

    try:
        body = response.json()

        if isinstance(body, dict):
            error = body.get("error")

            if isinstance(error, dict):
                message = error.get("message")
                code = error.get("code")
                error_type = error.get("type")

                details = []

                if message:
                    details.append(str(message))

                if error_type:
                    details.append(f"type={error_type}")

                if code:
                    details.append(f"code={code}")

                if details:
                    return (
                        f"{provider} returned HTTP {status} "
                        f"for model {model!r}: "
                        + " | ".join(details)
                    )

            if body.get("message"):
                return (
                    f"{provider} returned HTTP {status} "
                    f"for model {model!r}: "
                    f"{body['message']}"
                )

    except Exception:
        pass

    text = response.text.strip()

    if len(text) > 500:
        text = text[:500] + "..."

    return (
        f"{provider} returned HTTP {status} "
        f"for model {model!r}: {text}"
    )


def _openai_compatible_extract(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    data_uri: str,
    extra_headers: dict | None = None,
    timeout: float = 90.0,
) -> dict:
    """
    Call an OpenAI-compatible vision/chat endpoint.
    """
    if not api_key:
        raise RuntimeError(
            f"{provider} API key/token is not configured"
        )

    if not model:
        raise RuntimeError(
            f"{provider} model is empty"
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if extra_headers:
        headers.update(extra_headers)

    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 512,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": USER_PROMPT,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_uri,
                        },
                    },
                ],
            },
        ],
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                base_url,
                headers=headers,
                json=payload,
            )

    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"{provider} request timed out after {timeout:.0f}s"
        ) from exc

    except httpx.RequestError as exc:
        raise RuntimeError(
            f"{provider} network error: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise RuntimeError(
            _format_http_error(
                response,
                provider,
                model,
            )
        )

    try:
        body = response.json()

    except ValueError as exc:
        raise RuntimeError(
            f"{provider} returned invalid JSON"
        ) from exc

    # OpenAI-compatible response.
    try:
        message = body["choices"][0]["message"]
        content = message.get("content", "")
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"{provider} returned an unexpected response format"
        ) from exc

    # Some providers return:
    #
    # [
    #   {"type": "text", "text": "..."}
    # ]
    #
    if isinstance(content, list):
        pieces = []

        for block in content:
            if isinstance(block, dict):
                text = block.get("text")

                if text:
                    pieces.append(str(text))

        content = "".join(pieces)

    parsed = _parse_json_lenient(str(content))

    if not parsed:
        raise RuntimeError(
            f"{provider} returned no valid JSON. "
            f"Raw response: {str(content)[:300]}"
        )

    return parsed


# ---------------------------------------------------------------------------
# DashScope
# ---------------------------------------------------------------------------

def extract_via_dashscope(
    image_bytes: bytes,
) -> dict:
    """
    Extract using Alibaba DashScope.
    """
    api_key = os.getenv(
        "DASHSCOPE_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY is not set"
        )

    model = os.getenv(
        "QWEN_MODEL",
        "qwen-vl-max",
    ).strip()

    base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    ).strip()

    return _openai_compatible_extract(
        provider="DashScope",
        base_url=base_url,
        api_key=api_key,
        model=model,
        data_uri=image_to_data_uri(image_bytes),
    )


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

OPENROUTER_DEFAULT_MODELS = [
    "qwen/qwen3-vl-8b-instruct",
    "qwen/qwen2.5-vl-72b-instruct",
]


def extract_via_openrouter(
    image_bytes: bytes,
) -> dict:
    """
    Extract using OpenRouter.

    If QWEN_MODEL is not specified, try the configured default models
    sequentially. If QWEN_MODEL is specified, only that model is used.
    """
    api_key = os.getenv(
        "OPENROUTER_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set"
        )

    configured_model = os.getenv(
        "QWEN_MODEL",
        "",
    ).strip()

    if configured_model:
        models = [configured_model]
    else:
        models = OPENROUTER_DEFAULT_MODELS

    data_uri = image_to_data_uri(image_bytes)

    last_error: Exception | None = None

    for model in models:
        try:
            return _openai_compatible_extract(
                provider="OpenRouter",
                base_url=(
                    "https://openrouter.ai/api/v1/chat/completions"
                ),
                api_key=api_key,
                model=model,
                data_uri=data_uri,
                extra_headers={
                    "HTTP-Referer": os.getenv(
                        "APP_URL",
                        "http://localhost:8000",
                    ),
                    "X-Title": "BusinessCardExtraction",
                },
            )

        except Exception as exc:
            last_error = exc

            # If the user explicitly configured a model, do not silently
            # switch to another model.
            if configured_model:
                break

    raise RuntimeError(
        "OpenRouter extraction failed. "
        f"Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# Hugging Face
# ---------------------------------------------------------------------------

def extract_via_huggingface(
    image_bytes: bytes,
) -> dict:
    """
    Extract using Hugging Face Inference Providers.

    IMPORTANT:
    Hugging Face routing only works when the selected model is supported
    by a provider enabled for the user's account.

    Environment variables:

        HF_TOKEN
        QWEN_MODEL
        HF_PROVIDER

    Example:

        QWEN_PROVIDER=huggingface
        HF_TOKEN=hf_xxxxxxxxx
        QWEN_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
        HF_PROVIDER=featherless-ai

    If HF_PROVIDER is omitted, Hugging Face may automatically route the
    request when supported.
    """
    token = os.getenv(
        "HF_TOKEN",
        "",
    ).strip()

    if not token:
        raise RuntimeError(
            "HF_TOKEN is not set"
        )

    model = os.getenv(
        "QWEN_MODEL",
        "Qwen/Qwen2.5-VL-7B-Instruct",
    ).strip()

    # Do not force the unavailable Featherless Qwen3-VL model.
    if model.lower() == "qwen/qwen3-vl-8b-instruct:featherless-ai":
        model = "Qwen/Qwen2.5-VL-7B-Instruct"

    provider = os.getenv("HF_PROVIDER", "").strip()

    headers = {}
    if provider:
        headers["X-HF-Provider"] = provider

    try:
        return _openai_compatible_extract(
            provider="Hugging Face",
            base_url=(
                "https://router.huggingface.co/v1/chat/completions"
            ),
            api_key=token,
            model=model,
            data_uri=image_to_data_uri(image_bytes),
            extra_headers=headers,
        )

    except RuntimeError as exc:
        raise RuntimeError(
            "Hugging Face model/provider is unavailable. "
            f"model={model!r}, "
            f"provider={provider or 'auto'!r}. "
            "Check that the model is currently available through "
            "an Inference Provider enabled on your Hugging Face account. "
            f"Original error: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------

GROQ_DEFAULT_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3.6-27b",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
]

GROQ_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"


def extract_via_groq(
    image_bytes: bytes,
) -> dict:
    """
    Extract using the Groq API.

    Groq exposes an OpenAI-compatible chat-completions endpoint that
    supports vision models (text + image_url input). If QWEN_MODEL is not
    specified, the configured default vision models are tried sequentially.
    If QWEN_MODEL is specified, only that model is used.

    Environment variables:

        GROQ_API_KEY  (required, starts with gsk_...)
        QWEN_MODEL    (optional, e.g. qwen/qwen3.6-27b,
                      meta-llama/llama-4-scout-17b-16e-instruct)
        GROQ_BASE_URL (optional, defaults to the Groq OpenAI-compatible
                      chat-completions endpoint)

    Example:

        QWEN_PROVIDER=groq
        GROQ_API_KEY=gsk-...
        QWEN_MODEL=qwen/qwen3.6-27b

    Get a key at https://console.groq.com/keys. Vision-capable models
    include meta-llama/llama-4-scout-17b-16e-instruct (production),
    meta-llama/llama-4-maverick-17b-128e-instruct, and qwen/qwen3.6-27b.
    """
    api_key = os.getenv(
        "GROQ_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set"
        )

    configured_model = os.getenv(
        "QWEN_MODEL",
        "",
    ).strip()

    if configured_model:
        models = [configured_model]
    else:
        models = GROQ_DEFAULT_MODELS

    base_url = os.getenv(
        "GROQ_BASE_URL",
        GROQ_DEFAULT_BASE_URL,
    ).strip()

    data_uri = image_to_data_uri(image_bytes)

    last_error: Exception | None = None

    for model in models:
        try:
            return _openai_compatible_extract(
                provider="Groq",
                base_url=base_url,
                api_key=api_key,
                model=model,
                data_uri=data_uri,
            )

        except Exception as exc:
            last_error = exc

            # If the user explicitly configured a model, do not silently
            # switch to another model.
            if configured_model:
                break

    raise RuntimeError(
        "Groq extraction failed. "
        f"Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def extract_via_ollama(
    image_bytes: bytes,
) -> dict:
    """
    Extract using a local Ollama server.

    Default:
        http://localhost:11434
        qwen2-vl
    """
    host = os.getenv(
        "OLLAMA_HOST",
        "http://localhost:11434",
    ).strip().rstrip("/")

    model = os.getenv(
        "QWEN_MODEL",
        "qwen2-vl",
    ).strip()

    data_uri = image_to_data_uri(image_bytes)

    # Ollama expects raw base64 rather than a data URI.
    image_base64 = data_uri.split(",", 1)[1]

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": USER_PROMPT,
                "images": [image_base64],
            },
        ],
    }

    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                f"{host}/api/chat",
                json=payload,
            )

    except httpx.TimeoutException as exc:
        raise RuntimeError(
            "Ollama request timed out after 120 seconds"
        ) from exc

    except httpx.RequestError as exc:
        raise RuntimeError(
            f"Could not connect to Ollama at {host}: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise RuntimeError(
            _format_http_error(
                response,
                "Ollama",
                model,
            )
        )

    try:
        body = response.json()

    except ValueError as exc:
        raise RuntimeError(
            "Ollama returned invalid JSON"
        ) from exc

    content = (
        body
        .get("message", {})
        .get("content", "")
    )

    parsed = _parse_json_lenient(content)

    if not parsed:
        raise RuntimeError(
            "Ollama returned no valid lead JSON"
        )

    return parsed


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

def extract_via_mock(
    image_bytes: bytes,
    filename: str = "",
) -> dict:
    """
    Offline demo provider.

    This is intentionally deterministic so the frontend, bulk upload,
    API and Excel export can be tested without an API key.
    """
    return {
        "first_name": "Demo",
        "last_name": "Contact",
        "position": "Sample Title",
        "company": "Example Corp",
        "location": "Sample City",
        "phone": "+1-555-0100",
        "email": "demo@example.com",
    }


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS = {
    "dashscope": extract_via_dashscope,
    "openrouter": extract_via_openrouter,
    "huggingface": extract_via_huggingface,
    "groq": extract_via_groq,
    "ollama": extract_via_ollama,
    "mock": extract_via_mock,
}


# ---------------------------------------------------------------------------
# Provider selection/configuration
# ---------------------------------------------------------------------------

def active_provider() -> str:
    """
    Return the currently selected provider.

    Defaults to mock so the application can start without an API key.
    """
    name = os.getenv(
        "QWEN_PROVIDER",
        "mock",
    ).strip().lower()

    if name in PROVIDERS:
        return name

    return "mock"


def provider_configured() -> bool:
    """
    Return whether the active provider has the required configuration.
    """
    name = active_provider()

    if name == "mock":
        return True

    if name == "dashscope":
        return bool(
            os.getenv("DASHSCOPE_API_KEY", "").strip()
        )

    if name == "openrouter":
        return bool(
            os.getenv("OPENROUTER_API_KEY", "").strip()
        )

    if name == "huggingface":
        return bool(
            os.getenv("HF_TOKEN", "").strip()
        )

    if name == "groq":
        return bool(
            os.getenv("GROQ_API_KEY", "").strip()
        )

    if name == "ollama":
        # Connection is tested when a card is actually processed.
        return True

    return False


# ---------------------------------------------------------------------------
# Main extraction interface
# ---------------------------------------------------------------------------

def extract_lead(
    image_bytes: bytes,
    filename: str = "",
) -> dict:
    """
    Run the active provider and return a normalized lead.

    This function intentionally catches provider errors so that one bad
    business card does not abort an entire bulk upload.

    On error:
        position = "ERROR (<provider>): <message>"

    The existing FastAPI application can therefore continue processing
    subsequent cards.
    """
    provider_name = active_provider()

    provider_function = PROVIDERS[provider_name]

    try:
        if provider_name == "mock":
            raw = provider_function(
                image_bytes,
                filename,
            )
        else:
            raw = provider_function(
                image_bytes,
            )

        return _normalize_lead(
            raw,
            source_file=filename,
        )

    except Exception as exc:
        lead = _blank_lead(filename)

        # Keep the existing FastAPI contract.
        lead["position"] = (
            f"ERROR ({provider_name}): {exc}"
        )

        return lead
