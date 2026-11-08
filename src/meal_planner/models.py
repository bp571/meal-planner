"""Database tables and data schemas shared by the core logic, the agent tools and the web API.

All keys (weekdays, meals, departments, ...) are English ids. Display labels live in texts.py.
"""

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MealType = Literal["breakfast", "lunch", "dinner", "snack"]
MEAL_TYPES: tuple[MealType, ...] = ("breakfast", "lunch", "dinner", "snack")
SlotKind = Literal["dish", "fixed", "flex", "unplanned"]
ProteinSource = Literal["meat", "fish", "vegetarian", "vegan"]
NutritionSource = Literal["estimate", "package", "user"]
Department = Literal[
    "produce",
    "bakery",
    "dairy",
    "meat_fish",
    "frozen",
    "dry_goods",
    "canned",
    "spices_sauces",
    "drinks",
    "other",
]
# Order of the departments on the shopping list, roughly the walk through a supermarket.
DEPARTMENTS: tuple[Department, ...] = (
    "produce",
    "bakery",
    "dairy",
    "meat_fish",
    "frozen",
    "dry_goods",
    "canned",
    "spices_sauces",
    "drinks",
    "other",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)  # SQLModel rejects naive datetimes


# --- Profile (stored as JSON in ProfileRecord, so it can grow without migrations) ---


class FixedItem(BaseModel):
    """A dish eaten on fixed days without planning, e.g. a daily protein shake."""

    dish_id: int
    meal: MealType = "snack"
    days: list[Weekday] = list(WEEKDAYS)
    servings: float = PydanticField(1, gt=0)


class FlexMeal(BaseModel):
    """A meal that is neither cooked nor shopped for, e.g. fast food once a week."""

    label: str = PydanticField(min_length=1)
    per_week: int = PydanticField(1, ge=1)
    kcal: int = PydanticField(ge=0)  # rough lump sum for one such meal
    meal: MealType = "dinner"
    day: Weekday | None = None  # None: the agent picks the day each week


class UnplannedMeal(BaseModel):
    """A recurring meal the agent never plans, e.g. canteen lunch on office days."""

    label: str = PydanticField(min_length=1)
    meal: MealType
    days: list[Weekday]
    kcal: int | None = None  # rough lump sum; None means it does not count


class Profile(BaseModel):
    language: str = "de"
    goal: Literal["lose", "maintain", "gain"] = "maintain"
    kcal_target: int = PydanticField(gt=0)  # per day
    protein_target_g: int | None = None  # per day
    calorie_mode: Literal["daily", "weekly"] = "weekly"
    household_size: int = PydanticField(1, ge=1)  # scales shopping amounts of cooked dishes
    meals: list[MealType] = ["breakfast", "lunch", "dinner"]  # meals the agent plans
    breakfast_variety: Literal["same", "rotate", "varied"] = "rotate"
    cooking_minutes_weekday: int = 30
    cooking_minutes_weekend: int = 60
    meal_prep: bool = True
    max_same_dish_days: int = PydanticField(2, ge=1)
    diet: Literal["omnivore", "pescetarian", "vegetarian", "vegan"] = "omnivore"
    max_meat_meals_per_week: int | None = None
    max_fish_meals_per_week: int | None = None
    allergies: list[str] = []  # ingredient terms, hard exclusion
    dislikes: list[str] = []  # ingredient terms, hard exclusion
    fixed_items: list[FixedItem] = []
    flex_meals: list[FlexMeal] = []
    unplanned_meals: list[UnplannedMeal] = []
    shopping_days: list[Weekday] = ["mon"]
    pantry_staples: list[str] = []  # always at home, never on the shopping list
    equipment: list[str] = []
    notes: str = ""


