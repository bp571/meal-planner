# Meal Planner

A meal-planning agent with a local web UI, built with the
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview).

Talk to it on Sunday and get next week's meals plus a shopping list. It plans from your own dish
library (recipes, simple combos and ready meals), estimates calories roughly so you can hit your
target without counting every calorie, and keeps flexible slots such as a weekly fast-food meal.
Your profile and what the agent learns about you are stored locally and carry over from week to
week.

The LLM runs the conversation and picks dishes. Calorie totals, plan checks and the shopping list
are deterministic Python, so they can be tested without an API call.

> The MVP UI speaks German. English is on the roadmap.

## Features

- **Onboarding in chat:** goal, calorie and protein target, meals to plan, cooking time, meal
  prep, diet and meat limits, allergies and dislikes, fixed items (e.g. a daily protein shake),
  flex meals (e.g. fast food once a week), shopping day, pantry staples.
- **Dish import, separate from the chat:** in the "Gerichte" tab, paste a Notion link (a page or a
  whole database), notes, Instagram captions or ready meals with their package size. Claude
  extracts each dish in the background, with ingredients and rough kcal and protein. Every dish
  appears as an editable draft card that you save or discard. The chat can only change existing
  dishes.
- **Sunday planning:** the agent asks about last week and the coming week, builds a plan, checks it
  against your profile and adjusts it in chat. One line per day with rough kcal, plus the weekly
  total against your target.
- **Shopping list:** summed quantities grouped by supermarket department, without pantry staples,
  ready to copy into a note on your phone.
- **Memory:** profile, learned preferences, ratings and past plans live in a local database. You
  can see and delete every memory entry in the UI.

## Setup

Requires Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

On macOS or Linux, activate with `source .venv/bin/activate` and copy with `cp`.

## Authentication

If you are logged in to Claude Code on this machine, there is nothing to configure: the agent
uses that login for your own local use.

Everyone else sets their own `ANTHROPIC_API_KEY` in `.env`. Apps built on the Agent SDK must not
offer a claude.ai login to other people.

The model defaults to `sonnet`. Set `MEAL_PLANNER_MODEL` in `.env` to change it.

## Notion (optional)

1. Create an internal integration at <https://www.notion.so/profile/integrations> with read access
   to content.
2. Share your recipe page or database with the integration.
3. Put the token into `.env` as `NOTION_TOKEN`.

Never paste the token into the chat. Without a token, paste the recipe text into the import field
in the "Gerichte" tab instead.

## Run

```powershell
python -m meal_planner
```

This starts the server on <http://127.0.0.1:8000> and opens the browser. Set `MEAL_PLANNER_PORT`
for another port.

1. The first start runs the onboarding in the chat.
2. Then add your dishes in the "Gerichte" tab: paste a Notion link or the text of your notes,
   check the draft cards and save them.
3. On Sunday, click "Woche planen". When the plan is fine, say so: it becomes final and the
   shopping list appears in the "Einkauf" tab with a copy button.

## Data and privacy

Everything stays on your machine in `data/meal_planner.db`. `data/` and `.env` are in
`.gitignore`. The agent has no access to your files or a shell, only to its own tools and web
search for nutrition values of ready meals. Text from Notion or pasted notes is treated as data,
never as instructions.

> Nutrition values are rough estimates. The allergy check compares ingredient names and is no
> guarantee, so always check product labels.

## Architecture

| File | Purpose |
| --- | --- |
| `src/meal_planner/models.py` | Database tables (SQLModel) and data schemas |
| `src/meal_planner/db.py` | Engine and sessions; SQLite by default, `MEAL_PLANNER_DB_URL` for others |
| `src/meal_planner/services.py` | The one API for agent tools and web routes |
| `src/meal_planner/planning.py` | Calorie and protein totals, plan checks |
| `src/meal_planner/shopping.py` | Shopping list and text export |
| `src/meal_planner/notion.py` | Reads Notion pages and databases through the official API |
| `src/meal_planner/importer.py` | Dish import: Notion or text, extracted by Claude into draft dishes |
| `src/meal_planner/prompts.py` | System prompt and the context built from profile and memory |
| `src/meal_planner/tools.py` | SDK tools, thin wrappers around `services.py` |
| `src/meal_planner/agent.py` | Chat session on top of the Agent SDK, streams events to the UI |
| `src/meal_planner/app.py` | FastAPI app: chat stream and REST endpoints |
| `src/meal_planner/static/` | Frontend (HTML, CSS, vanilla JS, no build step) |

Agent tools and UI routes call the same functions in `services.py`, so chat and clicks share one
logic.

## Test

```powershell
pytest
```

The tests cover the deterministic core and need neither a login nor a network connection.

## Roadmap

See [PLAN.md](PLAN.md) (German): changing the plan during the week by chat and click, an English
UI, screenshot import for Instagram recipes, and an iPhone version.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md). You may use, change and share this code for
personal and other noncommercial purposes. Commercial use is not permitted without separate
permission from the author.
