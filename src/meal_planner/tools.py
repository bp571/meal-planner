"""In-process SDK MCP tools for the agent: thin wrappers around services.py.

Each tool validates its input with pydantic, runs in its own database session and returns JSON
text. Rejections (ServiceError, ValidationError) come back as error results the agent can read
and fix. Write tools report the UI areas they changed through `on_event`.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from claude_agent_sdk import SdkMcpTool, tool
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session

from meal_planner import db, services
from meal_planner.models import DishIn, Profile, SlotIn

SERVER_NAME = "meal_planner"
OnEvent = Callable[[dict[str, Any]], None]


# --- Tool arguments ---


class RememberArgs(BaseModel):
    text: str = Field(min_length=1, description="One durable fact, short, in the user's language")
    category: Literal["preference", "dislike", "habit", "feedback", "other"] = "other"


class MemoryIdArgs(BaseModel):
    memory_id: int


class DishIdArgs(BaseModel):
    dish_id: int


class UpdateDishArgs(BaseModel):
    dish_id: int
    dish: DishIn = Field(description="The complete dish with the changes")


class DishArgs(BaseModel):
    dish: DishIn


class RateDishArgs(BaseModel):
    dish_id: int
    score: int = Field(ge=1, le=5, description="1 = never again, 5 = great")
    comment: str = ""
    plan_id: int | None = Field(None, description="The plan the dish was eaten in")


class PlanDraftArgs(BaseModel):
    slots: list[SlotIn]
    week_start: date | None = Field(None, description="A Monday; default: next week")
    have_at_home: list[str] = Field([], description="Ingredients still at home this week")


class UpdateSlotArgs(BaseModel):
    slot_id: int
    change: SlotIn = Field(description="The complete new slot")


class PlanIdArgs(BaseModel):
    plan_id: int | None = Field(None, description="Default: the current plan")


# --- Helpers ---


def _default(value: Any) -> Any:
    if isinstance(value, BaseModel):  # SQLModel tables are pydantic models too
        return value.model_dump(mode="json")
    if isinstance(value, date):  # datetime included
        return value.isoformat()
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_default)


def compact_dishes(session: Session) -> list[dict[str, Any]]:
    """The active library as the agent sees it: key facts and stats per dish."""
    stats = services.dish_stats(session)
    return [
        {
            "id": dish.id,
            "name": dish.name,
            "kcal": dish.kcal_per_serving,
            "protein": dish.protein_g_per_serving,
            "protein_source": dish.protein_source,
            "meal_types": dish.meal_types,
            "prep_minutes": dish.prep_minutes,
            "meal_prep": dish.meal_prep,
            "avg_score": stats[dish.id].avg_score,
            "last_planned": stats[dish.id].last_planned,
        }
        for dish in services.list_dishes(session)
    ]


def _error(errors: list[str]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": to_json({"errors": errors})}], "is_error": True}


def _problems(error: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, p['loc'])) or 'input'}: {p['msg']}" for p in error.errors()]


# --- Tools ---


def build_tools(on_event: OnEvent) -> list[SdkMcpTool]:
    """Create the tools. `on_event` receives {"type": "changed" | "memory", ...} after writes."""

    def handled(*areas: str):
        """Turn fn(session, args) -> payload into an SDK handler that reports rejections."""

        def decorate(fn: Callable[[Session, dict[str, Any]], Any]):
            def run(args: dict[str, Any]) -> str:
                with db.session_scope() as session:
                    return to_json(fn(session, args))

            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                try:
                    # A worker thread keeps the web server responsive during database work.
                    text = await asyncio.to_thread(run, args)
                except services.ServiceError as error:
                    return _error(error.errors)
                except ValidationError as error:
                    return _error(_problems(error))
                if areas:
                    on_event({"type": "changed", "areas": list(areas)})
                return {"content": [{"type": "text", "text": text}]}

            return handler

        return decorate

    @tool("get_profile", "Read the saved profile (null if there is none yet).", {})
    @handled()
    def get_profile(session, _args):
        return {"profile": services.get_profile(session)}

    @tool(
        "save_profile",
        "Replace the whole profile. Always send every field: start from the current profile and "
        "change only what the user changed. Fixed items must point to existing dishes.",
        Profile.model_json_schema(),
    )
    @handled("profile", "plan", "shopping")
    def save_profile(session, args):
        return services.save_profile(session, Profile.model_validate(args))

    @tool(
        "remember",
        "Store a durable fact about the user, e.g. a preference, habit or feedback. "
        "The app shows it to the user as 'Gemerkt: ...'.",
        RememberArgs.model_json_schema(),
    )
    @handled("memory")
    def remember(session, args):
        request = RememberArgs.model_validate(args)
        entry = services.add_memory(session, request.text, request.category)
        on_event({"type": "memory", "text": entry.text})
        return entry

    @tool("forget", "Delete a memory entry by id.", MemoryIdArgs.model_json_schema())
    @handled("memory")
    def forget(session, args):
        request = MemoryIdArgs.model_validate(args)
        services.delete_memory(session, request.memory_id)
        return {"deleted": request.memory_id}

    @tool(
        "list_dishes",
        "List the active library: id, name, kcal and protein per serving, tags, average rating "
        "and the week it was last planned.",
        {},
    )
    @handled()
    def list_dishes(session, _args):
        return {"dishes": compact_dishes(session)}

    @tool("get_dish", "Read one dish with all ingredients.", DishIdArgs.model_json_schema())
    @handled()
    def get_dish(session, args):
        return services.get_dish(session, DishIdArgs.model_validate(args).dish_id)

    @tool(
        "update_dish",
        "Change an existing dish: send the complete dish (start from get_dish). Ingredient amounts "
        "are for the whole recipe, which yields `servings` servings; kcal and protein are per "
        "serving. kcal are stored rounded to 50; the stored dish is returned.",
        UpdateDishArgs.model_json_schema(),
    )
    @handled("dishes", "plan", "shopping")
    def update_dish(session, args):
        request = UpdateDishArgs.model_validate(args)
        return services.save_dish(session, request.dish, request.dish_id)

    @tool(
        "create_fixed_item_dish",
        "Create a dish only for a fixed item of the profile, e.g. a daily protein shake. All other "
        "dishes are added by the user in the tab 'Gerichte'. kcal and protein are per serving. "
        "Returns the stored dish; reference its id in fixed_items.",
        DishArgs.model_json_schema(),
    )
    @handled("dishes", "plan", "shopping")
    def create_fixed_item_dish(session, args):
        return services.save_dish(session, DishArgs.model_validate(args).dish)

    @tool(
        "archive_dish",
        "Remove a dish from the library. Ratings and past plans keep it.",
        DishIdArgs.model_json_schema(),
    )
    @handled("dishes", "plan", "shopping")
    def archive_dish(session, args):
        request = DishIdArgs.model_validate(args)
        services.archive_dish(session, request.dish_id)
        return {"archived": request.dish_id}

    @tool(
        "rate_dish",
        "Save the user's rating of a dish, ideally with the plan it was eaten in.",
        RateDishArgs.model_json_schema(),
    )
    @handled("dishes")
    def rate_dish(session, args):
        request = RateDishArgs.model_validate(args)
        return services.rate_dish(
            session, request.dish_id, request.score, request.comment, request.plan_id
        )

    @tool(
        "save_plan_draft",
        "Check and save a whole week as a draft (replaces the draft of that week). One slot per "
        "planned day and meal. kind 'dish' needs dish_id; servings are the user's portions. Meal "
        "prep: the cooking slot has cook=true, later slots with the same dish have cook=false. "
        "kind 'flex' with the label of a profile flex meal (its kcal are added); kind 'unplanned' "
        "for one-off meals out, with label and rough kcal. Fixed items and recurring unplanned "
        "meals of the profile are added automatically. Errors reject the plan and save nothing; "
        "warnings come back in check.warnings. Returns the plan with slot ids and totals.",
        PlanDraftArgs.model_json_schema(),
    )
    @handled("plan", "shopping")
    def save_plan_draft(session, args):
        request = PlanDraftArgs.model_validate(args)
        return services.save_plan_draft(
            session, request.slots, request.week_start, request.have_at_home
        )

    @tool(
        "update_slot",
        "Replace one slot of a plan by its id and re-check the plan. Fixed slots cannot be "
        "changed. If the plan then has any error, nothing changes: send the whole week with "
        "save_plan_draft instead. Returns the updated plan.",
        UpdateSlotArgs.model_json_schema(),
    )
    @handled("plan", "shopping")
    def update_slot(session, args):
        request = UpdateSlotArgs.model_validate(args)
        return services.update_slot(session, request.slot_id, request.change)

    @tool(
        "finalize_plan",
        "Mark a plan as final after the user confirmed it. Its shopping list is then ready.",
        PlanIdArgs.model_json_schema(),
    )
    @handled("plan", "shopping")
    def finalize_plan(session, args):
        return services.finalize_plan(session, PlanIdArgs.model_validate(args).plan_id)

    @tool(
        "get_plan",
        "Read a plan with slot ids, per-day totals and check results (null if there is none).",
        PlanIdArgs.model_json_schema(),
    )
    @handled()
    def get_plan(session, args):
        return services.get_plan_view(session, PlanIdArgs.model_validate(args).plan_id)

    @tool(
        "get_shopping_list",
        "Read the shopping list of a plan: one list per shopping trip (profile shopping_days), "
        "by department, plus the copyable text (null if there is no plan).",
        PlanIdArgs.model_json_schema(),
    )
    @handled()
    def get_shopping_list(session, args):
        return services.get_shopping_list(session, PlanIdArgs.model_validate(args).plan_id)

    return [
        get_profile,
        save_profile,
        remember,
        forget,
        list_dishes,
        get_dish,
        update_dish,
        create_fixed_item_dish,
        archive_dish,
        rate_dish,
        save_plan_draft,
        update_slot,
        finalize_plan,
        get_plan,
        get_shopping_list,
    ]
