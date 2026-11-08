from datetime import date

import pytest

from meal_planner.models import Dish, Slot
from meal_planner.shopping import build_items, round_amount, to_text


def dish(id, servings, *ingredients):
    """ingredients: (name, amount, unit, department) tuples."""
    return Dish(
        id=id,
        name=f"Dish {id}",
        servings=servings,
        kcal_per_serving=500,
        protein_g_per_serving=20,
        ingredients=[{"name": n, "amount": a, "unit": u, "department": d} for n, a, u, d in ingredients],
    )


def slot(dish_id, kind="dish", servings=1, day="mon"):
    return Slot(day=day, meal="dinner", kind=kind, dish_id=dish_id, servings=servings)


def amounts(departments):
    return {dep: [(i.name, i.amount, i.unit) for i in items] for dep, items in departments.items()}


def test_scales_dish_slots_by_household_and_fixed_slots_only_by_servings():
    dishes = {
        1: dish(1, 4, ("Linsen", 400, "g", "dry_goods")),
        2: dish(2, 1, ("Proteinpulver", 30, "g", "other")),
    }
    slots = [
        slot(1),
        Slot(day="tue", meal="dinner", kind="dish", dish_id=1, cook=False),  # leftovers are eaten too
        slot(2, kind="fixed"),
        slot(2, kind="fixed", servings=2, day="tue"),
        Slot(day="wed", meal="dinner", kind="flex", label="Pizza", kcal=900),
    ]
    departments, skipped = build_items(slots, dishes, household_size=2, exclude=[])
    assert amounts(departments) == {
        "dry_goods": [("Linsen", 400, "g")],  # 400 / 4 servings * 2 slots * 2 people
        "other": [("Proteinpulver", 90, "g")],
    }
    assert skipped == []


def test_merges_same_name_and_unit_and_orders_departments():
    dishes = {
        1: dish(1, 1, ("Tomaten", 400, "g", "canned"), ("Zwiebel", 1, "Stk", "produce")),
        2: dish(2, 1, (" zwiebel ", 2, "stk", "dairy"), ("Tomaten", 1, "Dose", "canned")),
    }
    departments, _ = build_items([slot(1), slot(2)], dishes, household_size=1, exclude=[])
    assert list(departments) == ["produce", "canned"]
    assert amounts(departments) == {
        "produce": [("Zwiebel", 3, "Stk")],  # first name, unit and department win
        "canned": [("Tomaten", 400, "g"), ("Tomaten", 1, "Dose")],
    }


def test_skips_pantry_staples_and_have_at_home_items():
    dishes = {1: dish(1, 1, ("Meersalz", 5, "g", "spices_sauces"), ("Basmati-Reis", 100, "g", "dry_goods"),
                      ("Paprika", 1, "Stk", "produce"))}
    departments, skipped = build_items([slot(1), slot(1, day="tue")], dishes, 1, exclude=["Salz", " reis", ""])
    assert amounts(departments) == {"produce": [("Paprika", 2, "Stk")]}
    assert skipped == ["Meersalz", "Basmati-Reis"]


def test_unknown_amounts_stay_as_needed_unless_mixed():
    dishes = {
        1: dish(1, 1, ("Petersilie", None, "", "produce"), ("Knoblauch", None, "Zehe", "produce")),
        2: dish(2, 1, ("Knoblauch", 2, "Zehe", "produce")),
    }
    departments, _ = build_items([slot(1), slot(2)], dishes, 1, exclude=[])
    assert amounts(departments) == {"produce": [("Knoblauch", 2, "Zehe"), ("Petersilie", None, "")]}


@pytest.mark.parametrize(
    ("amount", "unit", "rounded"),
    [
        (1.2, "Stk", 2),
        (1.0000000000000002, "stk", 1),
        (0.5, "", 1),
        (150.2, "g", 160),
        (42.3, "ml", 43),
        (300.0, "g", 300),
        (1.25, "EL", 1.3),
        (0.333, "kg", 0.3),
        (None, "g", None),
    ],
)
def test_round_amount(amount, unit, rounded):
    assert round_amount(amount, unit) == rounded


def test_text_export_in_german():
    dishes = {
        1: dish(1, 1, ("Avocado", 2, "Stk", "produce"), ("Petersilie", None, "", "produce"),
                ("Olivenöl", 1.5, "EL", "spices_sauces"), ("Eier", 3, "", "dairy")),
    }
    departments, _ = build_items([slot(1)], dishes, 1, exclude=[])
    assert to_text(departments, date(2026, 11, 10)) == (
        "Einkaufsliste ab 10.11.2026\n"
        "\n"
        "Obst & Gemüse\n"
        "- 2 Stk Avocado\n"
        "- Petersilie (nach Bedarf)\n"
        "\n"
        "Kühlregal & Milchprodukte\n"
        "- 3 Eier\n"
        "\n"
        "Gewürze & Saucen\n"
        "- 1,5 EL Olivenöl"
    )
