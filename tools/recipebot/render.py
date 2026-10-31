"""Render a Recipe into the house page format (matches the hand-written pages)."""
from __future__ import annotations

from html import escape

from .model import Recipe, Section

I1 = "    "
I2 = I1 * 2


def _t(text: str) -> str:
    # quote=False: plain " is fine in text nodes and matches the existing pages
    return escape(text, quote=False)


def _render_section(s: Section) -> list[str]:
    out = ["", f"{I1}<h2>{_t(s.heading)}</h2>"]
    if s.lead:
        out.append(f"{I1}<p><em>{_t(s.lead)}</em></p>")
    if s.kind == "text":
        out += [f"{I1}<p>{_t(i)}</p>" for i in s.items]
        return out
    tag = "ol" if s.kind == "steps" else "ul"
    out.append(f"{I1}<{tag}>")
    out += [f"{I2}<li>{_t(i)}</li>" for i in s.items]
    out.append(f"{I1}</{tag}>")
    return out


def render_page(r: Recipe) -> str:
    lines = [
        "<!DOCTYPE html>",
        '<html lang="hu">',
        "",
        "<head>",
        f'{I1}<meta charset="UTF-8">',
        f'{I1}<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"{I1}<title>{_t(r.title)} recept</title>",
        f'{I1}<link rel="stylesheet" href="../style.css">',
        "</head>",
        "",
        f'<body class="cat-{r.category}">',
        f'{I1}<a href="../index.html" class="back-link">← Receptek</a>',
        f"{I1}<h1>{_t(r.title)}</h1>",
    ]
    if r.intro:
        lines.append(f"{I1}<p>{_t(r.intro)}</p>")
    if r.note:
        lines.append(f'{I1}<p class="recipe-note">{_t(r.note)}</p>')
    for s in r.sections:
        lines += _render_section(s)
    if r.source_url:
        label = r.source_label or r.source_url
        lines += [
            "",
            f"{I1}<h2>Forrás</h2>",
            f'{I1}<p><a href="{escape(r.source_url, quote=True)}">{_t(label)}</a></p>',
        ]
    lines += ["</body>", "", "</html>", ""]
    return "\n".join(lines)

