"""Pure plan calculations: nutrition totals and the plan check rules. No database access.

Slots are Slot rows or unsaved Slot instances (dish, fixed, flex and unplanned). `dishes` maps
dish id to Dish and must contain every dish the slots refer to (archived ones included).
"""

import math
from collections import Counter, defaultdict

from meal_planner.models import MEAL_TYPES, WEEKDAYS, DayTotals, Dish, PlanCheck, Profile, Slot

DAY_INDEX = {day: i for i, day in enumerate(WEEKDAYS)}
MEAL_INDEX = {meal: i for i, meal in enumerate(MEAL_TYPES)}
WEEKEND = ("sat", "sun")
# Protein sources each diet rules out.
DIET_EXCLUDES = {
    "omnivore": set(),
    "pescetarian": {"meat"},
    "vegetarian": {"meat", "fish"},
    "vegan": {"meat", "fish", "vegetarian"},
}


def round_whole(x: float) -> int:
    """Round half up (Python's round() rounds half to even)."""
    return math.floor(x + 0.5)


def round_kcal(kcal: float) -> int:
    """Round to the nearest 50, the precision the plan shows ("~450 kcal")."""
    return round_whole(kcal / 50) * 50


def slot_order(slot: Slot) -> tuple[int, int]:
    """Sort key: day, then meal (breakfast < lunch < dinner < snack)."""
    return DAY_INDEX[slot.day], MEAL_INDEX[slot.meal]


def slot_nutrition(slot: Slot, dishes: dict[int, Dish]) -> tuple[float, float]:
    """Unrounded (kcal, protein_g) the user eats in this slot."""
    if slot.kind in ("dish", "fixed"):
        dish = dishes.get(slot.dish_id)
        if dish is None:
            return 0.0, 0.0
        return dish.kcal_per_serving * slot.servings, dish.protein_g_per_serving * slot.servings
    return float(slot.kcal or 0), 0.0


def day_totals(slots: list[Slot], dishes: dict[int, Dish]) -> list[DayTotals]:
    """All seven days; kcal rounded to 50, protein to whole grams."""
    kcal = dict.fromkeys(WEEKDAYS, 0.0)
    protein = dict.fromkeys(WEEKDAYS, 0.0)
    for slot in slots:
        slot_kcal, slot_protein = slot_nutrition(slot, dishes)
        kcal[slot.day] += slot_kcal
        protein[slot.day] += slot_protein
    return [DayTotals(day=d, kcal=round_kcal(kcal[d]), protein_g=round_whole(protein[d])) for d in WEEKDAYS]


def week_totals(slots: list[Slot], dishes: dict[int, Dish]) -> tuple[int, int]:
    """(kcal rounded to 50, protein in whole grams) of the whole week."""
    nutrition = [slot_nutrition(slot, dishes) for slot in slots]
    return round_kcal(sum(k for k, _ in nutrition)), round_whole(sum(p for _, p in nutrition))


def check_plan(profile: Profile, slots: list[Slot], dishes: dict[int, Dish]) -> PlanCheck:
    """Totals plus errors (the plan is rejected) and warnings (the agent fixes or explains)."""
    slots = sorted(slots, key=slot_order)
    days = day_totals(slots, dishes)
    week_kcal, week_protein = week_totals(slots, dishes)
    week_kcal_target = 7 * profile.kcal_target
    week_protein_target = 7 * profile.protein_target_g if profile.protein_target_g else None
    warnings = (
        _nutrition_warnings(profile, days, week_kcal, week_kcal_target, week_protein, week_protein_target)
        + _dish_warnings(profile, slots, dishes)
        + _coverage_warnings(profile, slots)
    )
    return PlanCheck(
        errors=_errors(profile, slots, dishes),
        warnings=warnings,
        days=days,
        week_kcal=week_kcal,
        week_kcal_target=week_kcal_target,
        week_protein_g=week_protein,
        week_protein_target_g=week_protein_target,
    )


def _errors(profile: Profile, slots: list[Slot], dishes: dict[int, Dish]) -> list[str]:
    terms = [(t.strip(), "allergy") for t in profile.allergies] + [(t.strip(), "dislike") for t in profile.dislikes]
    terms = [(term, reason) for term, reason in terms if term]
    errors: list[str] = []
    checked: set[int] = set()  # dishes whose content was checked already
    cooked: set[int] = set()  # dishes with a cooking slot so far
    for slot in slots:
        if slot.kind not in ("dish", "fixed"):
            continue
        where = f"{slot.day} {slot.meal}"
        dish = dishes.get(slot.dish_id)
        if slot.dish_id is None:
            errors.append(f"{where}: {slot.kind} slot without dish_id.")
            continue
        if dish is None:
            errors.append(f"{where}: dish {slot.dish_id} does not exist.")
            continue
        if dish.archived:
            errors.append(f"{where}: '{dish.name}' (id {dish.id}) is archived.")
            continue
        if slot.kind == "dish":
            if slot.cook:
                cooked.add(dish.id)
            elif dish.id not in cooked:
                errors.append(f"{where}: leftover of '{dish.name}' without an earlier cooking slot.")
        if dish.id in checked:
            continue
        checked.add(dish.id)
        names = [dish.name.casefold()] + [i["name"].casefold() for i in dish.ingredients]
        for term, reason in terms:
            if any(term.casefold() in name for name in names):
                errors.append(f"'{dish.name}' contains '{term}' ({reason}).")
        if dish.protein_source in DIET_EXCLUDES[profile.diet]:
            errors.append(f"'{dish.name}' ({dish.protein_source}) does not fit the {profile.diet} diet.")
    return errors


