"""The one API for agent tools and web routes: database access plus orchestration.

Agent tools and UI routes call these functions and never touch tables directly, so chat and
clicks share one logic. Every function takes an open session (see db.session_scope). Pure
calculations live in planning.py and shopping.py.

CONTRACT: the signatures below are used by tools.py and app.py. Do not change them without
updating both callers.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, col, select

from meal_planner import planning, shopping
from meal_planner.models import (
    Dish,
    DishDraft,
    DishIn,
    DishStats,
    DraftView,
    MemoryEntry,
    PlanView,
    Profile,
    ProfileRecord,
    Rating,
    ShoppingList,
    Slot,
    SlotIn,
    SlotView,
    WeekPlan,
)


class ServiceError(Exception):
    """A request the caller can fix. `errors` are short English sentences for the agent or the UI."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def next_week_start(today: date) -> date:
    """The Monday a plan made today is for: today if it is a Monday, else the next Monday."""
    return today + timedelta(days=(7 - today.weekday()) % 7)


def _now() -> datetime:
    """Timestamps must be timezone-aware: SQLModel rejects naive datetimes (the models' defaults are naive)."""
    return datetime.now(timezone.utc)


# --- Profile ---


def get_profile(session: Session) -> Profile | None:
    record = session.get(ProfileRecord, 1)
    return Profile.model_validate(record.data) if record else None


def save_profile(session: Session, profile: Profile) -> Profile:
    """Replace the whole profile. ServiceError if a fixed item points to a missing or archived dish."""
    errors = []
    for item in profile.fixed_items:
        dish = session.get(Dish, item.dish_id)
        if dish is None or dish.archived:
            errors.append(f"Fixed item: dish {item.dish_id} does not exist or is archived.")
    if errors:
        raise ServiceError(errors)
    record = session.get(ProfileRecord, 1) or ProfileRecord(id=1, data={})
    record.data = profile.model_dump(mode="json")
    record.updated_at = _now()
    session.add(record)
    session.flush()
    return profile


def _require_profile(session: Session) -> Profile:
    profile = get_profile(session)
    if profile is None:
        raise ServiceError(["There is no profile yet. Complete the onboarding first."])
    return profile


# --- Memory ---


def list_memories(session: Session) -> list[MemoryEntry]:
    """Oldest first."""
    return list(session.exec(select(MemoryEntry).order_by(MemoryEntry.created_at, MemoryEntry.id)))


def add_memory(session: Session, text: str, category: str = "other", source: str = "chat") -> MemoryEntry:
    """ServiceError for empty text. An identical text (case-insensitive) returns the existing entry."""
    text = text.strip()
    if not text:
        raise ServiceError(["The memory text is empty."])
    for entry in list_memories(session):
        if entry.text.casefold() == text.casefold():
            return entry
    entry = MemoryEntry(text=text, category=category, source=source, created_at=_now())
    session.add(entry)
    session.flush()
    return entry


def delete_memory(session: Session, memory_id: int) -> None:
    """ServiceError if the entry does not exist."""
    entry = session.get(MemoryEntry, memory_id)
    if entry is None:
        raise ServiceError([f"Memory {memory_id} does not exist."])
    session.delete(entry)
    session.flush()


# --- Dishes ---


def list_dishes(session: Session, include_archived: bool = False) -> list[Dish]:
    """Sorted by name."""
    dishes = session.exec(select(Dish)).all()
    return sorted((d for d in dishes if include_archived or not d.archived), key=lambda d: d.name.casefold())


def get_dish(session: Session, dish_id: int) -> Dish:
    """ServiceError if missing."""
    dish = session.get(Dish, dish_id)
    if dish is None:
        raise ServiceError([f"Dish {dish_id} does not exist."])
    return dish


def save_dish(session: Session, data: DishIn, dish_id: int | None = None) -> Dish:
    """Create (dish_id None) or update a dish.

    Rounds kcal_per_serving to the nearest 50 (values above 0 become at least 50) and protein to
    whole grams. ServiceError if another active dish has the same name (case-insensitive) or if
    dish_id does not exist.
    """
    if dish_id is not None:
        dish = get_dish(session, dish_id)
    else:
        dish = Dish(kcal_per_serving=0, protein_g_per_serving=0, created_at=_now())
    name = data.name.strip()
    for other in list_dishes(session):
        if other.id != dish_id and other.name.casefold() == name.casefold():
            raise ServiceError([f"A dish named '{other.name}' already exists (id {other.id})."])
    values = data.model_dump(mode="json")  # fresh lists, so JSON columns are reassigned, not mutated
    values["name"] = name
    kcal = planning.round_kcal(data.kcal_per_serving)
    values["kcal_per_serving"] = max(kcal, 50) if data.kcal_per_serving > 0 else 0
    values["protein_g_per_serving"] = planning.round_whole(data.protein_g_per_serving)
    for field, value in values.items():
        setattr(dish, field, value)
    dish.updated_at = _now()
    session.add(dish)
    session.flush()
    return dish


