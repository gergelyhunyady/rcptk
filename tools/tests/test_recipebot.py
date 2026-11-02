import glob
import json
import re
import shutil
import sys
import tempfile
import unittest
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from recipebot import llm, pipeline, siteindex  # noqa: E402
from recipebot.pageparse import parse_page  # noqa: E402
from recipebot.model import Recipe, RecipeError, slugify, sort_key  # noqa: E402
from recipebot.render import render_page  # noqa: E402
from recipebot.sources import find_jsonld_recipe, parse_html, parse_message, resolve_category  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent


def norm(html: str) -> str:
    """Whitespace-insensitive form; ignores <title> (a few old pages differ there)."""
    html = re.sub(r"<title>.*?</title>", "", html, flags=re.S)
    html = unescape(html)
    html = re.sub(r"\s+", " ", html)
    return re.sub(r"> <", "><", html).strip()


class RoundTrip(unittest.TestCase):
    """Rendering a parsed existing page must reproduce that page."""

    def test_existing_pages(self):
        checked, skipped = 0, []
        for f in sorted(glob.glob(str(ROOT / "*" / "*.html"))):
            html = Path(f).read_text(encoding="utf-8")
            if "style.css" not in html:
                continue  # empty placeholder pages
            data, unsupported = parse_page(html)
            if unsupported:
                skipped.append(Path(f).name)
                continue
            with self.subTest(page=Path(f).relative_to(ROOT).as_posix()):
                self.assertEqual(norm(render_page(Recipe.from_dict(data))), norm(html))
            checked += 1
        print(f"\nround-trip: {checked} pages checked, {len(skipped)} skipped (unsupported layout): {skipped}")
        self.assertEqual(skipped, [])
        self.assertGreater(checked, 50)


SAMPLE = {
    "title": "Túrós <tészta> & több",
    "category": "egytaletelek",
    "intro": "2 személyre",
    "sections": [
        {"heading": "", "kind": "ingredients", "items": ["- 250 g tészta", " 1 tk só "]},
        {"heading": "", "kind": "steps", "items": ["1. Megfőzzük.", "2) Tálaljuk.", ""]},
    ],
    "warnings": [],
}


