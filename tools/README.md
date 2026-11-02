# tools/ – recipe ingestion

`add_recipe.py` turns a link, photo(s) or text into a recipe page + index entry.
Pure Python standard library, no `pip install` needed (Python 3.10+).

```sh
export GEMINI_API_KEY=...            # free key from https://aistudio.google.com
# optional: export GEMINI_MODEL=...  # default: gemini-flash-latest

tools/add_recipe.py --message "https://example.com/recipe"            # link
tools/add_recipe.py --message "#sutik" --image page1.jpg --image page2.jpg   # handwritten
tools/add_recipe.py --message "pasted recipe text ..."               # text
tools/add_recipe.py --message "<link>" --dry-run --show-html          # preview, writes nothing
tools/add_recipe.py --from-json recipe.json                           # no LLM, render only
```

`#category` in the message (`#sutik`, `#hus`, `#leves`, ...) overrides the model's category choice.
Exit codes: `0` ok, `1` error, `2` duplicate (nothing written). `--result-file` writes the JSON result.

Layout: `recipebot/` – `model` (data + validation), `render` (page template), `siteindex`
(index.html edits + duplicate check), `sources` (message / URL parsing), `llm` (Gemini REST),
`prompts` (house style + JSON schema), `pipeline` (orchestration).

Tests: `python3 -m unittest discover -s tools/tests` (includes a round-trip check that the
renderer reproduces the existing pages).

## Editing existing recipes (via the Telegram bot / Action)

- **Reply** to the bot's "✅ Recept hozzáadva" (or "✏️ módosítva") message with the change, e.g.
  `légkeveréssel 180°C-on, 25 perc` – the replied-to message's link identifies the recipe.
- Or send `/edit csokis muffin: a tejet 150 ml-re csökkentsd` (name is matched against the index; if
  several match, the bot lists them).
- The bot answers with the changed lines (➖ old / ➕ new) and commits `Edit <title> recipe (bot)`.
  `/undo` reverts it. Title, category and source link are never changed by an edit.

Code: `recipebot/edit.py` (find / edit / diff), `recipebot/pageparse.py` (page -> recipe).
After changing `relay/worker.js`, paste it into the Cloudflare Worker again and **Deploy**.
