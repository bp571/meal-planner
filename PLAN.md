# Meal Planner – Plan für den Neustart

Stand: 2026-11-08

## Ziel

Sonntags mit dem Agenten sprechen und einen Wochenplan mit Einkaufsliste bekommen. Der Agent
plant aus der eigenen Gerichte-Bibliothek, schätzt die Kalorien grob und hält sich an das
Nutzerprofil (z. B. täglicher Proteinshake, wenig Fleisch, einmal pro Woche Fastfood).
Nutzerprofil und Gedächtnis sind das Herzstück. Alles läuft in einem UI.

Das Projekt wird öffentlich auf GitHub veröffentlicht. Deshalb steht nichts Nutzerspezifisches
im Code: Alles kommt aus dem Onboarding.

Beispiel für einen Tag im Plan:

```
Montag                                            ~2.150 kcal · 140 g Protein
  Frühstück  Brot mit Ei und Avocado              ~450 kcal
  Mittag     Linsencurry (kochen, reicht für Di)  ~650 kcal
  Abend      Frosta Hähnchen-Curry                ~550 kcal
  Fix        Proteinshake                         ~200 kcal
```

## Leitprinzipien

Diese Entscheidungen gelten schon im MVP, damit die späteren Phasen nichts umbauen müssen.

1. **Das LLM entscheidet, Python rechnet.** Der Agent führt das Gespräch, wählt Gerichte aus und
   schätzt Nährwerte beim Import. Kalorien-Summen, Prüfregeln und Einkaufsliste rechnet
   deterministischer Python-Code. Das ist testbar und liefert jedes Mal dasselbe Ergebnis.
2. **Eine Kernlogik für Chat und Klicks.** Agent-Tools und UI-Buttons rufen dieselben
   Python-Funktionen auf. Klicks in Phase 2 brauchen deshalb keine zweite Logik.
3. **Backend und Frontend getrennt.** FastAPI liefert eine HTTP-API, das Frontend sind
   statische Dateien. Handy, PWA und Hosting kommen später ohne Rewrite dazu.
4. **Gedächtnis sind strukturierte Daten, keine Chatverläufe.** Profil, gelernte Fakten,
   Bewertungen und vergangene Pläne liegen in der Datenbank. Sie gelten über alle Sitzungen
   hinweg, und man kann sie im UI ansehen und löschen.
5. **SQLite über SQLModel.** Lokal ist das eine Datei. Für Azure wird später nur die
   Verbindungs-URL getauscht, z. B. auf Postgres.
6. **Login konfigurierbar.** Lokal reicht das eigene Claude-Code-Login. Andere Nutzer und eine
   gehostete Version brauchen einen eigenen `ANTHROPIC_API_KEY`.
7. **Keine persönlichen Daten im Repo.** `data/` und `.env` sind von Anfang an in `.gitignore`.
   Das Repo kann jederzeit veröffentlicht werden.
8. **Nährwerte werden einmal pro Gericht geschätzt und gespeichert.** Jeder Wert speichert seine
   Herkunft: geschätzt, von der Packung oder von dir. So bleiben die Werte jede Woche gleich und
   lassen sich später durch Werte aus einer Datenbank ersetzen.
9. **Offene Datenmodelle.** Gerichte bekommen später Kochanleitung oder Bild als zusätzliche
   Felder. Plan-Slots haben feste IDs und Typen, damit man sie später tauschen und anklicken kann.
10. **UI-Texte an einer Stelle.** Das MVP-UI ist deutsch, Englisch kommt mit der Veröffentlichung.

## Architektur

### Stack

| Teil | Wahl | Grund |
| --- | --- | --- |
| Agent | Claude Agent SDK (Python) | Läuft lokal mit dem eigenen Claude-Login, eigene Tools über einen In-Process-MCP-Server |
| Backend | FastAPI | Streamt Chat-Antworten an den Browser, die API ist später hostbar |
| Frontend | HTML, CSS, Vanilla JS, ohne Build-Schritt | Andere brauchen nur Python zum Installieren, das Layout funktioniert später auch am iPhone |
| Datenbank | SQLite mit SQLModel | Eine Datei lokal, später austauschbar |
| Notion | Offizielle Notion-API mit Integration-Token in `.env` | Liest Seiten oder eine ganze Datenbank |
| Modell | Per Umgebungsvariable konfigurierbar, Standard: aktuelles Sonnet | Kosten und Qualität lassen sich ohne Codeänderung anpassen |

