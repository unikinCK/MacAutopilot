import dotenv

import queue
import threading
import time
import os
import base64
import io
import json
from http import HTTPStatus
from collections import deque
from dataclasses import dataclass
from typing import Any

import pyautogui
import requests
from flask import Flask, jsonify, render_template, request
from pynput import mouse

dotenv.load_dotenv()
print(os.getenv("OPENAI_BASE_URL", "nicht gesetzt"))
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


def take_screenshot() -> Any:
    try:
        return pyautogui.screenshot()
    except Exception as exc:
        message = str(exc)
        if "pyscreeze" in message.lower() or "pillow" in message.lower():
            raise ValueError(
                "Screenshot nicht möglich: Bitte Abhängigkeiten aktualisieren "
                "(pip install --upgrade Pillow pyscreeze)."
            ) from exc
        raise


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


def extract_text_from_llm_response(data: dict[str, Any]) -> str:
    direct_text = data.get("output_text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    output = data.get("output")
    if isinstance(output, list):
        fragments: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text_value = block.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    fragments.append(text_value.strip())
        if fragments:
            return "\n".join(fragments).strip()

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                text_value = part.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    parts.append(text_value.strip())
            if parts:
                return "\n".join(parts).strip()

    return ""


def extract_json_object_text(text: str) -> str:
    json_text = text.strip()
    if "```" in json_text:
        start = json_text.find("{")
        end = json_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json_text[start : end + 1]
    return json_text


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




def build_inspection_prompt(user_prompt: str) -> str:
    base_prompt = user_prompt.strip()
    coordinate_schema = (
        "\n\nZusatzanforderung (verpflichtend): Liefere die Antwort als JSON mit diesem Schema:\n"
        '{"screen_summary":"string","action_targets":[{"name":"string","type":"button|input|menu|link|other",'
        '"x":0,"y":0,"confidence":0.0,"reason":"string"}],"ocr_texts":["string"]}\n'
        "Regeln: (1) x/y sind Pixelkoordinaten im Screenshotzentrum des anklickbaren Bereichs, "
        "(2) nur sichtbare und wahrscheinlich interaktive Elemente nennen, "
        "(3) maximal 12 action_targets, (4) keine Ausgabe außerhalb des JSON."
    )
    return f"{base_prompt}{coordinate_schema}"

def analyze_screenshot_with_llm(prompt: str) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

    screenshot_image = take_screenshot()
    image_buffer = io.BytesIO()
    screenshot_image.save(image_buffer, format="PNG")
    image_b64 = base64.b64encode(image_buffer.getvalue()).decode("utf-8")
    image_url = f"data:image/png;base64,{image_b64}"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response_body = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_url},
                ],
            }
        ],
    }

    response = requests.post(
        f"{base_url}/responses",
        headers=headers,
        json=response_body,
        timeout=60,
    )

    if response.status_code in {
        HTTPStatus.NOT_FOUND,
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.METHOD_NOT_ALLOWED,
    }:
        chat_body = {
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
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=chat_body,
            timeout=60,
        )

    response.raise_for_status()
    data = response.json()

    content = extract_text_from_llm_response(data)

    return {
        "model": model,
        "analysis": content,
        "usage": data.get("usage"),
    }


def sanitize_actions(actions: Any) -> list[dict[str, Any]]:
    if not isinstance(actions, list) or not actions:
        raise ValueError("Das LLM hat keine gültigen Aktionen geliefert.")

    cleaned: list[dict[str, Any]] = []
    for idx, item in enumerate(actions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Aktion {idx} ist kein Objekt.")

        action = str(item.get("action", "")).strip().lower()
        if action == "move":
            cleaned.append(
                {
                    "action": "move",
                    "x": int(item["x"]),
                    "y": int(item["y"]),
                }
            )
        elif action == "click":
            button = str(item.get("button", "left")).strip().lower()
            if button not in {"left", "right", "middle"}:
                raise ValueError(f"Ungültiger Button in Aktion {idx}: {button}")
            cleaned.append({"action": "click", "button": button})
        elif action == "doubleclick":
            cleaned.append({"action": "doubleclick"})
        elif action == "type":
            cleaned.append({"action": "type", "text": str(item.get("text", ""))})
        elif action == "hotkey":
            keys_raw = item.get("keys", [])
            if not isinstance(keys_raw, list) or not keys_raw:
                raise ValueError(f"Aktion {idx} hat keine gültigen Hotkeys.")
            keys = [normalize_hotkey_key(str(key)) for key in keys_raw]
            cleaned.append({"action": "hotkey", "keys": keys})
        elif action == "wait":
            cleaned.append({"action": "wait", "seconds": float(item.get("seconds", 1.0))})
        else:
            raise ValueError(f"Unbekannte Aktion {idx}: {action}")

    return cleaned


def plan_actions_with_llm(goal: str) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

    screenshot_image = take_screenshot()
    image_buffer = io.BytesIO()
    screenshot_image.save(image_buffer, format="PNG")
    image_b64 = base64.b64encode(image_buffer.getvalue()).decode("utf-8")
    image_url = f"data:image/png;base64,{image_b64}"

    prompt_text = (
        "Erstelle einen kurzen Plan und konkrete UI-Aktionen, um folgendes Ziel zu erreichen:\n"
        f"{goal}\n\n"
        "Antworte NUR als JSON mit diesem Schema:\n"
        '{'
        '"plan_summary": "string",'
        '"actions":[{"action":"move","x":0,"y":0}|'
        '{"action":"click","button":"left"}|'
        '{"action":"doubleclick"}|'
        '{"action":"type","text":"..."}|'
        '{"action":"hotkey","keys":["command","space"]}|'
        '{"action":"wait","seconds":1.0}],'
        '"notes":"string"'
        "}\n"
        "Regeln: maximal 15 Aktionen, nur sichtbare UI verwenden, keine Erklärtexte außerhalb von JSON."
    )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.post(
        f"{base_url}/responses",
        headers=headers,
        json={
            "model": model,
            "instructions": "Du bist ein Desktop-Automationsplaner. Nutze den Screenshot als Kontext und gib NUR JSON zurück.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt_text},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
        },
        timeout=60,
    )

    if response.status_code in {
        HTTPStatus.NOT_FOUND,
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.METHOD_NOT_ALLOWED,
    }:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Du bist ein Desktop-Automationsplaner. Nutze den Screenshot als Kontext und gib NUR JSON zurück.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt_text,
                            },
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    },
                ],
            },
            timeout=60,
        )

    response.raise_for_status()
    data = response.json()

    content = extract_text_from_llm_response(data)
    json_text = extract_json_object_text(content)

    parsed = json.loads(json_text)
    cleaned_actions = sanitize_actions(parsed.get("actions"))

    return {
        "model": model,
        "plan_summary": str(parsed.get("plan_summary", "")).strip(),
        "notes": str(parsed.get("notes", "")).strip(),
        "actions": cleaned_actions,
        "usage": data.get("usage"),
    }


