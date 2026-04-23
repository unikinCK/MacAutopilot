# MacAutopilot

Kleiner Python-Prototyp, um einen macOS-Desktop per Skript über eine Weboberfläche zu steuern.

## Features

- Weboberfläche zum Einreichen von Aufträgen
- Zielbasierte Steuerung: Nutzer beschreibt eine Aufgabe in natürlicher Sprache, das LLM plant Aktionen und kann sie direkt ausführen
- Screenshot-Analyse via Vision-LLM als Kontext für den nächsten Automationsplan
- Hintergrund-Worker für die Abarbeitung von Befehlen
- Sicherheits-Interlock: Wenn die Maus manuell bewegt wird, pausiert die Automatisierung **15 Sekunden**
- Status-Endpoint für Live-Anzeige

## Voraussetzungen (macOS)

- Python 3.10+
- Accessibility-Berechtigung für Terminal/Python (System Settings → Privacy & Security → Accessibility)

## Setup

```bash
./setup_env.sh
```

Oder manuell:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Wenn beim Screenshot-Feature ein Fehler zu `pyscreeze`/`Pillow` erscheint, einmal die Umgebung aktualisieren:

```bash
pip install --upgrade Pillow pyscreeze
```

## Start

```bash
source .venv/bin/activate
# optional:
# export OPENAI_API_KEY="..."
# export OPENAI_BASE_URL="https://api.openai.com/v1"
# export OPENAI_MODEL="gpt-4.1-mini"
python app.py
```

Danach im Browser öffnen:

- <http://127.0.0.1:8000>

## Auftragssprache

Eine Aktion pro Zeile:

- `move 500 400`
- `click` oder `click right`
- `doubleclick`
- `type Hallo Welt`
- `hotkey cmd space`
- `wait 1.5`

Hinweis: Bei `hotkey` werden gängige Aliase normalisiert, z. B. `cmd` → `command`, `option` → `alt`, `return` → `enter`.

## API

### `POST /submit`

```json
{
  "instructions": "move 500 400\nclick\ntype Hallo"
}
```

### `GET /status`

Beispiel:

```json
{
  "paused": false,
  "pause_seconds_remaining": 0.0,
  "queued_jobs": 0
}
```

### `POST /inspect-screen`

Macht einen Screenshot des aktuellen Bildschirms und schickt ihn an ein OpenAI-kompatibles Vision-LLM.

Request:

```json
{
  "prompt": "Welche Bedienelemente und Texte sind zu sehen?"
}
```

Response (gekürzt):

```json
{
  "ok": true,
  "model": "gpt-4.1-mini",
  "analysis": "Ich sehe ...",
  "usage": { "prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168 }
}
```

### `POST /plan-and-run`

Nimmt ein natürlichsprachiges Ziel, macht einen aktuellen Screenshot, lässt ein LLM daraus einen Aktionsplan erzeugen und kann die Aktionen direkt in die Queue legen.

Request:

```json
{
  "goal": "Öffne Notizen und erstelle eine neue Notiz mit dem Titel Einkaufsliste.",
  "auto_execute": true
}
```

Response (gekürzt):

```json
{
  "ok": true,
  "goal": "Öffne Notizen und erstelle eine neue Notiz mit dem Titel Einkaufsliste.",
  "auto_execute": true,
  "accepted_actions": 6,
  "plan_summary": "...",
  "actions": [
    { "action": "hotkey", "keys": ["command", "space"] },
    { "action": "type", "text": "Notizen" }
  ],
  "instructions": "hotkey command space\ntype Notizen"
}
```
