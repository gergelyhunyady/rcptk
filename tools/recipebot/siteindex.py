"""Read / update the tile lists in index.html and detect duplicates."""
from __future__ import annotations

import re
from html import escape, unescape
from pathlib import Path

from .model import CATEGORIES, Recipe, RecipeError, slugify, sort_key

_LI = re.compile(r'^(?P<indent>[ \t]*)<li><a href="(?P<href>[^"]+)">(?P<text>.*?)</a></li>[ \t]*$')


def _tile_re(category: str) -> re.Pattern:
    return re.compile(
        rf'(<article class="tile tile-{category}">.*?<ul>\n)(?P<body>.*?)(\n[ \t]*</ul>)',
        re.S,
    )


def entries(index_html: str) -> list[tuple[str, str]]:
    """All (href, title) pairs listed on the index."""
    out = []
    for line in index_html.splitlines():
        m = _LI.match(line)
        if m:
            out.append((m["href"], unescape(m["text"])))
    return out


def find_duplicate(index_html: str, root: Path, recipe: Recipe) -> str | None:
    """Return the relative path of an already existing recipe, or None."""
    slug = recipe.slug
    for category in CATEGORIES:
        if (root / category / f"{slug}.html").exists():
            return f"{category}/{slug}.html"
    for href, title in entries(index_html):
        if href.rstrip("/").endswith(f"/{slug}.html") or slugify(title) == slug:
            return href.removeprefix("./")
    return None


def add_entry(index_html: str, recipe: Recipe) -> str:
    """Insert the recipe link into its category tile, keeping the list alphabetical."""
    m = _tile_re(recipe.category).search(index_html)
    if not m:
        raise RecipeError(f"Nem található a(z) {recipe.category} csempe az index.html-ben.")
    lines = m["body"].split("\n")
    indent = next((_LI.match(l)["indent"] for l in lines if _LI.match(l)), " " * 16)
    new_line = (
        f'{indent}<li><a href="./{recipe.category}/{recipe.slug}.html">'
        f"{escape(recipe.title, quote=False)}</a></li>"
    )
    key = sort_key(recipe.title)
    pos = len(lines)
    for i, line in enumerate(lines):
        li = _LI.match(line)
        if li and sort_key(unescape(li["text"])) > key:
            pos = i
            break
    lines.insert(pos, new_line)
    return index_html[: m.start("body")] + "\n".join(lines) + index_html[m.end("body") :]