def archive_dish(session: Session, dish_id: int) -> None:
    """Soft delete. ServiceError if missing or if the profile uses it as a fixed item."""
    dish = get_dish(session, dish_id)
    profile = get_profile(session)
    if profile and any(item.dish_id == dish_id for item in profile.fixed_items):
        raise ServiceError([f"'{dish.name}' is a fixed item in the profile. Remove it there first."])
    dish.archived = True
    dish.updated_at = _now()
    session.add(dish)
    session.flush()


# --- Dish drafts (from the import, saved or discarded by the user) ---


def _draft_views(session: Session, drafts: list[DishDraft]) -> list[DraftView]:
    active = {dish.name.casefold(): dish.id for dish in list_dishes(session)}
    views = []
    for draft in drafts:
        dish = DishIn.model_validate(draft.data)
        duplicate_of = active.get(dish.name.strip().casefold())
        views.append(DraftView(id=draft.id, dish=dish, source=draft.source, duplicate_of=duplicate_of))
    return views


def list_drafts(session: Session) -> list[DraftView]:
    """Oldest first."""
    drafts = session.exec(select(DishDraft).order_by(DishDraft.created_at, DishDraft.id)).all()
    return _draft_views(session, list(drafts))


def add_drafts(session: Session, dishes: list[DishIn], source: str) -> list[DraftView]:
    drafts = [DishDraft(data=dish.model_dump(mode="json"), source=source) for dish in dishes]
    session.add_all(drafts)
    session.flush()
    return _draft_views(session, drafts)


def _get_draft(session: Session, draft_id: int) -> DishDraft:
    draft = session.get(DishDraft, draft_id)
    if draft is None:
        raise ServiceError([f"Draft {draft_id} does not exist."])
    return draft


def save_draft(session: Session, draft_id: int, data: DishIn, replace: bool = False) -> Dish:
    """Save the (possibly edited) data of a draft as a dish and delete the draft.

    replace=True updates the active dish with the same name instead of creating one. ServiceError
    if the draft does not exist, or if the name is taken and replace is False.
    """
    draft = _get_draft(session, draft_id)
    name = data.name.strip().casefold()
    existing = next((d.id for d in list_dishes(session) if d.name.casefold() == name), None)
    dish = save_dish(session, data, existing if replace else None)
    session.delete(draft)
    session.flush()
    return dish


def discard_draft(session: Session, draft_id: int) -> None:
    """ServiceError if the draft does not exist."""
    session.delete(_get_draft(session, draft_id))
    session.flush()


# --- Ratings ---


def rate_dish(
    session: Session, dish_id: int, score: int, comment: str = "", plan_id: int | None = None
) -> Rating:
    """score 1..5. ServiceError for a missing dish or a score out of range."""
    get_dish(session, dish_id)
    if not 1 <= score <= 5:
        raise ServiceError([f"Score {score} is out of range (1 to 5)."])
    rating = Rating(dish_id=dish_id, plan_id=plan_id, score=score, comment=comment.strip(), created_at=_now())
    session.add(rating)
    session.flush()
    return rating


def dish_stats(session: Session) -> dict[int, DishStats]:
    """Per dish id: average rating, number of ratings, how often and when it was last planned.

    Counts only final plans as "planned". Dishes without ratings or plans are included too.
    """
    scores: dict[int, list[int]] = defaultdict(list)
    for rating in session.exec(select(Rating)):
        scores[rating.dish_id].append(rating.score)
    weeks: dict[int, set[date]] = defaultdict(set)  # weeks in which the dish is in a "dish" slot
    planned = select(Slot.dish_id, WeekPlan.week_start).where(
        Slot.plan_id == WeekPlan.id, WeekPlan.status == "final", Slot.kind == "dish"
    )
    for dish_id, week_start in session.exec(planned):
        weeks[dish_id].add(week_start)
    return {
        dish.id: DishStats(
            dish_id=dish.id,
            avg_score=round(sum(scores[dish.id]) / len(scores[dish.id]), 1) if scores[dish.id] else None,
            ratings=len(scores[dish.id]),
            times_planned=len(weeks[dish.id]),
            last_planned=max(weeks[dish.id], default=None),
        )
        for dish in session.exec(select(Dish))
    }


