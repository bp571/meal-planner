import pytest

from meal_planner.models import Dish, FlexMeal, Profile, Slot
from meal_planner.planning import check_plan, round_kcal


def dish(id, name, source="vegan", kcal=2000, protein=100, meal_types=("lunch", "dinner"), prep=20, ingredients=()):
    return Dish(
        id=id,
        name=name,
        servings=2,
        kcal_per_serving=kcal,
        protein_g_per_serving=protein,
        protein_source=source,
        meal_types=list(meal_types),
        prep_minutes=prep,
        ingredients=[{"name": n, "amount": 100, "unit": "g", "department": "other"} for n in ingredients],
    )


DISHES = {
    1: dish(1, "Linsencurry", ingredients=["Rote Linsen", "Kokosmilch"]),
    2: dish(2, "Chili con Carne", source="meat", ingredients=["Rinderhack", "Kidneybohnen"]),
    3: dish(3, "Lasagne", source="vegetarian", prep=45),
    4: dish(4, "Porridge", meal_types=["breakfast"]),
}


def slot(day, dish_id=None, meal="dinner", kind="dish", cook=True, **kwargs):
    return Slot(day=day, meal=meal, kind=kind, dish_id=dish_id, cook=cook, **kwargs)


def week():
    """A week that passes every rule: dinner only, 2,000 kcal and 100 g protein per day."""
    return [
        slot("mon", 1),
        slot("tue", 1, cook=False),
        slot("wed", 2),
        slot("thu", 2, cook=False),
        slot("fri", 1),
        slot("sat", 1, cook=False),
        slot("sun", 2),
    ]


def profile(**kwargs):
    return Profile(**{"kcal_target": 2000, "calorie_mode": "daily", "meals": ["dinner"], **kwargs})


@pytest.mark.parametrize(("kcal", "rounded"), [(0, 0), (24, 0), (25, 50), (425, 450), (449, 450), (474, 450)])
def test_round_kcal_to_nearest_50(kcal, rounded):
    assert round_kcal(kcal) == rounded


def test_valid_week_has_no_errors_or_warnings():
    check = check_plan(profile(), week(), DISHES)
    assert check.errors == []
    assert check.warnings == []
    assert (check.week_kcal, check.week_kcal_target, check.week_protein_g) == (14000, 14000, 700)


def test_totals_include_fixed_flex_and_unplanned_slots():
    dishes = {**DISHES, 5: dish(5, "Proteinshake", kcal=180, protein=30)}
    slots = [
        slot("mon", 1, servings=0.5),  # 1,000 kcal, 50 g
        slot("mon", 5, meal="snack", kind="fixed"),  # 180 kcal, 30 g
        slot("mon", meal="lunch", kind="flex", label="Burger", kcal=900),
        slot("mon", meal="breakfast", kind="unplanned", label="Hotel"),  # no kcal: counts 0
    ]
    check = check_plan(profile(), slots, dishes)
    assert (check.days[0].kcal, check.days[0].protein_g) == (2100, 80)  # 2,080 rounded to 50
    assert check.days[1].kcal == 0
    assert (check.week_kcal, check.week_protein_g) == (2100, 80)


def test_broken_dish_slots_are_errors():
    dishes = {**DISHES, 9: dish(9, "Old dish")}
    dishes[9].archived = True
    slots = [slot("mon"), slot("tue", 99), slot("wed", 9)]
    assert check_plan(profile(), slots, dishes).errors == [
        "mon dinner: dish slot without dish_id.",
        "tue dinner: dish 99 does not exist.",
        "wed dinner: 'Old dish' (id 9) is archived.",
    ]


def test_allergies_and_dislikes_match_ingredients_and_dish_names():
    check = check_plan(profile(allergies=["kokos"], dislikes=["CHILI"]), week(), DISHES)
    assert check.errors == [
        "'Linsencurry' contains 'kokos' (allergy).",
        "'Chili con Carne' contains 'CHILI' (dislike).",
    ]


@pytest.mark.parametrize(
    ("diet", "source", "rejected"),
    [
        ("vegetarian", "meat", True),
        ("vegetarian", "fish", True),
        ("pescetarian", "fish", False),
        ("pescetarian", "meat", True),
        ("vegan", "vegetarian", True),
        ("vegan", "vegan", False),
    ],
)
def test_diet_conflicts(diet, source, rejected):
    dishes = {**DISHES, 2: dish(2, "Dish", source=source)}
    errors = check_plan(profile(diet=diet), week(), dishes).errors
    assert errors == ([f"'Dish' ({source}) does not fit the {diet} diet."] if rejected else [])


