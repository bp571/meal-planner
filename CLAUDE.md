# CLAUDE.md

Meal-planning agent with a local web UI (Claude Agent SDK + FastAPI + SQLite). Public repo:
nothing user-specific belongs in code, everything comes from the onboarding and the profile.
Roadmap and product decisions: PLAN.md (German).

## Commands

- Install: `python -m venv .venv` then `.venv/Scripts/python -m pip install -e ".[dev]"`
- Run: `.venv/Scripts/python -m meal_planner` (http://127.0.0.1:8000)
- Test: `.venv/Scripts/python -m pytest -q`

## Architecture rules

- `services.py` is the single API. Agent tools (`tools.py`) and web routes (`app.py`) call it and
  never touch tables directly, so chat and clicks share one logic.
- The LLM decides, Python calculates: kcal/protein totals, plan checks (`planning.py`) and the
  shopping list (`shopping.py`) are deterministic and unit-tested. The agent never computes them.
- Memory is structured data in the DB (profile, memory entries, ratings, past plans), not chat
  transcripts. The system prompt context is built from it at session start (`prompts.py`).
- Data keys are English ids (weekdays, meals, departments). Display labels: `texts.py` for
  server-generated text, `static/texts.js` for the UI. UI language is German for the MVP.
- Profile is stored as JSON (`ProfileRecord`), so new profile fields need no migration. Dishes are
  soft-deleted (`archived`) to keep ratings and past plans intact.
- The agent gets only its own MCP tools plus WebSearch/WebFetch: no file or shell access.
- New dishes come only through the import area (`importer.py` → `DishDraft` → the user saves
  the card). The chat can change existing dishes and create fixed-item dishes (e.g. a protein
  shake), nothing else.

## Conventions

- UI theme: a meal-planner pad on a violet desk, printed like a two-ink risograph. Print violet +
  condensed face for everything pre-printed (labels, rules, fixed items), graphite + Vollkorn for
  everything written in (dishes, amounts). Pink is the second ink (title misregistration, today,
  flex meals, tape). Highlighter = cooking for several days, ditto mark = leftovers, red only for
  errors. Tokens at the top of `styles.css`; fonts are self-hosted in `static/fonts/` (OFL).
- Code, comments and README in English.
- Tests only for the deterministic core; no tests that call the model.
- `data/` and `.env` stay out of git.