Der Agent hat keinen Zugriff auf Dateien oder die Shell. Er bekommt nur die eigenen Tools und
optional die Websuche für Nährwerte von Fertigprodukten. Eingefügte Texte und Notion-Inhalte
sind für ihn Daten, keine Anweisungen.

### Datenmodell

| Objekt | Inhalt |
| --- | --- |
| Profil | Alle Antworten aus dem Onboarding, strukturiert, plus Freitext-Notizen |
| Gedächtnis-Eintrag | Ein gelernter Fakt (z. B. „mag es scharf“, „Linsencurry gern alle zwei Wochen“), mit Kategorie, Datum und Quelle |
| Gericht | Name, Zutaten (Name, Menge, Einheit, Supermarkt-Abteilung), Portionen, kcal und Protein pro Portion, Herkunft der Nährwerte, Tags (vegetarisch, Fleisch oder Fisch, passende Mahlzeiten, meal-prep-tauglich, Zubereitungszeit), Quelle (Notion-Link, Instagram-Link, Notiz). Rezepte, einfache Kombis und Fertigprodukte sind alle Gerichte. |
| Wochenplan | Woche, Status (Entwurf oder fest), Slots, Vorräte für diese Woche |
| Slot | Tag, Mahlzeit, Typ (Gericht, fester Eintrag, Flex-Mahlzeit, nicht geplant), Gericht, Portionen, Verweis „Rest von“ für Meal Prep |
| Bewertung | Gericht, Woche, Note und Kommentar |
| Einkaufsliste | Wird aus dem festen Plan berechnet. Einen Abhak-Status bekommt sie in Phase 2. |

Die Slot-Typen bilden alle Fälle ab, ohne Sonderlogik:

- **Fester Eintrag:** z. B. der tägliche Proteinshake, als Gericht in der Bibliothek.
- **Flex-Mahlzeit:** z. B. Fastfood oder Essen gehen. Sie zählt mit einer kcal-Pauschale und
  landet nicht auf der Einkaufsliste.
- **Nicht geplant:** z. B. Kantine. Optional mit einer kcal-Pauschale.

### Gedächtnis (Herzstück)

Zu Beginn jeder Sitzung bekommt der Agent:

- das Profil
- alle Gedächtnis-Einträge
- pro Gericht die Durchschnittsnote und das Datum, an dem es zuletzt geplant war
- eine Zusammenfassung der letzten drei Wochenpläne
- eine kompakte Liste der Bibliothek. Wird sie groß, wechselt das auf eine Suche über ein Tool.

Neue Fakten merkt sich der Agent über ein Tool. Im Chat erscheint dann sichtbar „Gemerkt: …“.
Im UI lässt sich jeder Eintrag ansehen und löschen.

### Agent-Tools im MVP

| Bereich | Tools |
| --- | --- |
| Profil | `get_profile`, `save_profile` |
| Gedächtnis | `remember`, `forget` |
| Bibliothek | `list_dishes`, `get_dish`, `save_dish`, `archive_dish`, `read_notion` |
| Plan | `save_plan_draft` (prüft, rechnet und speichert als Entwurf), `update_slot`, `finalize_plan`, `get_plan` |
| Feedback | `rate_dish` |
| Einkauf | `get_shopping_list` |

**Prüfregeln in Python:**

- *Fehler, der Plan wird abgelehnt:* Allergien und ausgeschlossene Zutaten.
- *Warnung, der Agent korrigiert oder erklärt:* Abweichung vom kcal-Ziel oder Wochenbudget,
  Fleisch-Limit überschritten, dasselbe Gericht zu viele Tage hintereinander, Kochzeit zu lang,
  Gericht passt nicht zur Mahlzeit.

Die kcal-Werte werden auf 50 gerundet und mit „~“ angezeigt.

## MVP

**Das MVP ist fertig, wenn** du am Laptop:

