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
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from recipebot import edit, pipeline, telegram  # noqa: E402
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


def parse_edit_command(text: str) -> tuple[str, str] | None:
    """'/edit csokis muffin: 180 fok' -> ('csokis muffin', '180 fok'); None if not an /edit message."""
    m = re.match(r"^/edit(?:@\w+)?\b[ \t]*(.*)$", text.strip(), re.S | re.I)
    if not m:
        return None
    rest = m.group(1).strip()
    name, _, instruction = re.split(r"([:\n])", rest, maxsplit=1) if re.search(r"[:\n]", rest) else (rest, "", "")
    return name.strip(), instruction.strip()


def format_edit_success_msg(recipe: Recipe, page_url: str, changes: list[str], warnings: list[str]) -> str:
    msg = [
        "✏️ <b>Recept módosítva!</b>",
        "",
        f"🍴 <b>{html.escape(recipe.title)}</b>",
        f'🔗 <a href="{html.escape(page_url, quote=True)}">{html.escape(page_url)}</a>',
        "",
        "<b>Változások:</b>",
    ]
    budget = 2800  # Telegram caps messages at 4096 chars; count the *escaped* text
    shown = 0
    for line in changes:
        text = line if len(line) <= 220 else line[:217] + "…"
        icon = "➖" if text.startswith("- ") else "➕"
        out = f"{icon} {html.escape(text[2:])}"
        budget -= len(out)
        if budget < 0:
            msg.append(f"<i>… és még {len(changes) - shown} sor</i>")
            break
        msg.append(out)
        shown += 1
    if warnings:
        msg += ["", "⚠️ <i>Megjegyzések:</i>"] + [f"• {html.escape(w)}" for w in warnings]
    msg += ["", "↩️ Visszavonás: /undo"]
    return "\n".join(msg)


def format_nochange_msg(title: str, warnings: list[str]) -> str:
    msg = [f"ℹ️ <b>{html.escape(title)}</b>: nem változott semmi, ezért nem mentettem."]
    msg += [f"• {html.escape(w)}" for w in warnings]
    return "\n".join(msg)


def format_ambiguous_msg(candidates: list[tuple[str, str]]) -> str:
    lines = ["🤔 <b>Több recept is egyezik, melyikre gondoltál?</b>"]
    lines += [f"• {html.escape(t)}" for _, t in candidates]
    lines.append("Add meg pontosabban: <code>/edit pontos név: módosítás</code>")
    return "\n".join(lines)


EDIT_USAGE = "Használat: <code>/edit recept neve: mit módosítsak</code>\nvagy válaszolj a bot ✅ üzenetére a módosítással."


def run_edit(
    target: str,
    instruction: str,
    image_paths: list[Path],
    dry_run: bool,
    root: Path,
    *,
    by_name: bool,
) -> tuple[Recipe, str, list[str], list[str]]:
    """Returns (recipe, rel_path, changes, warnings). `changes` is empty if nothing changed."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RecipeError("Hiányzik a GEMINI_API_KEY titkos kulcs a tárolóból (Secrets).")
    model = os.environ.get("GEMINI_MODEL", pipeline.DEFAULT_MODEL)

    if by_name:
        index_html = (root / "index.html").read_text(encoding="utf-8")
        rel_path, _ = edit.find_by_name(index_html, root, target)
    else:
        rel_path = target

    old = edit.load_recipe(root, rel_path)
    images = [pipeline.read_image(p) for p in image_paths]
    new = edit.apply_edit(old, instruction, images, model=model, api_key=api_key)
    changes = edit.diff_recipes(old, new)
    if changes and not dry_run:
        edit.save(new, root, rel_path)
        git_commit_and_push([rel_path], f"Edit {new.title} recipe (bot)", root)
    return new, rel_path, changes, new.warnings


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
    reply_to_urls: list[str] = []

    if event_name == "repository_dispatch":
        payload = event.get("client_payload") or {}
        chat_id = payload.get("chat_id")
        reply_to_id = payload.get("message_id")
        raw_text = payload.get("text") or payload.get("caption") or ""
        category = payload.get("category")
        photo_file_ids = payload.get("photo_file_ids") or []
        reply_to_urls = payload.get("reply_to_urls") or []
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
            edit_cmd = parse_edit_command(raw_text)
            reply_target = next(
                (p for u in reply_to_urls if (p := edit.path_from_url(u, PAGES_BASE_URL, REPO_ROOT))), None
            )
            if edit_cmd is not None or reply_target:
                if edit_cmd is not None:
                    name, instruction = edit_cmd
                    if not name or not instruction:
                        reply_tg(f"ℹ️ {EDIT_USAGE}")
                        return 0  # user error, already answered in chat
                    target, by_name = name, True
                else:
                    target, instruction, by_name = reply_target, raw_text, False
                recipe, rel_path, changes, warnings = run_edit(
                    target, instruction, image_paths, dry_run, REPO_ROOT, by_name=by_name
                )
                if not changes:
                    reply_tg(format_nochange_msg(recipe.title, warnings))
                    print(f"No change: {rel_path}")
                    return 0
                reply_tg(format_edit_success_msg(recipe, PAGES_BASE_URL + rel_path, changes, warnings))
                print(f"Edited: {rel_path} ({len(changes)} changed lines)")
                return 0

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
        except edit.AmbiguousError as e:
            reply_tg(format_ambiguous_msg(e.candidates))
            print(f"Ambiguous: {e}")
            return 0  # user error, already answered in chat
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

