from datetime import date

import pytest

from meal_planner import services
from meal_planner.models import DishIn, FixedItem, FlexMeal, Ingredient, Profile, SlotIn, UnplannedMeal
from meal_planner.services import ServiceError

MONDAY = date(2026, 11, 9)


def add_dish(session, name, **kwargs):
    values = {"kcal_per_serving": 600, "protein_g_per_serving": 30, "servings": 2, **kwargs}
    return services.save_dish(session, DishIn(name=name, **values))


def save_profile(session, **kwargs):
    return services.save_profile(session, Profile(**{"kcal_target": 2000, "meals": ["dinner"], **kwargs}))


def dinner(day, dish_id, cook=True, meal="dinner"):
    return SlotIn(day=day, meal=meal, dish_id=dish_id, cook=cook)


def test_next_week_start():
    assert services.next_week_start(date(2026, 11, 9)) == date(2026, 11, 9)  # Monday
    assert services.next_week_start(date(2026, 11, 8)) == date(2026, 11, 9)  # Sunday
    assert services.next_week_start(date(2026, 11, 10)) == date(2026, 11, 16)


def test_profile_round_trip_and_fixed_item_check(session):
    assert services.get_profile(session) is None
    shake = add_dish(session, "Proteinshake")
    profile = save_profile(session, fixed_items=[FixedItem(dish_id=shake.id)], allergies=["Erdnuss"])
    assert services.get_profile(session) == profile
    with pytest.raises(ServiceError):
        save_profile(session, fixed_items=[FixedItem(dish_id=99)])


def test_memories(session):
    first = services.add_memory(session, "  Mag es scharf ", category="preference")
    assert services.add_memory(session, "mag es SCHARF").id == first.id
    services.add_memory(session, "Linsencurry gern alle zwei Wochen")
    assert [m.text for m in services.list_memories(session)] == ["Mag es scharf", "Linsencurry gern alle zwei Wochen"]
    with pytest.raises(ServiceError):
        services.add_memory(session, "   ")
    services.delete_memory(session, first.id)
    with pytest.raises(ServiceError):
        services.delete_memory(session, first.id)


def test_save_dish_rounds_and_rejects_duplicate_names(session):
    curry = add_dish(session, " Linsencurry ", kcal_per_serving=449, protein_g_per_serving=12.5)
    assert (curry.name, curry.kcal_per_serving, curry.protein_g_per_serving) == ("Linsencurry", 450, 13)
    assert add_dish(session, "Gurke", kcal_per_serving=20).kcal_per_serving == 50
    assert add_dish(session, "Wasser", kcal_per_serving=0).kcal_per_serving == 0
    with pytest.raises(ServiceError):
        add_dish(session, "LINSENCURRY")
    updated = services.save_dish(session, DishIn(name="Linsencurry", kcal_per_serving=700, protein_g_per_serving=20), curry.id)
    assert (updated.id, updated.kcal_per_serving) == (curry.id, 700)
    with pytest.raises(ServiceError):
        services.save_dish(session, DishIn(name="Neu", kcal_per_serving=1, protein_g_per_serving=1), 99)


def test_archive_dish(session):
    shake = add_dish(session, "Proteinshake")
    curry = add_dish(session, "Linsencurry")
    save_profile(session, fixed_items=[FixedItem(dish_id=shake.id)])
    with pytest.raises(ServiceError):
        services.archive_dish(session, shake.id)
    services.archive_dish(session, curry.id)
    assert [d.name for d in services.list_dishes(session)] == ["Proteinshake"]
    assert len(services.list_dishes(session, include_archived=True)) == 2
    add_dish(session, "Linsencurry")  # the name is free again


def draft_dish(name, **kwargs):
    return DishIn(**{"name": name, "kcal_per_serving": 500, "protein_g_per_serving": 20, **kwargs})


def test_drafts_flag_duplicates_and_can_be_discarded(session):
    curry = add_dish(session, "Linsencurry")
    archived = add_dish(session, "Chili")
    services.archive_dish(session, archived.id)
    drafts = services.add_drafts(session, [draft_dish(" linsencurry "), draft_dish("Chili")], "https://notion.so/x")
    assert [(d.dish.name, d.duplicate_of, d.source) for d in drafts] == [
        (" linsencurry ", curry.id, "https://notion.so/x"),
        ("Chili", None, "https://notion.so/x"),  # archived dishes do not count
    ]
    services.add_drafts(session, [draft_dish("Porridge")], "text")
    assert [d.dish.name for d in services.list_drafts(session)] == [" linsencurry ", "Chili", "Porridge"]
    services.discard_draft(session, drafts[1].id)
    assert [d.dish.name for d in services.list_drafts(session)] == [" linsencurry ", "Porridge"]
    with pytest.raises(ServiceError):
        services.discard_draft(session, drafts[1].id)


