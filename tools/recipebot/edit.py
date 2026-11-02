"""Edit an existing recipe page: find it, ask the model for the change, diff the result."""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import llm, siteindex
from .model import CATEGORIES, Recipe, RecipeError, slugify, sort_key
from .pageparse import parse_page
from .prompts import EDIT_SYSTEM_PROMPT, RESPONSE_SCHEMA
from .render import render_page

_PAGE_PATH = re.compile(r"(?P<cat>[a-z]+)/(?P<slug>[a-z0-9-]+)\.html$")


class NoMatchError(RecipeError):
    pass


class AmbiguousError(RecipeError):
    def __init__(self, candidates: list[tuple[str, str]]):
        super().__init__("Több recept is egyezik: " + ", ".join(t for _, t in candidates))
        self.candidates = candidates  # [(rel_path, title)]


# ---------------------------------------------------------------- finding ---


def path_from_url(url: str, base_url: str, root: Path) -> str | None:
    """'https://…/rcptk/sutik/x.html?y#z' -> 'sutik/x.html' if it is a page of this site."""
    if not url.startswith(base_url):
        return None
    rest = re.split(r"[?#]", url[len(base_url):], maxsplit=1)[0]
    m = _PAGE_PATH.fullmatch(rest)
    if m and m["cat"] in CATEGORIES and (root / rest).is_file():
        return rest
    return None


def find_by_name(index_html: str, root: Path, query: str) -> tuple[str, str]:
    """Resolve a user-typed recipe name to (rel_path, title). Raises NoMatch/Ambiguous."""
    entries = [
        (href.removeprefix("./"), title)
        for href, title in siteindex.entries(index_html)
        if (root / href.removeprefix("./")).is_file()
    ]
    key = sort_key(query)
    if not key:
        raise NoMatchError("Nem adtál meg receptnevet.")
    exact = [e for e in entries if sort_key(e[1]) == key or slugify(e[1]) == slugify(query)]
    if len(exact) == 1:
        return exact[0]
    contained = [e for e in entries if key in sort_key(e[1]) or sort_key(e[1]) in key]
    if len(contained) == 1:
        return contained[0]
    if len(contained) > 1:
        raise AmbiguousError(contained[:6])
    titles = {sort_key(t): (p, t) for p, t in entries}
    close = difflib.get_close_matches(key, list(titles), n=3, cutoff=0.6)
    if len(close) == 1:
        return titles[close[0]]
    if close:
        raise AmbiguousError([titles[c] for c in close])
    raise NoMatchError(f"Nem találtam ilyen receptet: {query}")


def load_recipe(root: Path, rel_path: str) -> Recipe:
    html = (root / rel_path).read_text(encoding="utf-8")
    data, unsupported = parse_page(html)
    if unsupported:
        raise RecipeError("Ennek az oldalnak szokatlan a szerkezete, ezt kézzel szerkeszd.")
    data["category"] = rel_path.split("/")[0]
    recipe = Recipe.from_dict(data)
    recipe.warnings = []
    return recipe


# ---------------------------------------------------------------- editing ---


def apply_edit(
    old: Recipe,
    instruction: str,
    images: list[tuple[bytes, str]] | None = None,
    *,
    model: str,
    api_key: str,
    **kw,
) -> Recipe:
    """Ask the model for the modified recipe. Title/category/source stay fixed."""
    if not instruction.strip() and not images:
        raise RecipeError("Nem adtál meg módosítási kérést.")
    parts = [
        llm.text_part("A jelenlegi recept:\n" + json.dumps(old.to_dict(), ensure_ascii=False, indent=1)),
        llm.text_part("Módosítási kérés: " + (instruction.strip() or "(lásd a csatolt képet)")),
    ]
    parts += [llm.image_part(data, mime) for data, mime in images or []]
    try:
        data = llm.generate(
            parts, model=model, api_key=api_key, system=EDIT_SYSTEM_PROMPT, schema=RESPONSE_SCHEMA, **kw
        )
    except llm.LLMError as e:
        raise RecipeError(str(e)) from None
    data["title"], data["category"] = old.title, old.category
    data["source_url"], data["source_label"] = old.source_url, old.source_label
    data["intro_italic"] = old.intro_italic
    new = Recipe.from_dict(data)
    # a reply that lost everything would be a model failure, not an edit
    if sum(len(s.items) for s in new.sections) < 0.5 * sum(len(s.items) for s in old.sections):
        raise RecipeError("A módosított recept gyanúsan rövid lett, nem mentettem el.")
    return new


# ------------------------------------------------------------------- diff ---


def _lines(r: Recipe) -> list[str]:
    out = []
    if r.intro:
        out.append(f"Bevezető: {r.intro}")
    if r.note:
        out.append(f"Megjegyzés: {r.note}")
    for s in r.sections:
        if s.lead:
            out.append(f"{s.heading}: {s.lead}")
        out += [f"{s.heading}: {i}" for i in s.items]
    return out


def diff_recipes(old: Recipe, new: Recipe) -> list[str]:
    """Human-readable change list: '- old line' / '+ new line'. Empty if nothing changed."""
    a, b = _lines(old), _lines(new)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        out += [f"- {x}" for x in a[i1:i2]] + [f"+ {x}" for x in b[j1:j2]]
    return out


def save(recipe: Recipe, root: Path, rel_path: str) -> None:
    (root / rel_path).write_text(render_page(recipe), encoding="utf-8", newline="\n")
