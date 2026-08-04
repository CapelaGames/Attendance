# Attendance

A hosted, multi-teacher attendance app. Teachers create classes and add students;
students mark themselves present by scanning a **per-class QR code** — no student
accounts needed. Data is stored in Postgres, with CSV export.

## How it works

- **Teachers** sign up (a signup code is required) and log in.
- Each teacher creates **classes** and pastes in a student list.
- Every class has a public **check-in link + QR code** (`/m/<token>`). Students scan it,
  search their name, and tap their card to mark present. A name that isn't listed can be
  self-added on the spot.
- Teachers see live attendance, a **full sheet**, and can **export CSV**.
- Teachers can **close** self check-in or **regenerate** the QR link at any time.

## Run locally (no database setup)

Without a `DATABASE_URL`, the app uses a local SQLite file, so you can run it with nothing
but Python + the dependencies.

```bash
pip install -r requirements.txt
SIGNUP_CODE=test SECRET_KEY=dev python app.py
# open http://localhost:8080  — sign up with code "test"
```

## Environment variables

| Variable       | Purpose                                                        |
|----------------|----------------------------------------------------------------|
| `SIGNUP_CODE`  | The code new teachers must enter to register. **Required.**    |
| `SECRET_KEY`   | Secret for signing session cookies. Use a long random string.  |
| `DATABASE_URL` | Postgres URL. If unset, falls back to local SQLite.            |
| `PORT`         | Port for local dev (default 8080). Ignored under gunicorn.     |

## Deploy to Fly.io

You'll run these yourself (they need your Fly login). `<app-name>` must be globally unique.

```bash
# 1. Install flyctl (Windows PowerShell):
iwr https://fly.io/install.ps1 -useb | iex

# 2. Log in
fly auth login

# 3. Create the app (edit `app` in fly.toml to match this name, or let launch set it)
fly apps create <app-name>

# 4. Create + attach a Postgres database (this sets DATABASE_URL automatically)
fly postgres create --name <app-name>-db --region syd
fly postgres attach <app-name>-db -a <app-name>

# 5. Set the secrets
fly secrets set \
  SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") \
  SIGNUP_CODE=<your-chosen-code> \
  -a <app-name>

# 6. Deploy
fly deploy -a <app-name>

# 7. Open it
fly open -a <app-name>
```

Then sign up with your signup code, create a class, and open **Share / QR** to display
the student check-in code.

To add a custom domain later: `fly certs create attendance.yourdomain.com` and add the
DNS records it prints.

## Files

- `app.py` — Flask app (models + routes).
- `templates/` — pages (`base`, `login`, `signup`, `dashboard`, `class`, `mark`, `share`, `sheet`).
- `static/kiosk.js` — the shared tap-to-mark grid used by the teacher and student pages.
- `Dockerfile`, `fly.toml`, `requirements.txt` — deployment.
- `index.html` — the original standalone offline version (localStorage), kept for reference.
