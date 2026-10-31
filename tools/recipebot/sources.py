"""Turn a user message into hints, and a URL into text the model can read."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from .model import CATEGORIES, RecipeError, strip_accents

MAX_PAGE_BYTES = 3_000_000
MAX_TEXT_CHARS = 40_000
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ---------------------------------------------------------------- message ---

_URL = re.compile(r"https?://[^\s<>\"']+")
_TAG = re.compile(r"#(\w+)", re.UNICODE)


def resolve_category(tag: str) -> str | None:
    """'#sütik', '#suti', '#hus' ... -> folder name (unique prefix match, >= 3 chars)."""
    tag = strip_accents(tag)
    if len(tag) < 3:
        return None
    matches = [c for c in CATEGORIES if c.startswith(tag)]
    return matches[0] if len(matches) == 1 else None


@dataclass
class Message:
    category: str | None
    urls: list[str]
    text: str  # what is left after removing #category tags and URLs


def parse_message(raw: str) -> Message:
    raw = raw or ""
    category = None

    def drop_tag(m: re.Match) -> str:
        nonlocal category
        found = resolve_category(m.group(1))
        if found and category is None:
            category = found
            return ""
        return m.group(0)

    text = _TAG.sub(drop_tag, raw)
    urls = [u.rstrip(".,;:!?)]}") for u in _URL.findall(text)]
    text = _URL.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return Message(category, urls, text)


# ------------------------------------------------------------------- html ---

_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe", "button"}
_BLOCK_TAGS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "ul", "ol"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False
        self.jsonld: list[str] = []
        self._in_jsonld = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self.jsonld.append("")
            return
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            return
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_jsonld:
            self.jsonld[-1] += data
        elif self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def parse_html(html: str) -> tuple[str, str, list[str]]:
    """-> (title, readable text, raw JSON-LD script blocks)"""
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return re.sub(r"\s+", " ", p.title).strip(), text, p.jsonld


_RECIPE_KEYS = (
    "name", "description", "recipeYield", "prepTime", "cookTime", "totalTime",
    "recipeCategory", "recipeCuisine", "recipeIngredient", "recipeInstructions",
)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def find_jsonld_recipe(blocks: list[str]) -> dict | None:
    """First schema.org Recipe with ingredients and instructions, trimmed to useful keys."""
    for raw in blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in _walk(data):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Recipe" in kinds and node.get("recipeIngredient") and node.get("recipeInstructions"):
                return {k: node[k] for k in _RECIPE_KEYS if node.get(k)}
    return None


# ------------------------------------------------------------------ fetch ---


@dataclass
class SourceDoc:
    url: str
    title: str
    kind: str  # 'jsonld' | 'text' | 'unreadable'
    content: str  # what we hand to the model

    @property
    def label(self) -> str:
        host = urllib.parse.urlsplit(self.url).netloc.removeprefix("www.")
        return f"{host} - {self.title}" if self.title else host


def check_url(url: str) -> None:
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise RecipeError("Csak http(s) linkek támogatottak.")


def fetch_html(url: str, *, opener=urllib.request.urlopen, timeout: int = 30) -> str:
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "hu,en;q=0.8"})
    with opener(req, timeout=timeout) as resp:
        raw = resp.read(MAX_PAGE_BYTES + 1)
        charset = resp.headers.get_content_charset() or "utf-8"
    if len(raw) > MAX_PAGE_BYTES:
        raise RecipeError("Az oldal túl nagy.")
    return raw.decode(charset, errors="replace")


def load_source(url: str, *, opener=urllib.request.urlopen) -> SourceDoc:
    """Fetch a page. JSON-LD recipe data is preferred over scraped text."""
    try:
        html = fetch_html(url, opener=opener)
    except (urllib.error.URLError, OSError, RecipeError):
        return SourceDoc(url, "", "unreadable", "")
    title, text, blocks = parse_html(html)
    recipe = find_jsonld_recipe(blocks)
    if recipe:
        title = str(recipe.get("name") or title)
        return SourceDoc(url, title, "jsonld", json.dumps(recipe, ensure_ascii=False)[:MAX_TEXT_CHARS])
    if len(text) < 400:  # JS-rendered shell, bot wall, ...
        return SourceDoc(url, title, "unreadable", "")
    return SourceDoc(url, title, "text", text[:MAX_TEXT_CHARS])