def test_leftover_needs_an_earlier_cooking_slot():
    slots = [slot("mon", 1, cook=False), slot("tue", 1), slot("wed", 1, meal="lunch", cook=False)]
    assert check_plan(profile(), slots, DISHES).errors == [
        "mon dinner: leftover of 'Linsencurry' without an earlier cooking slot."
    ]


def test_daily_mode_warns_for_days_outside_15_percent():
    slots = week()
    slots[1] = slot("tue", kind="flex", label="Pizza", kcal=1600)  # -20%
    slots[2] = slot("wed", 2, servings=1.125)  # +12.5%: fine
    assert check_plan(profile(), slots, DISHES).warnings == [
        "tue: ~1,600 kcal, 20% below the daily target of 2,000 kcal."
    ]


def test_weekly_mode_checks_the_week_and_big_day_outliers():
    slots = week()
    slots[0] = slot("mon", kind="flex", label="Pizza", kcal=2600)  # +30%: fine in weekly mode
    slots[1] = slot("tue", kind="flex", label="Pizza", kcal=1500)
    slots[5] = slot("sat", kind="flex", label="Pizza", kcal=2800)  # +40%
    assert check_plan(profile(calorie_mode="weekly"), slots, DISHES).warnings == [
        "sat: ~2,800 kcal, 40% above the daily target of 2,000 kcal."
    ]
    assert check_plan(profile(calorie_mode="weekly", kcal_target=1700), week(), DISHES).warnings == [
        "week: ~14,000 kcal, 18% above the weekly budget of 11,900 kcal."
    ]


def test_protein_warnings_per_day_or_per_week():
    daily = check_plan(profile(protein_target_g=120), week(), DISHES).warnings
    assert len(daily) == 7
    assert daily[0] == "mon: 100 g protein, 17% below the daily target of 120 g protein."
    assert check_plan(profile(protein_target_g=110), week(), DISHES).warnings == []  # 100 >= 90% of 110
    assert check_plan(profile(calorie_mode="weekly", protein_target_g=120), week(), DISHES).warnings == [
        "week: 700 g protein, 17% below the weekly target of 840 g protein."
    ]


def test_meat_limit_counts_leftovers():
    assert check_plan(profile(max_meat_meals_per_week=2), week(), DISHES).warnings == [
        "3 meat meals this week, the limit is 2."
    ]
    assert check_plan(profile(max_meat_meals_per_week=3, max_fish_meals_per_week=0), week(), DISHES).warnings == []


def test_same_dish_on_too_many_consecutive_days():
    slots = week()
    slots[2] = slot("wed", 1, cook=False)
    slots[3] = slot("thu", 2)
    assert check_plan(profile(), slots, DISHES).warnings == [
        "'Linsencurry' on 3 days in a row (mon-wed), the maximum is 2."
    ]


def test_slot_warnings_for_prep_time_and_meal_type():
    slots = week()
    slots[0] = slot("mon", 3)  # 45 min on a weekday
    slots[1] = slot("tue", 3, cook=False)  # leftovers need no cooking
    slots[5] = slot("sat", 3)  # weekend limit is 60 min
    slots[6] = slot("sun", 4)  # breakfast dish at dinner
    assert check_plan(profile(), slots, DISHES).warnings == [
        "mon dinner: 'Lasagne' takes 45 min, the weekday limit is 30 min.",
        "sun dinner: 'Porridge' is not meant for dinner (breakfast).",
    ]


def test_flex_count_and_missing_meals():
    flex_profile = profile(flex_meals=[FlexMeal(label="Fast food", kcal=2000)])
    assert check_plan(flex_profile, week(), DISHES).warnings == ["'Fast food': 0 flex slots, the profile says 1 per week."]

    slots = week()[:-1]  # nothing on sunday
    slots[0] = slot("mon", kind="flex", label=" fast FOOD", kcal=2000)
    slots[1] = slot("tue", 1)
    assert check_plan(flex_profile, slots, DISHES).warnings == [
        "sun: ~0 kcal, 100% below the daily target of 2,000 kcal.",
        "sun: nothing planned for dinner.",
    ]