def _off_target(where: str, value: int, target: int, unit: str, target_name: str) -> str:
    percent = round_whole(abs(value - target) / target * 100)
    direction = "above" if value > target else "below"
    approx = "~" if unit == "kcal" else ""
    return f"{where}: {approx}{value:,} {unit}, {percent}% {direction} the {target_name} of {target:,} {unit}."


def _nutrition_warnings(
    profile: Profile,
    days: list[DayTotals],
    week_kcal: int,
    week_kcal_target: int,
    week_protein: int,
    week_protein_target: int | None,
) -> list[str]:
    warnings = []
    kcal_target = profile.kcal_target
    protein_target = profile.protein_target_g
    if profile.calorie_mode == "daily":
        day_tolerance = 0.15
    else:
        day_tolerance = 0.35
        if abs(week_kcal - week_kcal_target) > 0.10 * week_kcal_target:
            warnings.append(_off_target("week", week_kcal, week_kcal_target, "kcal", "weekly budget"))
    for d in days:
        if abs(d.kcal - kcal_target) > day_tolerance * kcal_target:
            warnings.append(_off_target(d.day, d.kcal, kcal_target, "kcal", "daily target"))
    if protein_target and profile.calorie_mode == "daily":
        for d in days:
            if d.protein_g < 0.9 * protein_target:
                warnings.append(_off_target(d.day, d.protein_g, protein_target, "g protein", "daily target"))
    elif week_protein_target and week_protein < 0.9 * week_protein_target:
        warnings.append(_off_target("week", week_protein, week_protein_target, "g protein", "weekly target"))
    return warnings


def _dish_warnings(profile: Profile, slots: list[Slot], dishes: dict[int, Dish]) -> list[str]:
    warnings = []
    eaten = [(s, dishes[s.dish_id]) for s in slots if s.kind in ("dish", "fixed") and s.dish_id in dishes]

    sources = Counter(dish.protein_source for _, dish in eaten)
    for source, limit in (("meat", profile.max_meat_meals_per_week), ("fish", profile.max_fish_meals_per_week)):
        if limit is not None and sources[source] > limit:
            warnings.append(f"{sources[source]} {source} meals this week, the limit is {limit}.")

    day_indexes: dict[int, set[int]] = defaultdict(set)
    for slot, dish in eaten:
        if slot.kind == "dish":
            day_indexes[dish.id].add(DAY_INDEX[slot.day])
    for dish_id, indexes in day_indexes.items():
        run: list[int] = []
        for i in range(len(WEEKDAYS) + 1):  # the extra index ends the last run
            if i in indexes:
                run.append(i)
                continue
            if len(run) > profile.max_same_dish_days:
                warnings.append(
                    f"'{dishes[dish_id].name}' on {len(run)} days in a row ({WEEKDAYS[run[0]]}-{WEEKDAYS[run[-1]]}), "
                    f"the maximum is {profile.max_same_dish_days}."
                )
            run = []

    for slot, dish in eaten:
        if slot.kind != "dish":
            continue
        where = f"{slot.day} {slot.meal}"
        if slot.cook and dish.prep_minutes:
            weekend = slot.day in WEEKEND
            limit = profile.cooking_minutes_weekend if weekend else profile.cooking_minutes_weekday
            if dish.prep_minutes > limit:
                warnings.append(
                    f"{where}: '{dish.name}' takes {dish.prep_minutes} min, "
                    f"the {'weekend' if weekend else 'weekday'} limit is {limit} min."
                )
        if dish.meal_types and slot.meal not in dish.meal_types:
            warnings.append(f"{where}: '{dish.name}' is not meant for {slot.meal} ({', '.join(dish.meal_types)}).")
    return warnings


def _coverage_warnings(profile: Profile, slots: list[Slot]) -> list[str]:
    warnings = []
    for flex in profile.flex_meals:
        label = flex.label.strip().casefold()
        count = sum(1 for slot in slots if slot.kind == "flex" and slot.label.strip().casefold() == label)
        if count != flex.per_week:
            warnings.append(f"'{flex.label}': {count} flex slots, the profile says {flex.per_week} per week.")
    taken = {(slot.day, slot.meal) for slot in slots}
    for day in WEEKDAYS:
        missing = [meal for meal in profile.meals if (day, meal) not in taken]
        if missing:
            warnings.append(f"{day}: nothing planned for {', '.join(missing)}.")
    return warnings
