# MacAutopilot

Kleiner Python-Prototyp, um einen macOS-Desktop per Skript über eine Weboberfläche zu steuern.

## Features

- Weboberfläche zum Einreichen von Aufträgen
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

## Start

```bash
source .venv/bin/activate
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
