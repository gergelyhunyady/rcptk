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
