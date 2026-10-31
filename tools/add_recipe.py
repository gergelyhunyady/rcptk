#!/usr/bin/env python3
"""Add a recipe to the site from a link, photo(s) or text.

Examples:
  GEMINI_API_KEY=... tools/add_recipe.py --message "https://example.com/recipe #sutik"
  GEMINI_API_KEY=... tools/add_recipe.py --image kep1.jpg --image kep2.jpg --dry-run
  tools/add_recipe.py --from-json recipe.json          # no LLM, just render + index
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from recipebot import pipeline  # noqa: E402
from recipebot.model import Recipe, RecipeError  # noqa: E402
from recipebot.render import render_page  # noqa: E402
from recipebot.sources import check_url, parse_message  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES_BASE_URL = "https://gergelyhunyady.github.io/rcptk/"


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--message", default="", help="raw chat text/caption: URLs, #category tags, free text or the recipe itself")
    p.add_argument("--url", action="append", default=[], help="recipe page to read (repeatable)")
    p.add_argument("--image", action="append", default=[], type=Path, help="photo of a recipe (repeatable)")
    p.add_argument("--category", help="force a category folder, e.g. sutik")
    p.add_argument("--from-json", type=Path, help="skip the LLM and use this recipe JSON")
    p.add_argument("--model", default=os.environ.get("GEMINI_MODEL", pipeline.DEFAULT_MODEL))
    p.add_argument("--root", type=Path, default=REPO_ROOT, help="site root (default: this repo)")
    p.add_argument("--dry-run", action="store_true", help="don't write any files")
    p.add_argument("--show-html", action="store_true", help="print the rendered page")
    p.add_argument("--result-file", type=Path, help="write a machine-readable result here (for the GitHub Action)")
    return p.parse_args(argv)


def run(args) -> dict:
    if args.from_json:
        recipe = Recipe.from_dict(json.loads(args.from_json.read_text(encoding="utf-8")))
    else:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RecipeError("Hiányzik a GEMINI_API_KEY környezeti változó.")
        msg = parse_message(args.message)
        if args.category:
            msg.category = args.category
        urls = list(dict.fromkeys(args.url + msg.urls))
        for u in urls:
            check_url(u)
        images = [pipeline.read_image(p) for p in args.image]
        recipe, _ = pipeline.extract(
            pipeline.Inputs(msg, images, urls), model=args.model, api_key=api_key
        )
    rel = pipeline.publish(recipe, args.root, dry_run=args.dry_run)
    if args.show_html:
        print(render_page(recipe))
    return {
        "status": "ok",
        "title": recipe.title,
        "category": recipe.category,
        "path": rel,
        "url": PAGES_BASE_URL + rel,
        "warnings": recipe.warnings,
        "dry_run": args.dry_run,
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
        code = 0
    except pipeline.DuplicateError as e:
        result = {"status": "duplicate", "error": str(e), "path": e.existing, "url": PAGES_BASE_URL + e.existing}
        code = 2
    except RecipeError as e:
        result = {"status": "error", "error": str(e)}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.result_file:
        args.result_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())