def test_save_draft_creates_or_replaces_and_deletes_the_draft(session):
    curry = add_dish(session, "Linsencurry")
    new, duplicate = services.add_drafts(session, [draft_dish("Chili"), draft_dish("LINSENCURRY")], "text")
    saved = services.save_draft(session, new.id, draft_dish("Chili sin Carne", kcal_per_serving=449))
    assert (saved.name, saved.kcal_per_serving) == ("Chili sin Carne", 450)  # the edited data, rounded
    with pytest.raises(ServiceError):
        services.save_draft(session, new.id, new.dish)  # the draft is gone
    with pytest.raises(ServiceError):
        services.save_draft(session, duplicate.id, duplicate.dish)  # name taken, no replace
    replaced = services.save_draft(session, duplicate.id, duplicate.dish, replace=True)
    assert (replaced.id, replaced.name, replaced.kcal_per_serving) == (curry.id, "LINSENCURRY", 500)
    assert services.list_drafts(session) == []
    assert len(services.list_dishes(session)) == 2


def test_ratings_and_stats_count_only_final_plans(session):
    curry = add_dish(session, "Linsencurry")
    save_profile(session)
    services.rate_dish(session, curry.id, 4)
    services.rate_dish(session, curry.id, 5, "lecker")
    with pytest.raises(ServiceError):
        services.rate_dish(session, curry.id, 6)
    with pytest.raises(ServiceError):
        services.rate_dish(session, 99, 3)

    services.save_plan_draft(session, [dinner("mon", curry.id)], MONDAY)
    stats = services.dish_stats(session)[curry.id]
    assert (stats.avg_score, stats.ratings, stats.times_planned, stats.last_planned) == (4.5, 2, 0, None)
    services.finalize_plan(session)
    stats = services.dish_stats(session)[curry.id]
    assert (stats.times_planned, stats.last_planned) == (1, MONDAY)


def test_save_plan_draft_adds_profile_slots_and_flex_kcal(session):
    shake = add_dish(session, "Proteinshake", kcal_per_serving=200, servings=1, meal_types=["snack"])
    curry = add_dish(session, "Linsencurry")
    save_profile(
        session,
        fixed_items=[FixedItem(dish_id=shake.id)],
        unplanned_meals=[UnplannedMeal(label="Kantine", meal="lunch", days=["mon", "tue"], kcal=700)],
        flex_meals=[FlexMeal(label="Fast food", kcal=900)],
    )
    view = services.save_plan_draft(
        session,
        [SlotIn(day="tue", meal="dinner", kind="flex", label="fast food"), dinner("mon", curry.id, meal="lunch")],
        MONDAY,
    )
    assert view.status == "draft"
    assert [(s.day, s.meal, s.kind) for s in view.slots[:5]] == [
        ("mon", "lunch", "dish"),  # replaces the canteen on monday
        ("mon", "snack", "fixed"),
        ("tue", "lunch", "unplanned"),
        ("tue", "dinner", "flex"),
        ("tue", "snack", "fixed"),
    ]
    assert len(view.slots) == 2 + 7 + 1
    assert [s.kcal for s in view.slots[2:4]] == [700, 900]
    assert view.check.days[0].kcal == 600 + 200


def test_view_links_leftovers_to_their_cooking_slot(session):
    curry = add_dish(session, "Linsencurry")
    save_profile(session)
    slots = [
        dinner("mon", curry.id),
        dinner("tue", curry.id, cook=False, meal="lunch"),
        dinner("wed", curry.id, cook=False),
        dinner("thu", curry.id),
        dinner("fri", curry.id, cook=False),
    ]
    by_day = {s.day: s for s in services.save_plan_draft(session, slots, MONDAY).slots}
    assert by_day["mon"].covers == ["tue", "wed"]
    assert by_day["wed"].cooked_on == "mon"
    assert (by_day["thu"].covers, by_day["fri"].cooked_on) == (["fri"], "thu")


