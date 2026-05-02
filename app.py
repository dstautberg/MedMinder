from dotenv import load_dotenv
load_dotenv()

import os
import sqlite3
import json
from datetime import datetime, date, timedelta
from flask import Flask, redirect, url_for, session, render_template, jsonify, request
from authlib.integrations.flask_client import OAuth
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")

# ── OAuth setup ───────────────────────────────────────────────────────────────
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = "medminder.db"

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    con = get_db()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id   TEXT    UNIQUE NOT NULL,
            email       TEXT    NOT NULL,
            name        TEXT,
            picture     TEXT,
            given_name  TEXT,
            family_name TEXT,
            locale      TEXT,
            created_at  TEXT    NOT NULL,
            last_login  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS medications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name        TEXT    NOT NULL,
            dosage      TEXT,
            notes       TEXT,
            color       TEXT    DEFAULT '#4F86C6',
            active      INTEGER DEFAULT 1,
            created_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS schedules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            med_id      INTEGER NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            time_of_day TEXT    NOT NULL,
            days_of_week TEXT   NOT NULL DEFAULT '0,1,2,3,4,5,6',
            label       TEXT
        );

        CREATE TABLE IF NOT EXISTS dose_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            med_id      INTEGER NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
            taken_at    TEXT    NOT NULL,
            scheduled_date TEXT NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'taken'
        );
    """)
    con.commit()
    con.close()

def upsert_user(info: dict) -> dict:
    now = datetime.utcnow().isoformat()
    con = get_db()
    con.execute("""
        INSERT INTO users (google_id, email, name, picture, given_name, family_name, locale, created_at, last_login)
        VALUES (:sub, :email, :name, :picture, :given_name, :family_name, :locale, :now, :now)
        ON CONFLICT(google_id) DO UPDATE SET
            email=excluded.email, name=excluded.name, picture=excluded.picture,
            given_name=excluded.given_name, family_name=excluded.family_name,
            locale=excluded.locale, last_login=excluded.last_login
    """, {"sub": info.get("sub"), "email": info.get("email"), "name": info.get("name"),
          "picture": info.get("picture"), "given_name": info.get("given_name"),
          "family_name": info.get("family_name"), "locale": info.get("locale"), "now": now})
    con.commit()
    row = con.execute("SELECT * FROM users WHERE google_id=?", (info["sub"],)).fetchone()
    con.close()
    return dict(row)

# ── Auth helpers ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated

def current_user_id():
    return session["user"]["id"]

# ── Routes: Auth ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    user = session.get("user")
    if user:
        return redirect(url_for("dashboard"))
    return render_template("index.html")

@app.route("/login")
def login():
    redirect_uri = url_for("authorized", _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/authorized")
def authorized():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo") or google.userinfo()
    db_user = upsert_user(userinfo)
    session["user"] = db_user
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ── Routes: Pages ─────────────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    hour = datetime.now().hour
    if hour < 12:
        greeting = "morning"
    elif hour < 17:
        greeting = "afternoon"
    else:
        greeting = "evening"
    return render_template("dashboard.html", user=session["user"], greeting=greeting)

@app.route("/medications")
@login_required
def medications_page():
    return render_template("medications.html", user=session["user"])

@app.route("/history")
@login_required
def history_page():
    return render_template("history.html", user=session["user"])

# ── API: Medications ──────────────────────────────────────────────────────────
@app.route("/api/medications", methods=["GET"])
@login_required
def api_get_medications():
    uid = current_user_id()
    con = get_db()
    meds = con.execute(
        "SELECT * FROM medications WHERE user_id=? AND active=1 ORDER BY name",
        (uid,)
    ).fetchall()
    result = []
    for m in meds:
        med = dict(m)
        schedules = con.execute(
            "SELECT * FROM schedules WHERE med_id=?", (m["id"],)
        ).fetchall()
        med["schedules"] = [dict(s) for s in schedules]
        result.append(med)
    con.close()
    return jsonify(result)

@app.route("/api/medications", methods=["POST"])
@login_required
def api_add_medication():
    uid = current_user_id()
    data = request.json
    now = datetime.utcnow().isoformat()
    con = get_db()
    cur = con.execute(
        "INSERT INTO medications (user_id, name, dosage, notes, color, created_at) VALUES (?,?,?,?,?,?)",
        (uid, data["name"], data.get("dosage",""), data.get("notes",""), data.get("color","#4F86C6"), now)
    )
    med_id = cur.lastrowid
    for sched in data.get("schedules", []):
        con.execute(
            "INSERT INTO schedules (med_id, user_id, time_of_day, days_of_week, label) VALUES (?,?,?,?,?)",
            (med_id, uid, sched["time_of_day"], sched.get("days_of_week","0,1,2,3,4,5,6"), sched.get("label",""))
        )
    con.commit()
    med = dict(con.execute("SELECT * FROM medications WHERE id=?", (med_id,)).fetchone())
    con.close()
    return jsonify(med), 201

@app.route("/api/medications/<int:med_id>", methods=["PUT"])
@login_required
def api_update_medication(med_id):
    uid = current_user_id()
    data = request.json
    con = get_db()
    con.execute(
        "UPDATE medications SET name=?, dosage=?, notes=?, color=? WHERE id=? AND user_id=?",
        (data["name"], data.get("dosage",""), data.get("notes",""), data.get("color","#4F86C6"), med_id, uid)
    )
    con.execute("DELETE FROM schedules WHERE med_id=? AND user_id=?", (med_id, uid))
    for sched in data.get("schedules", []):
        con.execute(
            "INSERT INTO schedules (med_id, user_id, time_of_day, days_of_week, label) VALUES (?,?,?,?,?)",
            (med_id, uid, sched["time_of_day"], sched.get("days_of_week","0,1,2,3,4,5,6"), sched.get("label",""))
        )
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/medications/<int:med_id>", methods=["DELETE"])
@login_required
def api_delete_medication(med_id):
    uid = current_user_id()
    con = get_db()
    con.execute("UPDATE medications SET active=0 WHERE id=? AND user_id=?", (med_id, uid))
    con.commit()
    con.close()
    return jsonify({"ok": True})

# ── API: Today's schedule ─────────────────────────────────────────────────────
@app.route("/api/today")
@login_required
def api_today():
    uid = current_user_id()
    today = date.today()
    weekday = str(today.weekday())  # 0=Mon, 6=Sun
    today_str = today.isoformat()

    con = get_db()
    schedules = con.execute("""
        SELECT s.*, m.name as med_name, m.dosage, m.color
        FROM schedules s
        JOIN medications m ON m.id = s.med_id
        WHERE s.user_id=? AND m.active=1
        ORDER BY s.time_of_day
    """, (uid,)).fetchall()

    result = []
    for s in schedules:
        days = s["days_of_week"].split(",")
        if weekday not in days:
            continue
        item = dict(s)
        log = con.execute(
            "SELECT * FROM dose_log WHERE schedule_id=? AND scheduled_date=? AND user_id=?",
            (s["id"], today_str, uid)
        ).fetchone()
        item["taken"] = log is not None
        item["taken_at"] = log["taken_at"] if log else None
        result.append(item)

    con.close()
    return jsonify(result)

# ── API: Log dose ─────────────────────────────────────────────────────────────
@app.route("/api/log", methods=["POST"])
@login_required
def api_log_dose():
    uid = current_user_id()
    data = request.json
    schedule_id = data["schedule_id"]
    scheduled_date = data.get("scheduled_date", date.today().isoformat())
    now = datetime.utcnow().isoformat()

    con = get_db()
    sched = con.execute(
        "SELECT * FROM schedules WHERE id=? AND user_id=?", (schedule_id, uid)
    ).fetchone()
    if not sched:
        con.close()
        return jsonify({"error": "Not found"}), 404

    existing = con.execute(
        "SELECT id FROM dose_log WHERE schedule_id=? AND scheduled_date=? AND user_id=?",
        (schedule_id, scheduled_date, uid)
    ).fetchone()

    if existing:
        con.execute("DELETE FROM dose_log WHERE id=?", (existing["id"],))
        con.commit()
        con.close()
        return jsonify({"taken": False})
    else:
        con.execute(
            "INSERT INTO dose_log (schedule_id, user_id, med_id, taken_at, scheduled_date) VALUES (?,?,?,?,?)",
            (schedule_id, uid, sched["med_id"], now, scheduled_date)
        )
        con.commit()
        con.close()
        return jsonify({"taken": True, "taken_at": now})

# ── API: History ──────────────────────────────────────────────────────────────
@app.route("/api/history")
@login_required
def api_history():
    uid = current_user_id()
    days = int(request.args.get("days", 7))
    since = (date.today() - timedelta(days=days)).isoformat()

    con = get_db()
    logs = con.execute("""
        SELECT dl.*, m.name as med_name, m.color, s.time_of_day, s.label
        FROM dose_log dl
        JOIN medications m ON m.id = dl.med_id
        JOIN schedules s ON s.id = dl.schedule_id
        WHERE dl.user_id=? AND dl.scheduled_date >= ?
        ORDER BY dl.scheduled_date DESC, s.time_of_day
    """, (uid, since)).fetchall()
    con.close()
    return jsonify([dict(r) for r in logs])

# ── API: Stats ────────────────────────────────────────────────────────────────
@app.route("/api/stats")
@login_required
def api_stats():
    uid = current_user_id()
    today = date.today()
    week_ago = (today - timedelta(days=6)).isoformat()
    today_str = today.isoformat()

    con = get_db()
    total_meds = con.execute(
        "SELECT COUNT(*) FROM medications WHERE user_id=? AND active=1", (uid,)
    ).fetchone()[0]

    taken_today = con.execute(
        "SELECT COUNT(*) FROM dose_log WHERE user_id=? AND scheduled_date=?", (uid, today_str)
    ).fetchone()[0]

    taken_week = con.execute(
        "SELECT COUNT(*) FROM dose_log WHERE user_id=? AND scheduled_date>=?", (uid, week_ago)
    ).fetchone()[0]

    # Count scheduled doses this week
    schedules = con.execute(
        "SELECT days_of_week FROM schedules WHERE user_id=?", (uid,)
    ).fetchall()

    scheduled_week = 0
    for i in range(7):
        day = today - timedelta(days=i)
        wd = str(day.weekday())
        for s in schedules:
            if wd in s["days_of_week"].split(","):
                scheduled_week += 1

    adherence = round((taken_week / scheduled_week * 100) if scheduled_week > 0 else 0)

    con.close()
    return jsonify({
        "total_meds": total_meds,
        "taken_today": taken_today,
        "adherence_7d": adherence,
        "taken_week": taken_week,
        "scheduled_week": scheduled_week,
    })

if __name__ == "__main__":
    init_db()
    app.run(host='127.0.0.1', debug=True, port=5000)
    # app.run(host='0.0.0.0', debug=True, port=5000)
    
