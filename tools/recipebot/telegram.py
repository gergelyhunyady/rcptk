"""Telegram Bot API helpers (pure standard library)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path


class TelegramError(RuntimeError):
    pass


def download_file(bot_token: str, file_id: str, dest_dir: Path, *, opener=urllib.request.urlopen) -> Path:
    """Download a file from Telegram using getFile and return its local path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    get_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
    try:
        with opener(urllib.request.Request(get_url), timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise TelegramError(f"Telegram getFile hiba: {e}") from e

    if not data.get("ok"):
        raise TelegramError(f"Telegram getFile sikertelen: {data}")

    file_path = data["result"]["file_path"]
    ext = Path(file_path).suffix or ".jpg"
    target = dest_dir / f"{file_id}{ext}"

    download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    try:
        with opener(urllib.request.Request(download_url), timeout=45) as resp:
            target.write_bytes(resp.read())
    except Exception as e:
        raise TelegramError(f"Telegram fájl letöltés hiba: {e}") from e

    return target


def send_message(
    bot_token: str,
    chat_id: int | str,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    parse_mode: str = "HTML",
    opener=urllib.request.urlopen,
) -> dict:
    """Send an HTML message back to the Telegram chat."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise TelegramError(f"Telegram sendMessage hiba: {e}") from e