- das Onboarding abschließt,
- deine rund 25 Gerichte aus Notion und Notizen importierst,
- sonntags eine Woche mit grober Kalorienschätzung planst und sie im Chat anpasst,
- die Einkaufsliste als Text aufs iPhone kopierst,

und der Agent am nächsten Sonntag dein Feedback und dein Profil noch kennt.

### Schritt 0 – Neustart

- Alten Code, alte Tests, `data/`, `.venv` und Caches löschen
- `git init`
- Projektgerüst anlegen: `pyproject.toml`, `src/meal_planner/`, `tests/`, `.gitignore`
  (mit `data/` und `.env`), `.env.example`

### Schritt 1 – Daten

- SQLModel-Tabellen für Profil, Gedächtnis, Gericht, Wochenplan, Slot und Bewertung
- Datenbankdatei unter `data/meal_planner.db`

### Schritt 2 – Kernlogik (deterministisch, mit Unit-Tests)

- Summen für kcal und Protein pro Tag und Woche, inklusive fester Einträge und Pauschalen,
  im Tageszielmodus oder im Wochenbudgetmodus
- Prüfregeln (siehe oben)
- Einkaufsliste:
  - Mengen pro Portion, hochgerechnet auf die geplanten Portionen
  - gleiche Zutat mit gleicher Einheit wird addiert
  - sortiert nach Abteilung
  - ohne Basisvorrat und ohne die Vorräte dieser Woche
  - als Textausgabe

### Schritt 3 – Agent

- Client des Agent SDK
- System-Prompt aus Profil und Gedächtnis
- Tools aus der Tabelle oben
- Ein Agent mit einem Chat für alle drei Abläufe (Onboarding, Rezepte-Import, Sonntagsplanung).
  Jeder Ablauf hat einen eigenen Abschnitt mit Anweisungen im System-Prompt.
- Startpunkt nach Zustand, alle Fähigkeiten in jedem Gespräch:
  - kein Profil: Der Agent führt durch das Onboarding, bis das Profil steht. Das ist der einzige
    echte Modus.
  - keine Gerichte: Der Agent schlägt vor, die Bibliothek aufzubauen.
  - sonst: Der Agent schlägt die Wochenplanung vor.
- Mitten in der Planung lassen sich trotzdem Gerichte hinzufügen oder das Profil ändern.
- Startknöpfe im UI wie „Woche planen“ oder „Gericht hinzufügen“ schicken nur eine passende erste
  Nachricht in denselben Chat.

### Schritt 4 – Web-UI

- Links der Chat mit Streaming
- Rechts vier Tabs:
  - **Woche:** Raster aus Tagen und Mahlzeiten, kcal pro Tag, Wochensumme gegen das Ziel
  - **Einkauf:** Liste mit Button „Kopieren“
  - **Gerichte:** Liste mit kcal und Protein, Löschen
  - **Profil & Gedächtnis:** Ansicht, Gedächtnis-Einträge löschbar
- Nach jedem Tool-Aufruf, der Daten ändert, aktualisieren sich die Tabs.

### Schritt 5 – Onboarding

Der Agent fragt im Chat ab:

- Ziel (abnehmen, halten oder aufbauen), kcal-Ziel pro Tag (ohne bekannten Wert schätzt der Agent
  ihn grob) und Proteinziel
- Kalorienmodus: Tagesziel oder Wochenbudget
- Personen im Haushalt: Die Mengen werden hochgerechnet, die Kalorien gelten für dich.
- Welche Mahlzeiten geplant werden. Mittags pro Wochentag: zu Hause, zum Mitnehmen oder Kantine.
- Frühstück: immer gleich, abwechselnd aus wenigen Standards oder jeden Tag anders
- Kochzeit unter der Woche und am Wochenende. Meal Prep: ja oder nein, höchstens wie viele Tage
  dasselbe Gericht.
- Ernährungsform und wie oft pro Woche Fleisch und Fisch vorkommen dürfen
- Allergien und Unverträglichkeiten, Abneigungen
- Feste Einträge, z. B. ein täglicher Proteinshake. Sie werden direkt als Gericht angelegt.
- Flex-Mahlzeiten, z. B. einmal pro Woche Fastfood: fester Tag oder flexibel, mit kcal-Pauschale
- Einkaufstag. Sonntags sind die Läden zu, also kommt das Montagsessen bei Bedarf aus dem Vorrat.
- Basisvorrat, der immer da ist, und Küchengeräte

