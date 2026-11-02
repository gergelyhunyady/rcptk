"""Recipe data model, validation and slug / sort helpers."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# folder name -> heading used on the index page
CATEGORIES = {
    "levesek": "Levesek",
    "kremek": "Krémek",
    "koretek": "Köretek",
    "husok": "Húsok",
    "sutik": "Sütik",
    "egytaletelek": "Egytálételek",
    "haletelek": "Halételek",
    "salatak": "Saláták",
}

# ingredients -> <ul>, steps -> <ol>, bullets -> <ul>, text -> one <p> per item
KINDS = ("ingredients", "steps", "bullets", "text")

DEFAULT_HEADINGS = {
    "ingredients": "Hozzávalók",
    "steps": "Elkészítés",
    "bullets": "Tippek",
    "text": "Megjegyzés",
}


class RecipeError(ValueError):
    """The recipe is unusable (bad LLM output, duplicate, ...)."""


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", strip_accents(text)).strip("-")
    return slug[:80].strip("-")


def sort_key(text: str) -> str:
    """Accent-insensitive key used to keep index lists alphabetical."""
    return re.sub(r"\s+", " ", strip_accents(text)).strip()


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


_BULLET = re.compile(r"^\s*[-•*–]\s+")
_NUMBERING = re.compile(r"^\s*\d+[.)]\s+")


def _clean_item(text, kind: str) -> str:
    text = _clean(text)
    text = _BULLET.sub("", text)
    if kind in ("steps", "bullets"):
        text = _NUMBERING.sub("", text)
    return text


@dataclass
class Section:
    heading: str
    kind: str
    items: list[str]
    lead: str | None = None  # italic paragraph between the heading and the list


@dataclass
class Recipe:
    title: str
    category: str
    sections: list[Section]
    intro: str | None = None  # plain <p> under the title, e.g. "2 személyre"
    note: str | None = None  # <p class="recipe-note">
    warnings: list[str] = field(default_factory=list)
    source_url: str | None = None
    source_label: str | None = None
    intro_italic: bool = False  # render the intro as <p><em>…</em></p>

    @property
    def slug(self) -> str:
        return slugify(self.title)

    @classmethod
    def from_dict(cls, data: dict) -> "Recipe":
        if not isinstance(data, dict):
            raise RecipeError("A modell válasza nem recept-objektum.")
        title = _clean(data.get("title") or "")
        if not title:
            raise RecipeError("Hiányzik a recept címe.")
        if len(title) > 120:
            raise RecipeError("A recept címe túl hosszú.")
        category = _clean(data.get("category") or "")
        if category not in CATEGORIES:
            raise RecipeError(f"Ismeretlen kategória: {category!r}")

        sections = []
        for raw in data.get("sections") or []:
            kind = _clean(raw.get("kind") or "")
            if kind not in KINDS:
                raise RecipeError(f"Ismeretlen szekció típus: {kind!r}")
            items = [_clean_item(i, kind) for i in raw.get("items") or []]
            items = [i for i in items if i]
            if not items:
                continue
            heading = _clean(raw.get("heading") or "") or DEFAULT_HEADINGS[kind]
            lead = _clean(raw.get("lead") or "") or None
            sections.append(Section(heading, kind, items, lead))

        if not any(s.kind == "ingredients" for s in sections):
            raise RecipeError("A receptnek nincs hozzávaló listája.")
        if not slugify(title):
            raise RecipeError("A címből nem képezhető fájlnév.")

        warnings = [_clean(w) for w in data.get("warnings") or [] if _clean(w)]
        if not any(s.kind == "steps" for s in sections):
            warnings.append("Nincs elkészítési lépés a receptben.")

        return cls(
            title=title,
            category=category,
            sections=sections,
            intro=_clean(data.get("intro") or "") or None,
            note=_clean(data.get("note") or "") or None,
            warnings=warnings,
            source_url=data.get("source_url") or None,
            source_label=data.get("source_label") or None,
            intro_italic=bool(data.get("intro_italic")),
        )

    def to_dict(self) -> dict:
        """The model-facing view (what the LLM sees / returns); no source or warnings."""
        return {
            "title": self.title,
            "category": self.category,
            "intro": self.intro,
            "note": self.note,
            "sections": [
                {"heading": s.heading, "kind": s.kind, "lead": s.lead, "items": list(s.items)}
                for s in self.sections
            ],
        }