# --- Week plans ---


def _new_slot(data: SlotIn, profile: Profile) -> Slot:
    """An unsaved slot; a flex slot without kcal takes the kcal of the profile flex meal with that label."""
    slot = Slot(**data.model_dump())
    if slot.kind == "flex" and slot.kcal is None:
        label = slot.label.strip().casefold()
        slot.kcal = next((f.kcal for f in profile.flex_meals if f.label.strip().casefold() == label), 0)
    return slot


def _plan_slots(session: Session, plan_id: int) -> list[Slot]:
    """Ordered by day, then meal."""
    slots = session.exec(select(Slot).where(Slot.plan_id == plan_id).order_by(Slot.id)).all()
    return sorted(slots, key=planning.slot_order)


def _dishes_for(session: Session, slots: list[Slot]) -> dict[int, Dish]:
    ids = {slot.dish_id for slot in slots if slot.dish_id is not None}
    return {dish.id: dish for dish in session.exec(select(Dish).where(col(Dish.id).in_(ids)))}


def _get_plan(session: Session, plan_id: int | None) -> WeekPlan | None:
    return session.get(WeekPlan, plan_id) if plan_id is not None else get_current_plan(session)


def _view(session: Session, plan: WeekPlan, profile: Profile) -> PlanView:
    slots = _plan_slots(session, plan.id)
    dishes = _dishes_for(session, slots)
    views: list[SlotView] = []
    cooking: dict[int, SlotView] = {}  # dish id -> its latest cooking slot so far
    for slot in slots:
        dish = dishes.get(slot.dish_id)
        kcal, protein = planning.slot_nutrition(slot, dishes)
        view = SlotView(
            id=slot.id,
            day=slot.day,
            meal=slot.meal,
            kind=slot.kind,
            dish_id=slot.dish_id,
            name=dish.name if dish else slot.label,
            kcal=planning.round_kcal(kcal),
            protein_g=planning.round_whole(protein),
            servings=slot.servings,
            cook=slot.cook,
        )
        if slot.kind == "dish" and dish is not None:
            if slot.cook:
                cooking[dish.id] = view
            elif dish.id in cooking:
                cooked = cooking[dish.id]
                view.cooked_on = cooked.day
                if slot.day != cooked.day and slot.day not in cooked.covers:
                    cooked.covers = cooked.covers + [slot.day]
        views.append(view)
    return PlanView(
        plan_id=plan.id,
        week_start=plan.week_start,
        status=plan.status,
        slots=views,
        check=planning.check_plan(profile, slots, dishes),
        have_at_home=list(plan.have_at_home),
    )


def save_plan_draft(
    session: Session,
    slots: list[SlotIn],
    week_start: date | None = None,
    have_at_home: list[str] | None = None,
) -> PlanView:
    """Check and save a whole week as a draft and return its view.

    - week_start defaults to next_week_start(date.today()). Must be a Monday.
    - Replaces any existing plan for that week (it becomes a draft again).
    - Adds a "fixed" slot for every profile.fixed_items entry and day, and an "unplanned" slot
      for every profile.unplanned_meals entry and day that the caller did not send already.
    - A flex slot without kcal takes the kcal of the profile flex meal with the same label
      (case-insensitive), else 0.
    - Runs planning.check_plan. If it returns errors, raises ServiceError and saves nothing.
      Warnings are returned in view.check.warnings.
    - ServiceError if there is no profile.
    """
    profile = _require_profile(session)
    week_start = week_start or next_week_start(date.today())
    if week_start.weekday() != 0:
        raise ServiceError([f"week_start {week_start.isoformat()} is not a Monday."])

    new_slots = [_new_slot(s, profile) for s in slots]
    sent = {(slot.day, slot.meal) for slot in new_slots}  # any sent slot replaces an unplanned meal
    for item in profile.fixed_items:
        new_slots += [
            Slot(day=day, meal=item.meal, kind="fixed", dish_id=item.dish_id, servings=item.servings)
            for day in item.days
        ]
    for meal in profile.unplanned_meals:
        new_slots += [
            Slot(day=day, meal=meal.meal, kind="unplanned", label=meal.label, kcal=meal.kcal)
            for day in meal.days
            if (day, meal.meal) not in sent
        ]
    check = planning.check_plan(profile, new_slots, _dishes_for(session, new_slots))
    if check.errors:
        raise ServiceError(check.errors)

    plan = session.exec(select(WeekPlan).where(WeekPlan.week_start == week_start)).first()
    if plan is None:
        plan = WeekPlan(week_start=week_start, created_at=_now())
        session.add(plan)
        session.flush()
    else:  # keep the row, ratings may point to it
        for old in _plan_slots(session, plan.id):
            session.delete(old)
        plan.status = "draft"
        plan.finalized_at = None
    if have_at_home is not None:
        plan.have_at_home = [item.strip() for item in have_at_home if item.strip()]
    for slot in new_slots:
        slot.plan_id = plan.id
        session.add(slot)
    session.add(plan)
    session.flush()
    return _view(session, plan, profile)


