"""Parse an existing recipe page back into a Recipe dict (used for edits and tests)."""
from __future__ import annotations

import re
from html.parser import HTMLParser


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.stack: list[str] = []
        self.buf = ""
        self.cur_class = None
        self.href = None
        self.title = ""
        self.intro = None
        self.intro_italic = False
        self.note = None
        self.sections: list[dict] = []
        self.source = None  # (url, label)
        self.unsupported = False
        self._p_has_em = False
        self._list_kind = None
        self._em = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "body":
            self.in_body = True
            self.category = (a.get("class") or "").removeprefix("cat-")
        if not self.in_body:
            return
        self.stack.append(tag)
        if tag in ("h1", "h2", "li", "p"):
            self.buf = ""
            self.cur_class = a.get("class")
            self._p_has_em = False
        if tag == "em":
            self._p_has_em = True
        if tag == "a" and "href" in a and a.get("class") != "back-link":
            self.href = a["href"]
        if tag in ("ul", "ol"):
            self._list_kind = tag
            sec = self.sections[-1]
            sec["list"] = tag
            sec.setdefault("items", [])
        if tag in ("div", "table", "img", "h3", "strong", "b", "i"):
            self.unsupported = True

    def handle_data(self, data):
        if self.in_body:
            self.buf += data

    def handle_endtag(self, tag):
        if not self.in_body:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        text = re.sub(r"\s+", " ", self.buf).strip()
        if tag == "h1":
            self.title = text
        elif tag == "h2":
            self.sections.append({"heading": text, "lead": None, "items": [], "list": None, "paras": []})
        elif tag == "li":
            self.sections[-1]["items"].append(text)
        elif tag == "p":
            if not self.sections:
                if self.cur_class == "recipe-note":
                    self.note = text
                else:
                    self.intro = text
                    self.intro_italic = self._p_has_em
            elif self.sections[-1]["heading"] == "Forrás" and self.href:
                self.source = (self.href, text)
            elif self._p_has_em and not self.sections[-1]["list"]:
                self.sections[-1]["lead"] = text
            else:
                self.sections[-1]["paras"].append(text)

    def to_dict(self) -> dict:
        secs = []
        for s in self.sections:
            if s["heading"] == "Forrás":
                continue
            if s["list"] == "ol":
                kind, items = "steps", s["items"]
            elif s["list"] == "ul":
                kind = "bullets" if s["heading"].startswith(("Tipp", "Megjegyz", "Díszít")) else "ingredients"
                items = s["items"]
            else:
                kind, items = "text", s["paras"]
            secs.append({"heading": s["heading"], "kind": kind, "lead": s["lead"], "items": items})
            if s["list"] and s["paras"]:
                self.unsupported = True  # list and loose paragraphs in the same section
        d = {"title": self.title, "category": self.category, "intro": self.intro, "intro_italic": self.intro_italic, "note": self.note, "sections": secs}
        if self.source:
            d["source_url"], d["source_label"] = self.source
        return d


def parse_page(html: str) -> tuple[dict, bool]:
    p = PageParser()
    p.feed(html)
    return p.to_dict(), p.unsupported
