#!/usr/bin/env bash
# Local dev run (uses a SQLite database — no Postgres needed).
cd "$(dirname "$0")"
export SIGNUP_CODE="${SIGNUP_CODE:-test}"
export SECRET_KEY="${SECRET_KEY:-dev}"
python app.py
