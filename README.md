# MedMinder

A medication tracking web app built on Flask + SQLite + Google OAuth.

## Features

- **Google OAuth login** — no passwords, just sign in with Google
- **Medication management** — add meds with name, dosage, color-coding, and notes
- **Flexible scheduling** — set multiple reminder times per medication, per day of week
- **Today's dashboard** — check off doses as you take them, see daily progress
- **7-day adherence stats** — track how consistently you're taking your meds
- **30-day history** — a visual heatmap + detailed log of every dose taken

## Project structure

```
medminder/
├── app.py                  # Flask app — all routes and API endpoints
├── requirements.txt
├── .env.example
└── templates/
    ├── base.html           # Shared layout, nav, styles, toast notifications
    ├── index.html          # Landing / sign-in page
    ├── dashboard.html      # Today's schedule (Today tab)
    ├── medications.html    # Add/edit/delete medications
    └── history.html        # Dose log + 30-day adherence heatmap
```

## Database schema

```sql
-- From the original starter:
users (id, google_id, email, name, picture, given_name, family_name, locale, created_at, last_login)

-- New tables:
medications (id, user_id, name, dosage, notes, color, active, created_at)
schedules   (id, med_id, user_id, time_of_day, days_of_week, label)
dose_log    (id, schedule_id, user_id, med_id, taken_at, scheduled_date, status)
```

`days_of_week` is a comma-separated string of integers (0=Monday … 6=Sunday).

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/medications` | List all active medications (with schedules) |
| POST | `/api/medications` | Add a medication |
| PUT | `/api/medications/<id>` | Update a medication |
| DELETE | `/api/medications/<id>` | Archive a medication |
| GET | `/api/today` | Today's scheduled doses with taken status |
| POST | `/api/log` | Toggle a dose taken/untaken |
| GET | `/api/history?days=N` | Dose log for past N days |
| GET | `/api/stats` | Summary stats (totals, 7-day adherence) |

## Quick start

### 1. Set up Google OAuth (same as original)

- Google Cloud Console → APIs & Services → Credentials → OAuth client ID
- Redirect URI: `http://localhost:5000/authorized`

### 2. Install & configure

```bash
cd medminder
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in FLASK_SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
```

### 3. Run

```bash
python app.py
```

Open http://localhost:5000 — sign in with Google, add your medications!

## Extending

- **Email/SMS reminders**: integrate SendGrid or Twilio; add a background scheduler (APScheduler) to fire based on `schedules` table rows
- **Multiple profiles**: add a `profile_id` foreign key to medications to let one account track meds for family members
- **PostgreSQL**: swap `sqlite3` calls for SQLAlchemy with a Postgres URL for production
- **PWA / push notifications**: add a service worker and Web Push to send browser notifications at scheduled times
