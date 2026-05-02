# MedMinder

An application to let you track your daily medications.

## Quick start

### 1. Create a Google OAuth app

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services → Credentials**.
2. Click **Create Credentials → OAuth client ID**.
3. Application type: **Web application**.
4. Under **Authorised redirect URIs**, add:

   ```bash
   http://localhost:5000/authorized
   ```

5. Copy your **Client ID** and **Client Secret**.

### 2. Set up the project

```bash
# Clone / copy the project, then:
cd google-auth-app
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure secrets

```bash
cp .env.example .env
```

Edit `.env`:

```bash
FLASK_SECRET_KEY=some-long-random-string
GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxxx
```

Load the env vars before running (or use `python-dotenv`):

```bash
export $(grep -v '^#' .env | xargs)   # macOS/Linux
# or install python-dotenv and add load_dotenv() to app.py
```

### 4. Run

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000).

---

## Optional: auto-load `.env` with python-dotenv

```bash
pip install python-dotenv
```

Add to the top of `app.py`:

```python
from dotenv import load_dotenv
load_dotenv()
```

---

## Database schema

```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    google_id   TEXT    UNIQUE NOT NULL,   -- Google "sub" claim
    email       TEXT    NOT NULL,
    name        TEXT,
    picture     TEXT,
    given_name  TEXT,
    family_name TEXT,
    locale      TEXT,
    created_at  TEXT    NOT NULL,          -- ISO-8601 UTC
    last_login  TEXT    NOT NULL           -- ISO-8601 UTC
);
```

Users are upserted on every login — `created_at` stays fixed, `last_login` updates.

---

## API endpoint

| Method | Path | Auth required | Description |
|--------|------|---------------|-------------|
| GET | `/` | No | Landing page |
| GET | `/login` | No | Starts Google OAuth flow |
| GET | `/authorized` | No | OAuth callback |
| GET | `/dashboard` | Yes (session) | Profile + users table |
| GET | `/api/users` | Yes (session) | JSON list of all users |
| GET | `/logout` | No | Clears session, redirects to `/` |

---

## Extending this template

- **Switch to PostgreSQL/MySQL**: replace the `sqlite3` calls in `app.py` with SQLAlchemy.
- **Add roles**: add a `role` column to the users table and check it in a `@login_required` decorator.
- **Production deployment**: set `FLASK_SECRET_KEY` to a real secret, use a proper WSGI server (gunicorn), and register your production domain in Google Console.
