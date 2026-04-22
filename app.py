import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import pyautogui
from flask import Flask, jsonify, render_template, request
from pynput import mouse

app = Flask(__name__)


@dataclass
class AutomationState:
    paused_until: float = 0.0
    last_automation_move_ts: float = 0.0
    queue_size: int = 0

    def is_paused(self) -> bool:
        return time.time() < self.paused_until


state = AutomationState()
command_queue: queue.Queue[list[dict[str, Any]]] = queue.Queue()
state_lock = threading.Lock()
startup_lock = threading.Lock()
startup_complete = False


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


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
                actions.append({"action": "hotkey", "keys": [k.lower() for k in parts[1:]]})
            elif command == "wait" and len(parts) >= 2:
                actions.append({"action": "wait", "seconds": float(parts[1])})
            else:
                raise ValueError(f"Unbekannter Befehl: {line}")
        except Exception as exc:
            raise ValueError(f"Fehler in Zeile {idx}: {exc}") from exc

    return actions


def perform_action(action: dict[str, Any]) -> None:
    kind = action["action"]

    if kind == "move":
        with state_lock:
            state.last_automation_move_ts = time.time()
        pyautogui.moveTo(action["x"], action["y"], duration=0.15)
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


def worker() -> None:
    while True:
        actions = command_queue.get()
        with state_lock:
            state.queue_size = command_queue.qsize() + 1

        for action in actions:
            while True:
                with state_lock:
                    paused = state.is_paused()
                    until = state.paused_until
                if not paused:
                    break
                sleep_for = max(0.05, min(0.5, until - time.time()))
                time.sleep(sleep_for)

            perform_action(action)

        with state_lock:
            state.queue_size = command_queue.qsize()
        command_queue.task_done()


def start_background_services() -> None:
    global startup_complete

    with startup_lock:
        if startup_complete:
            return

        threading.Thread(target=worker, daemon=True).start()
        start_mouse_listener()
        startup_complete = True


def on_mouse_move(x: int, y: int) -> None:
    del x, y
    now = time.time()
    with state_lock:
        recently_automated = now - state.last_automation_move_ts < 0.3
        if recently_automated:
            return
        state.paused_until = now + 15.0


def start_mouse_listener() -> None:
    listener = mouse.Listener(on_move=on_mouse_move)
    listener.daemon = True
    listener.start()


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
    return jsonify(
        {
            "paused": paused,
            "pause_seconds_remaining": round(remaining, 2),
            "queued_jobs": pending,
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
        return jsonify({"ok": False, "error": str(exc)}), 400

    command_queue.put(actions)
    with state_lock:
        state.queue_size = command_queue.qsize()
    return jsonify({"ok": True, "accepted_actions": len(actions)})


if __name__ == "__main__":
    start_background_services()
    app.run(host="0.0.0.0", port=8000)
