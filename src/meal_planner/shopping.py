"""Pure shopping-list calculations: split into trips, scale, merge, filter, round and format.
No database access."""

import math
from datetime import date, timedelta

from meal_planner import texts
from meal_planner.models import (
    DEPARTMENTS,
    WEEKDAYS,
    Department,
    Dish,
    Ingredient,
    ShoppingItem,
    ShoppingTrip,
    Slot,
    Weekday,
)
from meal_planner.planning import slot_order

# Units bought in whole numbers: amounts are rounded up. "" means pieces ("2 Eier").
WHOLE_UNITS = {
    "", "stk", "stück", "dose", "dosen", "packung", "packungen", "pck", "bund", "beutel",
    "glas", "gläser", "becher", "flasche", "flaschen", "kopf", "köpfe", "zehe", "zehen", "scheibe",
    "scheiben", "knolle", "knollen", "tüte", "tüten",
}


def round_amount(amount: float | None, unit: str) -> float | None:
    """g and ml round up to whole numbers (steps of 10 from 100 on), whole units up to whole
    numbers, all others to one decimal."""
    if amount is None:
        return None
    if unit.strip().casefold() in {"g", "ml"}:
        step = 10 if amount >= 100 else 1
        return float(math.ceil(round(amount / step, 6)) * step)
    if unit.strip().casefold() in WHOLE_UNITS:
        return float(math.ceil(round(amount, 6)))  # round() first: 3 x 1/3 must stay 1
    return math.floor(amount * 10 + 0.5) / 10


def build_items(
    slots: list[Slot], dishes: dict[int, Dish], household_size: int, exclude: list[str]
) -> tuple[dict[Department, list[ShoppingItem]], list[str]]:
    """Items per department (DEPARTMENTS order, empty ones left out) and the skipped ingredient names.

    Only "dish" and "fixed" slots count: amount / dish.servings * slot.servings, times
    household_size for "dish" slots. Same name and unit (case-insensitive) are summed. An
    ingredient is skipped if an `exclude` term (pantry staples, have_at_home) is part of its name.
    """
    terms = [t.strip().casefold() for t in exclude if t.strip()]
    merged: dict[tuple[str, str], ShoppingItem] = {}
    skipped: dict[str, str] = {}
    for slot in slots:
        dish = dishes.get(slot.dish_id)
        if slot.kind not in ("dish", "fixed") or dish is None:
            continue
        factor = slot.servings / dish.servings * (household_size if slot.kind == "dish" else 1)
        for raw in dish.ingredients:
            ingredient = Ingredient.model_validate(raw)
            name = ingredient.name.strip()
            if any(term in name.casefold() for term in terms):
                skipped.setdefault(name.casefold(), name)
                continue
            unit = ingredient.unit.strip()
            amount = None if ingredient.amount is None else ingredient.amount * factor
            key = (name.casefold(), unit.casefold())
            item = merged.get(key)
            if item is None:
                merged[key] = ShoppingItem(name=name, amount=amount, unit=unit, department=ingredient.department)
            elif amount is not None:
                item.amount = (item.amount or 0) + amount

    departments: dict[Department, list[ShoppingItem]] = {}
    for department in DEPARTMENTS:
        items = sorted((i for i in merged.values() if i.department == department), key=lambda i: i.name.casefold())
        for item in items:
            item.amount = round_amount(item.amount, item.unit)
        if items:
            departments[department] = items
    return departments, list(skipped.values())


def format_number(x: float) -> str:
    """German format: 1.5 -> "1,5", 2.0 -> "2"."""
    return str(int(x)) if x == int(x) else f"{x:.1f}".replace(".", ",")


def to_text(departments: dict[Department, list[ShoppingItem]], title: str, language: str = "de") -> str:
    """Plain text to copy, e.g. into an iPhone note."""
    labels = texts.labels(language)
    lines = [title]
    if not departments:
        lines += ["", labels["shopping"]["nothing"]]
    for department, items in departments.items():
        lines += ["", labels["departments"][department]]
        for item in items:
            if item.amount is None:
                lines.append(f"- {item.name} ({labels['shopping']['as_needed']})")
            else:
                lines.append("- " + " ".join(p for p in (format_number(item.amount), item.unit, item.name) if p))
    return "\n".join(lines)


def trip_days(shopping_days: list[Weekday]) -> dict[Weekday, list[Weekday]]:
    """Shopping day -> the days it buys for, in week order: its own day up to the day before the
    next shopping day. Days before the first shopping day belong to the first trip. No shopping
    days: one trip on Monday."""
    starts = sorted(set(shopping_days), key=WEEKDAYS.index) or ["mon"]
    trips: dict[Weekday, list[Weekday]] = {day: [] for day in starts}
    current = starts[0]
    for day in WEEKDAYS:
        if day in trips:
            current = day
        trips[current].append(day)
    return trips


def split_by_trip(slots: list[Slot], shopping_days: list[Weekday]) -> dict[Weekday, list[Slot]]:
    """Slots per shopping day. A leftover slot (cook=False) goes with the latest earlier cooking
    of the same dish, because its ingredients are bought for that cooking."""
    trips = trip_days(shopping_days)
    trip_of = {day: trip for trip, days in trips.items() for day in days}
    groups: dict[Weekday, list[Slot]] = {trip: [] for trip in trips}
    cooked_in: dict[int, Weekday] = {}  # dish id -> trip of its latest cooking so far
    for slot in sorted(slots, key=slot_order):
        trip = trip_of[slot.day]
        if slot.kind == "dish" and slot.dish_id is not None:
            if slot.cook:
                cooked_in[slot.dish_id] = trip
            else:
                trip = cooked_in.get(slot.dish_id, trip)
        groups[trip].append(slot)
    return groups


def build_trips(
    slots: list[Slot],
    dishes: dict[int, Dish],
    household_size: int,
    exclude: list[str],
    shopping_days: list[Weekday],
    week_start: date,
    language: str = "de",
) -> tuple[list[ShoppingTrip], list[str]]:
    """One shopping list per trip (see build_items) and the skipped ingredient names of all trips.

    A single trip keeps the plain title "Einkaufsliste ab <week_start>"; several trips name their
    day, date and the days they buy for.
    """
    labels = texts.labels(language)
    days_of = trip_days(shopping_days)
    trips: list[ShoppingTrip] = []
    skipped: dict[str, str] = {}
    for day, trip_slots in split_by_trip(slots, shopping_days).items():
        departments, trip_skipped = build_items(trip_slots, dishes, household_size, exclude)
        for name in trip_skipped:
            skipped.setdefault(name.casefold(), name)
        shopping_date = week_start + timedelta(days=WEEKDAYS.index(day))
        days = days_of[day]
        if len(days_of) == 1:
            title = labels["shopping"]["title"].format(date=week_start.strftime("%d.%m.%Y"))
        else:
            short = labels["weekdays_short"]
            title = labels["shopping"]["trip_title"].format(
                weekday=labels["weekdays"][day],
                date=shopping_date.strftime("%d.%m.%Y"),
                days=short[days[0]] if len(days) == 1 else f"{short[days[0]]}–{short[days[-1]]}",
            )
        trips.append(
            ShoppingTrip(
                day=day,
                shopping_date=shopping_date,
                days=days,
                departments=departments,
                text=to_text(departments, title, language),
            )
        )
    return trips, list(skipped.values())
