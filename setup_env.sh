#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "Python-Umgebung eingerichtet. Starte mit:"
echo "source .venv/bin/activate && python app.py"