class ModelTests(unittest.TestCase):
    def test_clean_and_defaults(self):
        r = Recipe.from_dict(SAMPLE)
        self.assertEqual(r.sections[0].heading, "Hozzávalók")
        self.assertEqual(r.sections[0].items, ["250 g tészta", "1 tk só"])
        self.assertEqual(r.sections[1].items, ["Megfőzzük.", "Tálaljuk."])
        self.assertEqual(r.slug, "turos-teszta-tobb")

    def test_decimals_not_stripped(self):
        r = Recipe.from_dict({**SAMPLE, "sections": [
            {"kind": "ingredients", "heading": "", "items": ["1.5 dl tej"]},
            {"kind": "steps", "heading": "", "items": ["1.5 percig főzzük."]}]})
        self.assertEqual(r.sections[0].items[0], "1.5 dl tej")
        self.assertEqual(r.sections[1].items[0], "1.5 percig főzzük.")

    def test_rejects(self):
        with self.assertRaises(RecipeError):
            Recipe.from_dict({**SAMPLE, "category": "pizza"})
        with self.assertRaises(RecipeError):
            Recipe.from_dict({**SAMPLE, "sections": []})
        with self.assertRaises(RecipeError):
            Recipe.from_dict({**SAMPLE, "title": "  "})

    def test_html_is_escaped(self):
        html = render_page(Recipe.from_dict(SAMPLE))
        self.assertIn("<h1>Túrós &lt;tészta&gt; &amp; több</h1>", html)
        self.assertNotIn("<tészta>", html)

    def test_slug_and_sort(self):
        self.assertEqual(slugify("Őszibarackos Ünnepi Rétes!"), "oszibarackos-unnepi-retes")
        self.assertLess(sort_key("Ízes"), sort_key("Jó"))


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "index.html", self.tmp)
        shutil.copy(ROOT / "style.css", self.tmp)
        for c in ("egytaletelek", "sutik"):
            shutil.copytree(ROOT / c, self.tmp / c)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def recipe(self, title, cat="sutik"):
        return Recipe.from_dict({**SAMPLE, "title": title, "category": cat})

    def test_alphabetical_insert(self):
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        out = siteindex.add_entry(index, self.recipe("Dobos torta"))
        titles = [t for h, t in siteindex.entries(out) if h.startswith("./sutik/")]
        self.assertEqual(titles.index("Dobos torta"), titles.index("Csokis muffin") + 1)
        # only one line added, everything else byte-identical
        self.assertEqual(out.count("\n"), index.count("\n") + 1)

    def test_insert_first_and_last(self):
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        titles = lambda s: [t for h, t in siteindex.entries(s) if h.startswith("./sutik/")]
        self.assertEqual(titles(siteindex.add_entry(index, self.recipe("Aaa")))[0], "Aaa")
        self.assertEqual(titles(siteindex.add_entry(index, self.recipe("Zzz")))[-1], "Zzz")

    def test_duplicates(self):
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertIsNotNone(siteindex.find_duplicate(index, self.tmp, self.recipe("Palacsinta", "sutik")))  # exists in egytaletelek
        self.assertIsNotNone(siteindex.find_duplicate(index, self.tmp, self.recipe("Csokis  Muffin")))  # same title as index entry
        self.assertIsNone(siteindex.find_duplicate(index, self.tmp, self.recipe("Dobos torta")))

    def test_publish_and_duplicate_refused(self):
        r = self.recipe("Dobos torta")
        rel = pipeline.publish(r, self.tmp)
        self.assertEqual(rel, "sutik/dobos-torta.html")
        self.assertTrue((self.tmp / rel).exists())
        self.assertIn("./sutik/dobos-torta.html", (self.tmp / "index.html").read_text(encoding="utf-8"))
        before = (self.tmp / "index.html").read_text(encoding="utf-8")
        with self.assertRaises(pipeline.DuplicateError):
            pipeline.publish(r, self.tmp)
        self.assertEqual(before, (self.tmp / "index.html").read_text(encoding="utf-8"))

    def test_dry_run_writes_nothing(self):
        before = (self.tmp / "index.html").read_text(encoding="utf-8")
        pipeline.publish(self.recipe("Dobos torta"), self.tmp, dry_run=True)
        self.assertFalse((self.tmp / "sutik/dobos-torta.html").exists())
        self.assertEqual(before, (self.tmp / "index.html").read_text(encoding="utf-8"))


class SourceTests(unittest.TestCase):
    def test_message_parsing(self):
        m = parse_message("Nézd meg ezt: https://example.com/r/1?a=b). #sütik  kevesebb cukorral")
        self.assertEqual(m.category, "sutik")
        self.assertEqual(m.urls, ["https://example.com/r/1?a=b"])
        self.assertNotIn("#", m.text)
        self.assertNotIn("http", m.text)

    def test_category_aliases(self):
        for tag, cat in [("suti", "sutik"), ("sütik", "sutik"), ("hus", "husok"), ("hal", "haletelek"),
                         ("egytálétel", "egytaletelek"), ("leves", "levesek"), ("saláta", "salatak"),
                         ("köret", "koretek"), ("krém", "kremek")]:
            self.assertEqual(resolve_category(tag), cat, tag)
        self.assertIsNone(resolve_category("k"))
        self.assertIsNone(resolve_category("pizza"))

    def test_unknown_hashtag_kept(self):
        m = parse_message("#vacsora https://x.hu/a")
        self.assertIsNone(m.category)
        self.assertIn("#vacsora", m.text)

    HTML = """<html><head><title> Jó  süti </title>
    <script type="application/ld+json">{"@graph":[{"@type":"WebSite"},
      {"@type":["Recipe"],"name":"Jó süti","image":"x.jpg","recipeIngredient":["1 tojás"],
       "recipeInstructions":[{"@type":"HowToStep","text":"Süssük."}]}]}</script>
    <style>p{}</style></head><body><nav>Menü</nav><h1>Jó süti</h1><p>Hozzávalók: tojás</p>
    <script>var x=1;</script><footer>lábléc</footer></body></html>"""

    def test_html_parsing(self):
        title, text, blocks = parse_html(self.HTML)
        self.assertEqual(title, "Jó süti")
        self.assertIn("Hozzávalók: tojás", text)
        for noise in ("Menü", "lábléc", "var x", "p{}"):
            self.assertNotIn(noise, text)
        r = find_jsonld_recipe(blocks)
        self.assertEqual(r["name"], "Jó süti")
        self.assertNotIn("image", r)

    def test_jsonld_requires_instructions(self):
        self.assertIsNone(find_jsonld_recipe(['{"@type":"Recipe","name":"x","recipeIngredient":["a"]}', "not json"]))


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        import json as j
        return j.dumps(self.payload).encode()


