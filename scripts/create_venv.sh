# #!/usr/bin/env bash
# set -euo pipefail
# cd "$(dirname "$0")/.."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python3 -m portfolio_app.migrate
# echo "venv ready."