def test_save_plan_draft_rejects_invalid_input_and_saves_nothing(session):
    satay = add_dish(session, "Satay", ingredients=[Ingredient(name="Erdnussbutter", amount=50, unit="g")])
    with pytest.raises(ServiceError):
        services.save_plan_draft(session, [dinner("mon", satay.id)], MONDAY)  # no profile
    save_profile(session, allergies=["erdnuss"])
    with pytest.raises(ServiceError) as error:
        services.save_plan_draft(session, [dinner("mon", satay.id)], MONDAY)
    assert error.value.errors == ["'Satay' contains 'erdnuss' (allergy)."]
    with pytest.raises(ServiceError):
        services.save_plan_draft(session, [], date(2026, 11, 10))  # a Tuesday
    assert services.get_current_plan(session) is None


def test_save_plan_draft_replaces_the_plan_of_the_same_week(session):
    curry = add_dish(session, "Linsencurry")
    chili = add_dish(session, "Chili")
    save_profile(session)
    first = services.save_plan_draft(session, [dinner("mon", curry.id)], MONDAY, have_at_home=["Reis", " "])
    services.finalize_plan(session, first.plan_id)
    second = services.save_plan_draft(session, [dinner("mon", chili.id), dinner("tue", chili.id)], MONDAY)
    assert (second.plan_id, second.status, second.have_at_home) == (first.plan_id, "draft", ["Reis"])
    assert [s.name for s in second.slots] == ["Chili", "Chili"]
    assert services.save_plan_draft(session, [], MONDAY, have_at_home=[]).have_at_home == []


def test_update_slot(session):
    curry = add_dish(session, "Linsencurry")
    chili = add_dish(session, "Chili", kcal_per_serving=800, ingredients=[Ingredient(name="Chili")])
    shake = add_dish(session, "Proteinshake", meal_types=["snack"])
    save_profile(session, fixed_items=[FixedItem(dish_id=shake.id, days=["mon"])])
    view = services.save_plan_draft(session, [dinner("mon", curry.id)], MONDAY)
    services.finalize_plan(session)
    dish_slot, fixed_slot = view.slots[0], view.slots[1]

    updated = services.update_slot(session, dish_slot.id, dinner("mon", chili.id))
    assert (updated.slots[0].id, updated.slots[0].name, updated.slots[0].kcal, updated.status) == (
        dish_slot.id, "Chili", 800, "final"
    )
    with pytest.raises(ServiceError):
        services.update_slot(session, fixed_slot.id, dinner("mon", curry.id))
    with pytest.raises(ServiceError):
        services.update_slot(session, 999, dinner("mon", curry.id))
    save_profile(session, fixed_items=[FixedItem(dish_id=shake.id, days=["mon"])], dislikes=["kokos"])
    kokos = add_dish(session, "Kokoscurry")
    with pytest.raises(ServiceError):
        services.update_slot(session, dish_slot.id, dinner("mon", kokos.id))
    assert services.get_plan_view(session).slots[0].name == "Chili"


def test_archived_dishes_keep_their_name_in_views(session):
    curry = add_dish(session, "Linsencurry")
    save_profile(session)
    services.save_plan_draft(session, [dinner("mon", curry.id)], MONDAY)
    services.archive_dish(session, curry.id)
    view = services.get_plan_view(session)
    assert view.slots[0].name == "Linsencurry"
    assert view.check.errors == ["mon dinner: 'Linsencurry' (id 1) is archived."]


def test_shopping_list(session):
    curry = add_dish(
        session,
        "Linsencurry",
        servings=4,
        ingredients=[
            Ingredient(name="Rote Linsen", amount=300, unit="g", department="dry_goods"),
            Ingredient(name="Salz", unit="TL", department="spices_sauces"),
            Ingredient(name="Reis", amount=250, unit="g", department="dry_goods"),
        ],
    )
    save_profile(session, household_size=2, pantry_staples=["salz"])
    assert services.get_shopping_list(session) is None
    services.save_plan_draft(
        session, [dinner("mon", curry.id), dinner("tue", curry.id, cook=False)], MONDAY, have_at_home=["Reis"]
    )
    shopping_list = services.get_shopping_list(session)
    assert shopping_list.skipped == ["Salz", "Reis"]
    assert shopping_list.text == "Einkaufsliste ab 09.11.2026\n\nTrockenware\n- 300 g Rote Linsen"