class LLMTests(unittest.TestCase):
    def test_request_shape_and_parse(self):
        seen = {}

        def opener(req, timeout):
            seen["req"] = req
            return FakeResp({"candidates": [{"content": {"parts": [
                {"text": "ignored", "thought": True}, {"text": '{"title": "X"}'}]}}]})

        out = llm.generate([llm.text_part("hi"), llm.image_part(b"abc", "image/jpeg")], model="m", api_key="K",
                           system="sys", schema={"type": "OBJECT"}, opener=opener)
        self.assertEqual(out, {"title": "X"})
        req = seen["req"]
        self.assertTrue(req.full_url.endswith("/models/m:generateContent"))
        self.assertNotIn("K", req.full_url)
        self.assertEqual(req.get_header("X-goog-api-key"), "K")
        import json as j
        body = j.loads(req.data)
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "sys")
        self.assertEqual(body["contents"][0]["parts"][1]["inline_data"]["data"], "YWJj")

    def test_retry_then_success(self):
        import io
        import urllib.error
        calls = []

        def opener(req, timeout):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b"busy"))
            return FakeResp({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

        self.assertEqual(llm.generate([llm.text_part("x")], model="m", api_key="k", opener=opener, sleep=lambda s: None), "ok")
        self.assertEqual(len(calls), 3)

    def test_non_retryable_error(self):
        import io
        import urllib.error

        def opener(req, timeout):
            raise urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(b"bad key"))

        with self.assertRaises(llm.LLMError):
            llm.generate([llm.text_part("x")], model="m", api_key="k", opener=opener, sleep=lambda s: None)

    def test_blocked(self):
        with self.assertRaises(llm.LLMError):
            llm.generate([llm.text_part("x")], model="m", api_key="k",
                         opener=lambda r, timeout: FakeResp({"promptFeedback": {"blockReason": "SAFETY"}}))


class PipelineTests(unittest.TestCase):
    def test_category_override_and_source(self):
        reply = {"title": "Teszt süti", "category": "husok",
                 "sections": [{"heading": "Hozzávalók", "kind": "ingredients", "items": ["1 tojás"]}], "warnings": []}
        orig = llm.generate
        llm.generate = lambda *a, **k: dict(reply)
        orig_load = pipeline.load_source
        pipeline.load_source = lambda url: pipeline.SourceDoc(url, "Teszt", "text", "szöveg")
        try:
            msg = parse_message("https://www.example.com/x #sutik")
            r, _ = pipeline.extract(pipeline.Inputs(msg, [], msg.urls), model="m", api_key="k")
        finally:
            llm.generate, pipeline.load_source = orig, orig_load
        self.assertEqual(r.category, "sutik")
        self.assertEqual(r.source_url, "https://www.example.com/x")
        self.assertEqual(r.source_label, "example.com - Teszt")
        self.assertIn('<a href="https://www.example.com/x">example.com - Teszt</a>', render_page(r))

    def test_nothing_to_process(self):
        with self.assertRaises(RecipeError):
            pipeline.build_parts(parse_message(""), [], [])


if __name__ == "__main__":
    unittest.main()
