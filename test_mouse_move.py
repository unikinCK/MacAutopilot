"""Sehr einfaches Testskript, das die Maus sichtbar bewegt.

Nutzung:
    python test_mouse_move.py
    python test_mouse_move.py --distance 200 --duration 0.8
"""

from __future__ import annotations

import argparse
import time



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bewegt die Maus in einem kleinen Quadrat.")
    parser.add_argument("--distance", type=int, default=120, help="Bewegungsstrecke pro Schritt in Pixeln")
    parser.add_argument("--duration", type=float, default=0.45, help="Dauer pro Schritt in Sekunden")
    parser.add_argument("--start-delay", type=float, default=3.0, help="Wartezeit vor dem Start in Sekunden")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.distance <= 0:
        raise ValueError("--distance muss > 0 sein")
    if args.duration <= 0:
        raise ValueError("--duration muss > 0 sein")
    if args.start_delay < 0:
        raise ValueError("--start-delay muss >= 0 sein")

    print(f"Starte in {args.start_delay:.1f} Sekunden ...")
    time.sleep(args.start_delay)

    import pyautogui

    # Schutz-Ecke aktiv lassen: Maus in die linke obere Ecke bewegen, um abzubrechen.
    pyautogui.FAILSAFE = True

    start_x, start_y = pyautogui.position()
    print(f"Startposition: ({start_x}, {start_y})")

    # Sichtbare Testbewegung: kleines Quadrat und zurück zur Startposition.
    pyautogui.moveRel(args.distance, 0, duration=args.duration)
    pyautogui.moveRel(0, args.distance, duration=args.duration)
    pyautogui.moveRel(-args.distance, 0, duration=args.duration)
    pyautogui.moveRel(0, -args.distance, duration=args.duration)

    end_x, end_y = pyautogui.position()
    print(f"Endposition: ({end_x}, {end_y})")
    print("Fertig. Wenn Start- und Endposition gleich sind, war der Test erfolgreich.")


if __name__ == "__main__":
    main()
