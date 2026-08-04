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

## Admin

Set `ADMIN_EMAIL` to your own address and that account becomes an admin on every boot, so
admin access can't be locked out by accident. Admins get an **Admin** button on the
dashboard, leading to `/admin`, where you can:

- see every account with its join date, class and student counts, and PeopleSoft status
- **enable or disable PeopleSoft upload** per teacher — new accounts start **disabled** and
  don't see the button at all until an admin turns it on. Disabling an account that had it
  also makes any already-installed bookmark stop working, since the API checks too
- promote or demote other admins
- **delete an account**, which permanently removes its classes, students, and attendance

Deletion asks you to retype the account's email, and you can't delete yourself or drop your
own admin rights.

## Pushing attendance into PeopleSoft

One bookmarklet covers every class you teach — it's on the dashboard, and on each class's
**PeopleSoft** page (`/classes/<id>/sync`). Drag it to your bookmarks bar; it needs no add-on
and no admin rights, so it works on TAFE SOE machines where extensions can't be installed.
It identifies a roster by the `CLASS_NBR` the page publishes, and if that class isn't linked
yet it asks which of your classes it is and links it there and then.

Open a class roster in PeopleSoft, click the bookmark, and it ticks everyone this app has
marked present for that meeting's date, then reports what it did. **It never clicks Save** —
you review the grid and submit yourself.

Each class is tied to a PeopleSoft class the first time you run its bookmark, using the
`CLASS_NBR` the roster page publishes. Run that bookmark on a different class's roster and
it refuses instead of marking anyone — which matters when you teach two classes on the same
day and a student is enrolled in both. The sync page shows the link and can unlink it, which
you'd only need when a class moves to a different PeopleSoft class, e.g. a new term.

Students are matched by PeopleSoft EMPLID, not by name. IDs are learned automatically the
first time you run the bookmark on a roster: it reads `RX_AT_ROST_GRID_EMPLID` from the grid
and pairs each one with a student whose name or alias matches exactly (handling PeopleSoft's
`Surname,Given` form). Anyone still unmatched is listed on the sync page — fix them with a
rename or an alias. An EMPLID is never silently overwritten; mismatches are reported as
conflicts.

TAFE's roster page forbids contacting other sites, so the bookmarklet can't call this app
directly. Instead it opens a small window on this app's own domain which does the work and
posts the answer back. If the roster page has also severed the link between the two windows
(`Cross-Origin-Opener-Policy`), that reply can't arrive — so the window always shows the ID
list and copies it to your clipboard, and the bookmarklet asks you to paste it. Either way
you end up in the same place; one path is automatic and the other is one paste.

The sync link carries a per-class secret (`Klass.sync_token`, separate from the student QR
token) and can read student IDs, so don't leave it on a shared machine's bookmarks bar.

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
| `ADMIN_EMAIL`  | Account granted admin on every boot. See **Admin** above.      |
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
- `templates/` — pages (`base`, `login`, `signup`, `dashboard`, `class`, `mark`, `share`,
  `sheet`, `sync`, `sync_disabled`, `admin`).
- `static/kiosk.js` — the shared tap-to-mark grid used by the teacher and student pages.
- `static/ps_sync.js` — source for the PeopleSoft bookmarklet; inlined into a `javascript:`
  URL at serve time (it can't be loaded remotely — the TAFE page's CSP would block it).
- `Dockerfile`, `fly.toml`, `requirements.txt` — deployment.
- `index.html` — the original standalone offline version (localStorage), kept for reference.
