"""Pure shopping-list calculations: scale, merge, filter, round and format. No database access."""

import math
from datetime import date

from meal_planner import texts
from meal_planner.models import DEPARTMENTS, Department, Dish, Ingredient, ShoppingItem, Slot

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


def to_text(departments: dict[Department, list[ShoppingItem]], week_start: date, language: str = "de") -> str:
    """Plain text to copy, e.g. into an iPhone note."""
    labels = texts.labels(language)
    lines = [labels["shopping"]["title"].format(date=week_start.strftime("%d.%m.%Y"))]
    for department, items in departments.items():
        lines += ["", labels["departments"][department]]
        for item in items:
            if item.amount is None:
                lines.append(f"- {item.name} ({labels['shopping']['as_needed']})")
            else:
                lines.append("- " + " ".join(p for p in (format_number(item.amount), item.unit, item.name) if p))
    return "\n".join(lines)
