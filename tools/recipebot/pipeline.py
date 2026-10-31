"""End-to-end orchestration: inputs -> Gemini -> Recipe -> files on disk."""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from . import llm, siteindex
from .model import Recipe, RecipeError
from .prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT
from .render import render_page
from .sources import Message, SourceDoc, load_source

DEFAULT_MODEL = "gemini-flash-latest"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


@dataclass
class Inputs:
    message: Message
    images: list[tuple[bytes, str]] = field(default_factory=list)  # (data, mime type)
    urls: list[str] = field(default_factory=list)


def read_image(path: Path) -> tuple[bytes, str]:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if mime not in IMAGE_TYPES:
        raise RecipeError(f"Nem támogatott képformátum: {path.name}")
    return path.read_bytes(), mime


def read_url_with_model(url: str, *, model: str, api_key: str, **kw) -> SourceDoc:
    """Fallback for pages we can't scrape ourselves: let Gemini's url_context tool read them."""
    prompt = (
        "Olvasd be ezt az oldalt, és add vissza a recept teljes szövegét (cím, adag, hozzávalók, "
        f"elkészítés, tippek) szó szerint, más szöveg nélkül: {url}"
    )
    try:
        text = llm.generate([llm.text_part(prompt)], model=model, api_key=api_key, tools=[{"url_context": {}}], **kw)
    except llm.LLMError as e:
        raise RecipeError(f"Nem sikerült elolvasni a linket: {e}") from None
    return SourceDoc(url, "", "model", str(text))


def build_parts(msg: Message, docs: list[SourceDoc], images: list[tuple[bytes, str]]) -> list[dict]:
    parts = []
    if msg.text:
        parts.append(llm.text_part(f"A felhasználó üzenete: {msg.text}"))
    if msg.category:
        parts.append(llm.text_part(f"A kategória legyen: {msg.category}"))
    for doc in docs:
        parts.append(llm.text_part(f"<<<FORRÁS: {doc.url}\n{doc.content}\n>>>"))
    for data, mime in images:
        parts.append(llm.image_part(data, mime))
    if not parts:
        raise RecipeError("Nincs mit feldolgozni: adj meg linket, képet vagy receptszöveget.")
    return parts


def extract(inputs: Inputs, *, model: str, api_key: str, **kw) -> tuple[Recipe, list[SourceDoc]]:
    """Call the model and return a validated Recipe (not yet saved)."""
    msg = inputs.message
    docs = []
    for url in inputs.urls:
        doc = load_source(url)
        if doc.kind == "unreadable":
            doc = read_url_with_model(url, model=model, api_key=api_key, **kw)
        docs.append(doc)
    parts = build_parts(msg, docs, inputs.images)
    try:
        data = llm.generate(parts, model=model, api_key=api_key, system=SYSTEM_PROMPT, schema=RESPONSE_SCHEMA, **kw)
    except llm.LLMError as e:
        raise RecipeError(str(e)) from None
    if msg.category:
        data["category"] = msg.category
    if docs:
        data["source_url"] = docs[0].url
        data["source_label"] = docs[0].label
    return Recipe.from_dict(data), docs


def publish(recipe: Recipe, root: Path, *, dry_run: bool = False) -> str:
    """Write the page and add it to the index. Returns the repo-relative page path.

    Raises RecipeError("duplicate:<path>") if the recipe already exists.
    """
    index_path = root / "index.html"
    index_html = index_path.read_text(encoding="utf-8")
    existing = siteindex.find_duplicate(index_html, root, recipe)
    if existing:
        raise DuplicateError(existing)
    rel = f"{recipe.category}/{recipe.slug}.html"
    new_index = siteindex.add_entry(index_html, recipe)  # raises before anything is written
    if not dry_run:
        page = root / rel
        page.parent.mkdir(exist_ok=True)
        page.write_text(render_page(recipe), encoding="utf-8", newline="\n")
        index_path.write_text(new_index, encoding="utf-8", newline="\n")
    return rel


class DuplicateError(RecipeError):
    def __init__(self, existing: str):
        super().__init__(f"Már létezik: {existing}")
        self.existing = existing

