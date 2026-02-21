#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
python3 -m portfolio_app.migrate
python3 -m portfolio_app.telegram_bot