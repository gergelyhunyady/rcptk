#!/usr/bin/env python3
"""GitHub Actions runner for recipebot.

Dispatched either via:
1. repository_dispatch (from the Cloudflare Worker relay for Telegram)
2. workflow_dispatch (manual trigger from GitHub UI / mobile app)
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from recipebot import pipeline, telegram  # noqa: E402
from recipebot.model import CATEGORIES, Recipe, RecipeError  # noqa: E402
from recipebot.sources import check_url, parse_message  # noqa: E402

PAGES_BASE_URL = "https://gergelyhunyady.github.io/rcptk/"


def git_commit_and_push(files: list[str], commit_message: str, root: Path) -> None:
    subprocess.run(["git", "config", "user.name", "rcptk-bot"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
        cwd=root,
        check=True,
    )
    for f in files:
        subprocess.run(["git", "add", f], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", commit_message], cwd=root, check=True)
    subprocess.run(["git", "push"], cwd=root, check=True)


def git_undo(root: Path) -> tuple[bool, str]:
    """Reverts the last commit if it was made by the bot."""
    res = subprocess.run(
        ["git", "log", "-1", "--pretty=format:%s"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    last_msg = res.stdout.strip()
    if not (last_msg.endswith("(bot)") or "[bot]" in last_msg.lower()):
        return False, f"Az utolsó commit nem a bot által készült (<i>{html.escape(last_msg)}</i>)."

    subprocess.run(["git", "config", "user.name", "rcptk-bot"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "revert", "--no-edit", "HEAD"], cwd=root, check=True)
    subprocess.run(["git", "push"], cwd=root, check=True)
    return True, f"Visszavonva: <b>{html.escape(last_msg)}</b>"


def format_success_msg(recipe: Recipe, page_url: str) -> str:
    cat_label = CATEGORIES.get(recipe.category, recipe.category)
    msg = [
        "✅ <b>Recept hozzáadva!</b>",
        "",
        f"🍴 <b>{html.escape(recipe.title)}</b> ({html.escape(cat_label)})",
        f'🔗 <a href="{html.escape(page_url, quote=True)}">{html.escape(page_url)}</a>',
    ]
    if recipe.warnings:
        msg.append("")
        msg.append("⚠️ <i>Megjegyzések a feldolgozásból:</i>")
        for w in recipe.warnings:
            msg.append(f"• {html.escape(w)}")
    return "\n".join(msg)


def format_duplicate_msg(existing_path: str) -> str:
    page_url = PAGES_BASE_URL + existing_path
    return "\n".join([
        "⚠️ <b>Ez a recept már létezik:</b>",
        f'🔗 <a href="{html.escape(page_url, quote=True)}">{html.escape(existing_path)}</a>',
    ])


def format_error_msg(err: str) -> str:
    return f"❌ <b>Hiba történt a recept feldolgozása során:</b>\n{html.escape(err)}"


def run_pipeline(
    raw_message: str,
    category_override: str | None,
    image_paths: list[Path],
    dry_run: bool,
    root: Path,
) -> tuple[Recipe, str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RecipeError("Hiányzik a GEMINI_API_KEY titkos kulcs a tárolóból (Secrets).")

    model = os.environ.get("GEMINI_MODEL", pipeline.DEFAULT_MODEL)
    msg = parse_message(raw_message)
    if category_override and category_override != "auto":
        msg.category = category_override

    urls = list(dict.fromkeys(msg.urls))
    for u in urls:
        check_url(u)

    images = [pipeline.read_image(p) for p in image_paths]
    recipe, _ = pipeline.extract(
        pipeline.Inputs(msg, images, urls), model=model, api_key=api_key
    )

    rel_path = pipeline.publish(recipe, root, dry_run=dry_run)
    if not dry_run:
        commit_msg = f"Add {recipe.title} recipe (bot)"
        git_commit_and_push([rel_path, "index.html"], commit_msg, root)

    return recipe, rel_path


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")

    event = {}
    if event_path and Path(event_path).exists():
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Warning: could not parse GITHUB_EVENT_PATH: {e}", file=sys.stderr)

    chat_id = None
    reply_to_id = None
    raw_text = ""
    category = None
    dry_run = False
    photo_file_ids = []

    if event_name == "repository_dispatch":
        payload = event.get("client_payload") or {}
        chat_id = payload.get("chat_id")
        reply_to_id = payload.get("message_id")
        raw_text = payload.get("text") or payload.get("caption") or ""
        category = payload.get("category")
        photo_file_ids = payload.get("photo_file_ids") or []
        dry_run = bool(payload.get("dry_run", False))
    elif event_name == "workflow_dispatch":
        inputs = event.get("inputs") or {}
        raw_text = inputs.get("message") or ""
        category = inputs.get("category")
        dry_run = str(inputs.get("dry_run", "false")).lower() in ("true", "1")
    else:
        # Local or CLI invocation
        raw_text = os.environ.get("BOT_MESSAGE", " ".join(sys.argv[1:]))

    def reply_tg(html_msg: str):
        if bot_token and chat_id:
            try:
                telegram.send_message(bot_token, chat_id, html_msg, reply_to_message_id=reply_to_id)
            except Exception as e:
                print(f"Failed to send Telegram reply: {e}", file=sys.stderr)

    # Check for /undo command
    if raw_text.strip().lower() in ("/undo", "undo"):
        ok, msg = git_undo(REPO_ROOT)
        reply_tg(f"{'↩️' if ok else '⚠️'} {msg}")
        print(msg)
        return 0 if ok else 1

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        image_paths = []
        if photo_file_ids and bot_token:
            for fid in photo_file_ids:
                try:
                    image_paths.append(telegram.download_file(bot_token, fid, tmp_path))
                except Exception as e:
                    err_msg = f"Nem sikerült letölteni a fotót a Telegramról: {e}"
                    reply_tg(format_error_msg(err_msg))
                    print(err_msg, file=sys.stderr)
                    return 1

        try:
            recipe, rel_path = run_pipeline(
                raw_message=raw_text,
                category_override=category,
                image_paths=image_paths,
                dry_run=dry_run,
                root=REPO_ROOT,
            )
            page_url = PAGES_BASE_URL + rel_path
            reply_tg(format_success_msg(recipe, page_url))
            print(f"Success: {recipe.title} -> {rel_path}")
            return 0
        except pipeline.DuplicateError as e:
            reply_tg(format_duplicate_msg(e.existing))
            print(f"Duplicate: {e.existing}")
            return 0  # not a runner crash, handled gracefully
        except RecipeError as e:
            reply_tg(format_error_msg(str(e)))
            print(f"Recipe error: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            reply_tg(format_error_msg(f"Váratlan hiba: {e}"))
            print(f"Unexpected error: {e}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())

