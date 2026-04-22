"""Sehr einfaches Testskript, das die Maus kurz bewegt.

Nutzung:
    python test_mouse_move.py
"""

import time

import pyautogui


# Schutz-Ecke aktiv lassen: Maus in die linke obere Ecke bewegen, um abzubrechen.
pyautogui.FAILSAFE = True


def main() -> None:
    print("Starte in 3 Sekunden ...")
    time.sleep(3)

    start_x, start_y = pyautogui.position()
    print(f"Startposition: ({start_x}, {start_y})")

    # Kleine Bewegung nach rechts und wieder zurück.
    pyautogui.moveRel(100, 0, duration=0.3)
    pyautogui.moveRel(-100, 0, duration=0.3)

    end_x, end_y = pyautogui.position()
    print(f"Endposition: ({end_x}, {end_y})")
    print("Fertig.")


if __name__ == "__main__":
    main()
