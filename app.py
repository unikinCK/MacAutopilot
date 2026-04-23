import queue
import threading
import time
import os
import base64
import io
from collections import deque
from dataclasses import dataclass
from typing import Any

import pyautogui
import requests
from flask import Flask, jsonify, render_template, request
from pynput import mouse

app = Flask(__name__)


@dataclass
class AutomationState:
    paused_until: float = 0.0
    last_automation_move_ts: float = 0.0
    suppress_pause_until: float = 0.0
    queue_size: int = 0
    worker_alive: bool = False
    mouse_listener_alive: bool = False
    current_job_id: int | None = None
    processed_actions_total: int = 0
    last_error: str | None = None
    command_counter: int = 0

    def is_paused(self) -> bool:
        return time.time() < self.paused_until


state = AutomationState()
command_queue: queue.Queue[list[dict[str, Any]]] = queue.Queue()
state_lock = threading.Lock()
startup_lock = threading.Lock()
startup_complete = False
debug_events: deque[dict[str, Any]] = deque(maxlen=50)


def push_debug_event(event: str, **details: Any) -> None:
    debug_events.append(
        {
            "ts": round(time.time(), 3),
            "event": event,
            "details": details,
        }
    )


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


def normalize_hotkey_key(key: str) -> str:
    normalized = key.strip().lower()
    aliases = {
        "cmd": "command",
        "⌘": "command",
        "strg": "ctrl",
        "steuerung": "ctrl",
        "ctrlleft": "ctrl",
        "ctrlright": "ctrl",
        "option": "alt",
        "opt": "alt",
        "return": "enter",
    }
    return aliases.get(normalized, normalized)


def parse_instructions(raw_text: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    for idx, line in enumerate(lines, start=1):
        parts = line.split()
        command = parts[0].lower()

        try:
            if command == "move" and len(parts) >= 3:
                actions.append({"action": "move", "x": int(parts[1]), "y": int(parts[2])})
            elif command == "click":
                button = parts[1].lower() if len(parts) > 1 else "left"
                actions.append({"action": "click", "button": button})
            elif command == "doubleclick":
                actions.append({"action": "doubleclick"})
            elif command == "type" and len(parts) >= 2:
                text = line[len("type") :].strip()
                actions.append({"action": "type", "text": text})
            elif command == "hotkey" and len(parts) >= 2:
                actions.append(
                    {
                        "action": "hotkey",
                        "keys": [normalize_hotkey_key(k) for k in parts[1:]],
                    }
                )
            elif command == "wait" and len(parts) >= 2:
                actions.append({"action": "wait", "seconds": float(parts[1])})
            else:
                raise ValueError(f"Unbekannter Befehl: {line}")
        except Exception as exc:
            raise ValueError(f"Fehler in Zeile {idx}: {exc}") from exc

    return actions


def perform_action(action: dict[str, Any]) -> None:
    kind = action["action"]
    push_debug_event("action_start", action=action)

    if kind == "move":
        with state_lock:
            state.last_automation_move_ts = time.time()
            state.suppress_pause_until = time.time() + 1.0
        pyautogui.moveTo(action["x"], action["y"], duration=0.15)
        with state_lock:
            state.suppress_pause_until = max(state.suppress_pause_until, time.time() + 0.5)
    elif kind == "click":
        pyautogui.click(button=action.get("button", "left"))
    elif kind == "doubleclick":
        pyautogui.doubleClick()
    elif kind == "type":
        pyautogui.write(action["text"], interval=0.02)
    elif kind == "hotkey":
        pyautogui.hotkey(*action["keys"])
    elif kind == "wait":
        time.sleep(max(0.0, float(action["seconds"])))

    with state_lock:
        state.processed_actions_total += 1
    push_debug_event("action_done", action=action)


def worker() -> None:
    while True:
        actions = command_queue.get()
        job_id: int
        with state_lock:
            state.worker_alive = True
            state.queue_size = command_queue.qsize() + 1
            state.command_counter += 1
            job_id = state.command_counter
            state.current_job_id = job_id
        push_debug_event("job_start", job_id=job_id, actions_count=len(actions))

        try:
            for action in actions:
                while True:
                    with state_lock:
                        paused = state.is_paused()
                        until = state.paused_until
                    if not paused:
                        break
                    sleep_for = max(0.05, min(0.5, until - time.time()))
                    push_debug_event("wait_paused", sleep_for=round(sleep_for, 3))
                    time.sleep(sleep_for)

                perform_action(action)
        except Exception as exc:
            with state_lock:
                state.last_error = f"{type(exc).__name__}: {exc}"
            push_debug_event("job_error", job_id=job_id, error=str(exc))
        finally:
            with state_lock:
                state.queue_size = command_queue.qsize()
                state.current_job_id = None
            push_debug_event("job_done", job_id=job_id, queue_size=command_queue.qsize())
            command_queue.task_done()


def start_background_services() -> None:
    global startup_complete

    with startup_lock:
        if startup_complete:
            return

        threading.Thread(target=worker, daemon=True).start()
        start_mouse_listener()
        push_debug_event("services_started")
        startup_complete = True


def on_mouse_move(x: int, y: int) -> None:
    del x, y
    now = time.time()
    with state_lock:
        if now < state.suppress_pause_until:
            return
        recently_automated = now - state.last_automation_move_ts < 0.3
        if recently_automated:
            return
        state.paused_until = now + 15.0
    push_debug_event("manual_mouse_move_pause", pause_until=round(now + 15.0, 3))


def start_mouse_listener() -> None:
    listener = mouse.Listener(on_move=on_mouse_move)
    listener.daemon = True
    listener.start()
    with state_lock:
        state.mouse_listener_alive = True
    push_debug_event("mouse_listener_started")


def analyze_screenshot_with_llm(prompt: str) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

    screenshot_image = pyautogui.screenshot()
    image_buffer = io.BytesIO()
    screenshot_image.save(image_buffer, format="PNG")
    image_b64 = base64.b64encode(image_buffer.getvalue()).decode("utf-8")
    image_url = f"data:image/png;base64,{image_b64}"

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    }

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=body,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()

    content = ""
    choices = data.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")

    return {
        "model": model,
        "analysis": content,
        "usage": data.get("usage"),
    }


