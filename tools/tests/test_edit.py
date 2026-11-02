import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import action_runner  # noqa: E402
from recipebot import edit, llm  # noqa: E402
from recipebot.model import RecipeError  # noqa: E402
from recipebot.render import render_page  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
BASE = "https://gergelyhunyady.github.io/rcptk/"


class EditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "index.html", self.tmp)
        shutil.copy(ROOT / "style.css", self.tmp)
        for c in ("egytaletelek", "sutik", "levesek", "husok"):
            shutil.copytree(ROOT / c, self.tmp / c)
        self.index = (self.tmp / "index.html").read_text(encoding="utf-8")
        self._orig = llm.generate

    def tearDown(self):
        llm.generate = self._orig
        shutil.rmtree(self.tmp)

    def fake_llm(self, mutate):
        """Make the 'model' return the current recipe after applying `mutate` to its dict."""
        captured = {}

        def gen(parts, **kw):
            import json
            text = parts[0]["text"].split("\n", 1)[1]
            data = json.loads(text)
            captured["request"] = [p.get("text") for p in parts]
            mutate(data)
            data["warnings"] = []
            return data

        llm.generate = gen
        return captured

    # --- finding
    def test_find_by_name(self):
        self.assertEqual(edit.find_by_name(self.index, self.tmp, "csokis muffin")[0], "sutik/csokismuffin.html")
        self.assertEqual(edit.find_by_name(self.index, self.tmp, "Csokis Muffin")[0], "sutik/csokismuffin.html")
        self.assertEqual(edit.find_by_name(self.index, self.tmp, "csokimuffin")[0], "sutik/csokismuffin.html")  # typo
        self.assertEqual(edit.find_by_name(self.index, self.tmp, "kacsacomb")[0], "husok/kacsacomb.html")

    def test_find_ambiguous_and_missing(self):
        with self.assertRaises(edit.AmbiguousError) as cm:
            edit.find_by_name(self.index, self.tmp, "tarja")
        self.assertGreaterEqual(len(cm.exception.candidates), 2)
        with self.assertRaises(edit.NoMatchError):
            edit.find_by_name(self.index, self.tmp, "xyzxyzxyz")

    def test_path_from_url(self):
        f = lambda u: edit.path_from_url(u, BASE, self.tmp)
        self.assertEqual(f(BASE + "sutik/csokismuffin.html"), "sutik/csokismuffin.html")
        self.assertEqual(f(BASE + "sutik/csokismuffin.html?x=1#y"), "sutik/csokismuffin.html")
        self.assertIsNone(f(BASE + "sutik/nincs-ilyen.html"))
        self.assertIsNone(f(BASE + "../../etc/passwd.html"))
        self.assertIsNone(f("https://evil.example/sutik/csokismuffin.html"))

    # --- editing
    def test_edit_changes_only_requested_line(self):
        old = edit.load_recipe(self.tmp, "sutik/csokismuffin.html")

        def mutate(d):
            step = next(s for s in d["sections"] if s["kind"] == "steps")
            step["items"] = [i.replace("200°C-on sütjük 5 percig, majd a hőmérsékletet 180°C-ra csökkentjük és további 20 percig sütjük.",
                                       "190°C-on sütjük 22 percig, légkeveréssel.") for i in step["items"]]

        cap = self.fake_llm(mutate)
        new = edit.apply_edit(old, "190 fokon légkeveréssel 22 perc", model="m", api_key="k")
        self.assertIn("190 fokon légkeveréssel 22 perc", cap["request"][1])
        changes = edit.diff_recipes(old, new)
        self.assertEqual(len(changes), 2)
        self.assertTrue(changes[0].startswith("- Elkészítés: 200°C-on"))
        self.assertTrue(changes[1].startswith("+ Elkészítés: 190°C-on"))
        edit.save(new, self.tmp, "sutik/csokismuffin.html")
        page = (self.tmp / "sutik/csokismuffin.html").read_text(encoding="utf-8")
        self.assertIn("190°C-on sütjük 22 percig", page)
        self.assertIn("<h2>Forrás</h2>", page)  # source block preserved

    def test_title_category_source_are_pinned(self):
        old = edit.load_recipe(self.tmp, "sutik/csokismuffin.html")

        def mutate(d):
            d["title"], d["category"] = "Más cím", "husok"

        self.fake_llm(mutate)
        new = edit.apply_edit(old, "valami", model="m", api_key="k")
        self.assertEqual((new.title, new.category, new.source_url), (old.title, "sutik", old.source_url))
        self.assertEqual(edit.diff_recipes(old, new), [])

    def test_no_change_has_empty_diff(self):
        old = edit.load_recipe(self.tmp, "levesek/gombaleves.html")
        self.fake_llm(lambda d: None)
        self.assertEqual(edit.diff_recipes(old, edit.apply_edit(old, "x", model="m", api_key="k")), [])

    def test_truncated_model_answer_rejected(self):
        old = edit.load_recipe(self.tmp, "sutik/csokismuffin.html")

        def mutate(d):
            for s in d["sections"]:
                s["items"] = s["items"][:1]

        self.fake_llm(mutate)
        with self.assertRaises(RecipeError):
            edit.apply_edit(old, "x", model="m", api_key="k")

    def test_empty_instruction_rejected(self):
        old = edit.load_recipe(self.tmp, "sutik/csokismuffin.html")
        with self.assertRaises(RecipeError):
            edit.apply_edit(old, "  ", model="m", api_key="k")

    def test_italic_intro_page_survives_edit(self):
        old = edit.load_recipe(self.tmp, "sutik/bogres-teszta.html")
        self.assertTrue(old.intro_italic)
        self.assertIn("<p><em>", render_page(old))


class CommandParsing(unittest.TestCase):
    def test_edit_command(self):
        p = action_runner.parse_edit_command
        self.assertEqual(p("/edit csokis muffin: 190 fok"), ("csokis muffin", "190 fok"))
        self.assertEqual(p("/edit csokis muffin\nA tejet felezd"), ("csokis muffin", "A tejet felezd"))
        self.assertEqual(p("/edit@rcptk_bot muffin: x: y"), ("muffin", "x: y"))
        self.assertEqual(p("/edit muffin"), ("muffin", ""))
        self.assertEqual(p("/edit"), ("", ""))
        self.assertIsNone(p("https://example.com/recept"))
        self.assertIsNone(p("editálj valamit"))

    def test_messages_are_html_safe_and_bounded(self):
        changes = ["- " + "a<b>" * 100, "+ " + "c&d" * 100] * 30
        old = action_runner.format_edit_success_msg
        from recipebot.model import Recipe
        r = Recipe.from_dict({"title": "T <x>", "category": "sutik",
                              "sections": [{"kind": "ingredients", "heading": "", "items": ["a"]}]})
        msg = old(r, BASE + "sutik/t.html", changes, ["w<1>"])
        self.assertLess(len(msg), 4096)
        self.assertNotIn("<b>a", msg)
        self.assertIn("&lt;", msg)
        self.assertIn("még", msg)


if __name__ == "__main__":
    unittest.main()
