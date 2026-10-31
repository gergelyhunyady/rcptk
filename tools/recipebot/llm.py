"""Minimal Gemini REST client (stdlib only, so the GitHub Action needs no pip install)."""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_INLINE_BYTES = 15_000_000  # the API limits a whole request to ~20 MB


class LLMError(RuntimeError):
    pass


def text_part(text: str) -> dict:
    return {"text": text}


def image_part(data: bytes, mime_type: str) -> dict:
    return {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode("ascii")}}


def _call(body: dict, *, model: str, api_key: str, timeout: int, retries: int, opener, sleep) -> dict:
    req = urllib.request.Request(
        API_URL.format(model=model),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    last = ""
    for attempt in range(retries):
        try:
            with opener(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}"
            if e.code not in RETRY_STATUS:
                raise LLMError(last) from None
        except (urllib.error.URLError, TimeoutError) as e:
            last = f"network error: {e}"
        if attempt + 1 < retries:
            sleep(2 ** (attempt + 1))
    raise LLMError(f"Gemini hívás sikertelen ({retries} próbálkozás): {last}")


def _answer_text(resp: dict) -> str:
    block = (resp.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise LLMError(f"A kérést a modell blokkolta: {block}")
    candidates = resp.get("candidates") or []
    if not candidates:
        raise LLMError("A modell nem adott választ.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise LLMError(f"Üres válasz (finishReason={candidates[0].get('finishReason')}).")
    return text


def generate(
    parts: list[dict],
    *,
    model: str,
    api_key: str,
    system: str | None = None,
    schema: dict | None = None,
    tools: list[dict] | None = None,
    timeout: int = 120,
    retries: int = 3,
    opener=urllib.request.urlopen,
    sleep=time.sleep,
) -> str | dict:
    """Run one generateContent call. With `schema` the reply is parsed JSON, otherwise text."""
    size = sum(len(p["inline_data"]["data"]) * 3 // 4 for p in parts if "inline_data" in p)
    if size > MAX_INLINE_BYTES:
        raise LLMError("A képek együttes mérete túl nagy.")
    body: dict = {"contents": [{"role": "user", "parts": parts}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if schema:
        body["generationConfig"] = {"responseMimeType": "application/json", "responseSchema": schema}
    if tools:
        body["tools"] = tools
    text = _answer_text(_call(body, model=model, api_key=api_key, timeout=timeout, retries=retries, opener=opener, sleep=sleep))
    if not schema:
        return text
    try:
        return json.loads(text)
    except ValueError as e:
        raise LLMError(f"A modell válasza nem érvényes JSON: {e}") from None