@app.get("/")
def index() -> str:
    start_background_services()
    return render_template("index.html")


@app.get("/status")
def status() -> Any:
    start_background_services()
    with state_lock:
        paused = state.is_paused()
        remaining = max(0.0, state.paused_until - time.time())
        pending = state.queue_size
        worker_alive = state.worker_alive
        mouse_listener_alive = state.mouse_listener_alive
        current_job_id = state.current_job_id
        processed_actions_total = state.processed_actions_total
        last_error = state.last_error
    return jsonify(
        {
            "paused": paused,
            "pause_seconds_remaining": round(remaining, 2),
            "queued_jobs": pending,
            "worker_alive": worker_alive,
            "mouse_listener_alive": mouse_listener_alive,
            "current_job_id": current_job_id,
            "processed_actions_total": processed_actions_total,
            "last_error": last_error,
            "recent_events": list(debug_events)[-10:],
        }
    )


@app.post("/submit")
def submit() -> Any:
    start_background_services()
    payload = request.get_json(silent=True) or {}
    instructions = payload.get("instructions", "")

    if not isinstance(instructions, str) or not instructions.strip():
        return jsonify({"ok": False, "error": "Bitte Anweisungen eingeben."}), 400

    try:
        actions = parse_instructions(instructions)
    except ValueError as exc:
        push_debug_event("submit_parse_error", error=str(exc))
        return jsonify({"ok": False, "error": str(exc)}), 400

    command_queue.put(actions)
    with state_lock:
        state.queue_size = command_queue.qsize()
    push_debug_event("submit_ok", actions_count=len(actions), queue_size=command_queue.qsize())
    return jsonify({"ok": True, "accepted_actions": len(actions)})


@app.post("/inspect-screen")
def inspect_screen() -> Any:
    start_background_services()
    payload = request.get_json(silent=True) or {}
    prompt = payload.get(
        "prompt",
        (
            "Beschreibe prägnant, welche Bedienelemente sichtbar sind "
            "(Buttons, Eingabefelder, Menüs, etc.) und welche Texte im Screenshot stehen."
        ),
    )
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"ok": False, "error": "Prompt darf nicht leer sein."}), 400

    try:
        result = analyze_screenshot_with_llm(prompt.strip())
    except ValueError as exc:
        push_debug_event("inspect_screen_config_error", error=str(exc))
        return jsonify({"ok": False, "error": str(exc)}), 400
    except requests.RequestException as exc:
        push_debug_event("inspect_screen_request_error", error=str(exc))
        return jsonify({"ok": False, "error": f"LLM-Aufruf fehlgeschlagen: {exc}"}), 502
    except Exception as exc:
        push_debug_event("inspect_screen_error", error=str(exc))
        return jsonify({"ok": False, "error": f"Interner Fehler: {exc}"}), 500

    push_debug_event("inspect_screen_ok", model=result["model"])
    return jsonify({"ok": True, **result})


if __name__ == "__main__":
    start_background_services()
    app.run(host="0.0.0.0", port=8000)