Am Ende fasst der Agent das Profil zusammen. Du korrigierst es bei Bedarf, dann wird es
gespeichert.

### Schritt 6 – Bibliothek aufbauen

- **Notion:** Link zu einer Seite oder einer ganzen Datenbank. Der Agent übernimmt die Zutaten
  und Mengen.
- **Text einfügen:** Notizen oder Instagram-Beschreibungen, auch mehrere Gerichte auf einmal. Der
  Agent zerlegt den Text in einzelne Gerichte.
- **Fertigprodukte:** Name und Packungsgröße reichen. Packungswerte kannst du angeben, sonst
  schätzt der Agent.
- Vor dem Speichern zeigt der Agent jedes Gericht mit Zutaten, Portionen und geschätzten
  Nährwerten. Du bestätigst oder korrigierst.

### Schritt 7 – Sonntagsplanung

1. Der Agent fragt kurz nach der letzten Woche: Was hat geschmeckt, was nicht? Daraus werden
   Bewertungen und Gedächtnis-Einträge.
2. Danach stellt er zwei bis drei Fragen zur neuen Woche: Termine, Essen außer Haus, Lust auf
   etwas Bestimmtes, was noch im Kühlschrank ist. Mit „mach einfach“ überspringst du das.
3. Er baut den Entwurf, und Python prüft ihn. Bei Warnungen korrigiert der Agent den Entwurf,
   bevor du ihn siehst.
4. Du passt im Chat an, der Tab „Woche“ aktualisiert sich.
5. Du sagst „passt“. Der Plan ist dann fest, und die Einkaufsliste lässt sich kopieren.

### Schritt 8 – Abschluss MVP

- README auf Englisch: Setup, Login (eigener Claude-Login oder API-Key), Notion-Token, Start
- `CLAUDE.md` für das Projekt

## Nach dem MVP

### Phase 2 – Unter der Woche anpassen (Chat und Klick)

- Gerichte tauschen (z. B. Mittwoch mit Donnerstag), einen Slot ersetzen (der Agent schlägt
  Alternativen vor), „war auswärts essen“ eintragen
- Die Einkaufsliste zeigt, was schon gekauft ist, was nicht mehr nötig ist und was neu dazukommt
- Einkaufsliste in der App abhaken
- Feedback-Button „hat geschmeckt / nicht“ direkt am Gericht. Er schreibt ins Gedächtnis.

### Phase 3 – Veröffentlichen auf GitHub

- UI auf Englisch umschaltbar
- README mit Screenshots, Lizenz (Vorschlag: MIT)
- GitHub Actions für die Tests

### Phase 4 – Bequemer importieren

- Screenshot-Import: Bild ins UI ziehen, Claude liest das Rezept. Gedacht für Instagram-Reels,
  bei denen das Rezept nur im Video steht.
- Kochanleitungen als zusätzliches Feld
- Notion-Sync: neue Seiten in der Rezept-Datenbank automatisch übernehmen

### Phase 5 – iPhone

- Hosting auf Azure (z. B. Container Apps) mit Login und API-Key
- Das UI als PWA auf dem Home-Bildschirm
- Einkaufsliste als abhakbare Liste am Handy, oder Export nach Todoist oder Microsoft To Do (beide
  mit offizieller API) bzw. in die Erinnerungen über einen iOS-Kurzbefehl

### Ideen ohne feste Reihenfolge

- Vorratsverwaltung: Was ist zu Hause, Reste zuerst verbrauchen
- Genauere Nährwerte aus einer Lebensmitteldatenbank (z. B. Open Food Facts)
- Mehrere Personen mit eigenen Zielen
- Tests und Evaluation für die Tool-Aufrufe des Agenten
- Echte Produktdaten von Aldi, Rewe oder Lidl sind bewusst ausgeschlossen, weil es keine
  offiziellen APIs gibt. Andockpunkt wären die generischen Zutaten der Einkaufsliste.