def actions_to_instructions(actions: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for action in actions:
        kind = action["action"]
        if kind == "move":
            lines.append(f"move {action['x']} {action['y']}")
        elif kind == "click":
            button = action.get("button", "left")
            lines.append("click" if button == "left" else f"click {button}")
        elif kind == "doubleclick":
            lines.append("doubleclick")
        elif kind == "type":
            lines.append(f"type {action['text']}")
        elif kind == "hotkey":
            lines.append(f"hotkey {' '.join(action['keys'])}")
        elif kind == "wait":
            lines.append(f"wait {action['seconds']}")
    return "\n".join(lines)


def get_proxy_headers() -> dict[str, str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


@app.get("/v1/models")
def proxy_models() -> Any:
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    response = requests.get(f"{base_url}/models", headers=get_proxy_headers(), timeout=30)
    return jsonify(response.json()), response.status_code


def proxy_post(endpoint: str) -> Any:
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    payload = request.get_json(silent=True) or {}
    response = requests.post(
        f"{base_url}/{endpoint}",
        headers=get_proxy_headers(),
        json=payload,
        timeout=60,
    )
    return jsonify(response.json()), response.status_code


@app.post("/v1/responses")
def proxy_responses() -> Any:
    return proxy_post("responses")


@app.post("/v1/chat/completions")
def proxy_chat_completions() -> Any:
    return proxy_post("chat/completions")


@app.post("/v1/completions")
def proxy_completions() -> Any:
    return proxy_post("completions")


@app.post("/v1/embeddings")
def proxy_embeddings() -> Any:
    return proxy_post("embeddings")


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
            "Beschreibe prägnant sichtbare, interaktive Bedienelemente "
            "(Buttons, Eingabefelder, Menüs, etc.) samt Koordinaten für mögliche Aktionen."
        ),
    )
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"ok": False, "error": "Prompt darf nicht leer sein."}), 400

    try:
        result = analyze_screenshot_with_llm(build_inspection_prompt(prompt))
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


@app.post("/plan-and-run")
def plan_and_run() -> Any:
    start_background_services()
    payload = request.get_json(silent=True) or {}
    goal = payload.get("goal", "")
    auto_execute = bool(payload.get("auto_execute", True))

    if not isinstance(goal, str) or not goal.strip():
        return jsonify({"ok": False, "error": "Ziel darf nicht leer sein."}), 400

    try:
        result = plan_actions_with_llm(goal.strip())
    except ValueError as exc:
        push_debug_event("plan_and_run_validation_error", error=str(exc))
        return jsonify({"ok": False, "error": str(exc)}), 400
    except requests.RequestException as exc:
        push_debug_event("plan_and_run_request_error", error=str(exc))
        return jsonify({"ok": False, "error": f"LLM-Aufruf fehlgeschlagen: {exc}"}), 502
    except json.JSONDecodeError as exc:
        push_debug_event("plan_and_run_json_error", error=str(exc))
        return jsonify({"ok": False, "error": f"LLM-Antwort war kein valides JSON: {exc}"}), 502
    except Exception as exc:
        push_debug_event("plan_and_run_error", error=str(exc))
        return jsonify({"ok": False, "error": f"Interner Fehler: {exc}"}), 500

    accepted_actions = 0
    if auto_execute:
        command_queue.put(result["actions"])
        with state_lock:
            state.queue_size = command_queue.qsize()
        accepted_actions = len(result["actions"])

    push_debug_event(
        "plan_and_run_ok",
        model=result["model"],
        accepted_actions=accepted_actions,
        auto_execute=auto_execute,
    )
    return jsonify(
        {
            "ok": True,
            "goal": goal.strip(),
            "auto_execute": auto_execute,
            "accepted_actions": accepted_actions,
            "instructions": actions_to_instructions(result["actions"]),
            **result,
        }
    )


if __name__ == "__main__":
    start_background_services()
    app.run(host="0.0.0.0", port=8000)