def update_slot(session: Session, slot_id: int, change: SlotIn) -> PlanView:
    """Replace one slot of a plan, re-check the whole plan and return its view.

    Errors from the check raise ServiceError and change nothing. The plan status stays as it is.
    ServiceError if the slot does not exist or is a fixed slot.
    """
    slot = session.get(Slot, slot_id)
    if slot is None:
        raise ServiceError([f"Slot {slot_id} does not exist."])
    if slot.kind == "fixed":
        raise ServiceError([f"Slot {slot_id} is a fixed item from the profile and cannot be changed."])
    profile = _require_profile(session)
    new = _new_slot(change, profile)
    slots = [s for s in _plan_slots(session, slot.plan_id) if s.id != slot_id] + [new]
    check = planning.check_plan(profile, slots, _dishes_for(session, slots))
    if check.errors:
        raise ServiceError(check.errors)
    for field, value in change.model_dump().items():
        setattr(slot, field, value)
    slot.kcal = new.kcal
    session.add(slot)
    session.flush()
    return _view(session, session.get(WeekPlan, slot.plan_id), profile)


def finalize_plan(session: Session, plan_id: int | None = None) -> PlanView:
    """Mark the plan (default: the current plan) as final. ServiceError if there is none."""
    plan = _get_plan(session, plan_id)
    if plan is None:
        raise ServiceError(["There is no plan to finalize."])
    plan.status = "final"
    plan.finalized_at = _now()
    session.add(plan)
    session.flush()
    return _view(session, plan, _require_profile(session))


def get_current_plan(session: Session) -> WeekPlan | None:
    """The plan with the latest week_start."""
    return session.exec(select(WeekPlan).order_by(col(WeekPlan.week_start).desc(), col(WeekPlan.id).desc())).first()


def get_plan_view(session: Session, plan_id: int | None = None) -> PlanView | None:
    """The view of a plan (default: the current plan), or None if there is no plan."""
    plan = _get_plan(session, plan_id)
    return _view(session, plan, _require_profile(session)) if plan else None


def recent_plans(session: Session, limit: int = 3) -> list[PlanView]:
    """The latest plans, newest first."""
    plans = session.exec(select(WeekPlan).order_by(col(WeekPlan.week_start).desc()).limit(limit)).all()
    if not plans:
        return []
    profile = _require_profile(session)
    return [_view(session, plan, profile) for plan in plans]


def get_shopping_list(session: Session, plan_id: int | None = None) -> ShoppingList | None:
    """The shopping list of a plan (default: the current plan), or None if there is no plan.

    Sums the ingredients of all "dish" and "fixed" slots: amount / dish.servings * slot.servings,
    times profile.household_size for "dish" slots only. Leaves out pantry staples and the plan's
    have_at_home items. Text labels come from texts.labels(profile.language).
    """
    plan = _get_plan(session, plan_id)
    if plan is None:
        return None
    profile = _require_profile(session)
    slots = _plan_slots(session, plan.id)
    departments, skipped = shopping.build_items(
        slots, _dishes_for(session, slots), profile.household_size, profile.pantry_staples + plan.have_at_home
    )
    return ShoppingList(
        plan_id=plan.id,
        week_start=plan.week_start,
        departments=departments,
        skipped=skipped,
        text=shopping.to_text(departments, plan.week_start, profile.language),
    )
