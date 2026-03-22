#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

echo ""
echo " ====================================================="
echo "   RBMK-1000 REACTOR CONTROL TRAINING SYSTEM"
echo "   V.I. Lenin Nuclear Power Plant - Unit 4"
echo " ====================================================="
echo ""

# ── Check Python ────────────────────────────────────────────────────
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
    echo " ERROR: Python 3 not found. Please install Python 3.11+"
    exit 1
fi

PY_VER=$($PY --version 2>&1 | awk '{print $2}')
echo " Python $PY_VER found."

# ── Virtual environment ──────────────────────────────────────────────
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo " Creating virtual environment..."
    $PY -m venv "$VENV_DIR"
    echo " Virtual environment created."
fi

source "$VENV_DIR/bin/activate"

# ── Dependencies ─────────────────────────────────────────────────────
echo " Checking dependencies..."
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet --upgrade
echo " Dependencies OK."

# ── Directories ──────────────────────────────────────────────────────
mkdir -p "$SCRIPT_DIR/data"
mkdir -p "$SCRIPT_DIR/config/scenarios"

# ── Launch ───────────────────────────────────────────────────────────
echo ""
echo " Starting RBMK-1000 Simulator..."
echo " Web UI: http://localhost:8080"
echo " Press Ctrl+C to stop."
echo ""

cd "$SCRIPT_DIR"
python backend/main.py
