"""Display labels for server-generated text. The MVP is German; add languages as new keys."""

LABELS: dict[str, dict[str, dict[str, str]]] = {
    "de": {
        "weekdays": {
            "mon": "Montag",
            "tue": "Dienstag",
            "wed": "Mittwoch",
            "thu": "Donnerstag",
            "fri": "Freitag",
            "sat": "Samstag",
            "sun": "Sonntag",
        },
        "meals": {
            "breakfast": "Frühstück",
            "lunch": "Mittag",
            "dinner": "Abend",
            "snack": "Snack",
        },
        "departments": {
            "produce": "Obst & Gemüse",
            "bakery": "Brot & Backwaren",
            "dairy": "Kühlregal & Milchprodukte",
            "meat_fish": "Fleisch & Fisch",
            "frozen": "Tiefkühl",
            "dry_goods": "Trockenware",
            "canned": "Konserven & Gläser",
            "spices_sauces": "Gewürze & Saucen",
            "drinks": "Getränke",
            "other": "Sonstiges",
        },
        "weekdays_short": {
            "mon": "Mo",
            "tue": "Di",
            "wed": "Mi",
            "thu": "Do",
            "fri": "Fr",
            "sat": "Sa",
            "sun": "So",
        },
        "shopping": {
            "title": "Einkaufsliste ab {date}",
            "trip_title": "Einkauf am {weekday}, {date} (für {days})",
            "as_needed": "nach Bedarf",
            "nothing": "Nichts zu kaufen.",
        },
    },
}


def labels(language: str = "de") -> dict[str, dict[str, str]]:
    return LABELS.get(language, LABELS["de"])