class ProfileRecord(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    data: dict = Field(sa_column=Column(JSON, nullable=False))  # Profile.model_dump(mode="json")
    updated_at: datetime = Field(default_factory=_utcnow)


# --- Memory ---


class MemoryEntry(SQLModel, table=True):
    """A fact the agent learned in conversation, e.g. "likes it spicy"."""

    id: int | None = Field(default=None, primary_key=True)
    text: str
    category: str = "other"  # preference | dislike | habit | feedback | other
    source: str = "chat"
    created_at: datetime = Field(default_factory=_utcnow)


# --- Dishes ---


class Ingredient(BaseModel):
    name: str = PydanticField(min_length=1)
    amount: float | None = None  # None: "as needed"
    unit: str = ""  # free text: g, ml, Stk, EL, TL, Dose, Packung, ...
    department: Department = "other"


class DishIn(BaseModel):
    """Validated input to create or update a dish. Recipes, simple combos and ready meals are all dishes."""

    name: str = PydanticField(min_length=1)
    servings: float = PydanticField(1, gt=0)  # servings the ingredient list yields
    ingredients: list[Ingredient] = []
    kcal_per_serving: int = PydanticField(ge=0)
    protein_g_per_serving: float = PydanticField(ge=0)
    nutrition_source: NutritionSource = "estimate"
    protein_source: ProteinSource = "vegetarian"
    meal_types: list[MealType] = ["lunch", "dinner"]
    prep_minutes: int | None = None
    meal_prep: bool = False  # keeps well for several days
    source: str = ""  # Notion URL, Instagram URL, "note", ...
    notes: str = ""


class Dish(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    servings: float = 1
    ingredients: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    kcal_per_serving: int
    protein_g_per_serving: float
    nutrition_source: str = "estimate"
    protein_source: str = "vegetarian"
    meal_types: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    prep_minutes: int | None = None
    meal_prep: bool = False
    source: str = ""
    notes: str = ""
    archived: bool = False  # soft delete keeps ratings and past plans intact
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Rating(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dish_id: int = Field(foreign_key="dish.id", index=True)
    plan_id: int | None = Field(default=None, foreign_key="weekplan.id")
    score: int  # 1..5
    comment: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


# --- Week plans ---


class WeekPlan(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    week_start: date = Field(index=True)  # a Monday
    status: str = "draft"  # draft | final
    have_at_home: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    notes: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    finalized_at: datetime | None = None


class Slot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="weekplan.id", index=True)
    day: str  # Weekday
    meal: str  # MealType
    kind: str = "dish"  # SlotKind
    dish_id: int | None = Field(default=None, foreign_key="dish.id")
    label: str = ""  # name for flex and unplanned slots
    kcal: int | None = None  # lump sum for flex and unplanned slots
    servings: float = 1  # servings the user eats
    cook: bool = True  # False: leftover of the latest cooking of the same dish


class SlotIn(BaseModel):
    """A slot as the agent or the UI sends it. Fixed items and recurring unplanned meals come from the profile."""

    day: Weekday
    meal: MealType
    kind: Literal["dish", "flex", "unplanned"] = "dish"
    dish_id: int | None = None
    label: str = ""
    kcal: int | None = None
    servings: float = PydanticField(1, gt=0)
    cook: bool = True


# --- Views returned by services.py ---


class DayTotals(BaseModel):
    day: Weekday
    kcal: int
    protein_g: int


class PlanCheck(BaseModel):
    errors: list[str] = []  # English; any error rejects the plan
    warnings: list[str] = []  # English; for the agent to fix or explain
    days: list[DayTotals]
    week_kcal: int
    week_kcal_target: int
    week_protein_g: int
    week_protein_target_g: int | None = None


class SlotView(BaseModel):
    id: int
    day: Weekday
    meal: MealType
    kind: SlotKind
    dish_id: int | None
    name: str  # dish name, or label for flex and unplanned slots
    kcal: int  # for this slot (servings included); 0 if not counted
    protein_g: int
    servings: float
    cook: bool
    cooked_on: Weekday | None = None  # leftovers: the day the dish was cooked
    covers: list[Weekday] = []  # cooking slots: later days that eat the leftovers


class PlanView(BaseModel):
    plan_id: int
    week_start: date
    status: Literal["draft", "final"]
    slots: list[SlotView]  # ordered by day, then meal
    check: PlanCheck
    have_at_home: list[str]


class DishStats(BaseModel):
    dish_id: int
    avg_score: float | None
    ratings: int
    times_planned: int
    last_planned: date | None  # week_start of the latest plan with this dish


class ShoppingItem(BaseModel):
    name: str
    amount: float | None  # None: "as needed"
    unit: str
    department: Department


class ShoppingList(BaseModel):
    plan_id: int
    week_start: date
    departments: dict[Department, list[ShoppingItem]]  # in DEPARTMENTS order, empty ones left out
    skipped: list[str]  # pantry staples and have_at_home items that were left out
    text: str  # plain text to copy, e.g. into an iPhone note
