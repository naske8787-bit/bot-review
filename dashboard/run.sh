#!/usr/bin/env bash
# Run the Mining Production Dashboard on port 5051
cd "$(dirname "$0")"
PYTHON=/home/codespace/.python/current/bin/python

echo "Installing dependencies..."
$PYTHON -m pip install -q -r requirements.txt

echo "Starting Mining Dashboard → http://localhost:5051"
$PYTHON app.py
