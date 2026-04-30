from dotenv import load_dotenv
load_dotenv()

import os
import sqlite3
import json
from datetime import datetime
from flask import Flask, redirect, url_for, session, render_template, jsonify
from authlib.integrations.flask_client import OAuth

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")

# ── OAuth setup ──────────────────────────────────────────────────────────────
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ── Database ─────────────────────────────────────────────────────────────────
DB_PATH = "users.db"

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id     TEXT    UNIQUE NOT NULL,
            email         TEXT    NOT NULL,
            name          TEXT,
            picture       TEXT,
            given_name    TEXT,
            family_name   TEXT,
            locale        TEXT,
            created_at    TEXT    NOT NULL,
            last_login    TEXT    NOT NULL
        )
    """)
    con.commit()
    con.close()

def upsert_user(info: dict) -> dict:
    """Insert or update a user from Google userinfo. Returns the DB row."""
    now = datetime.utcnow().isoformat()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""
        INSERT INTO users (google_id, email, name, picture, given_name, family_name, locale, created_at, last_login)
        VALUES (:sub, :email, :name, :picture, :given_name, :family_name, :locale, :now, :now)
        ON CONFLICT(google_id) DO UPDATE SET
            email       = excluded.email,
            name        = excluded.name,
            picture     = excluded.picture,
            given_name  = excluded.given_name,
            family_name = excluded.family_name,
            locale      = excluded.locale,
            last_login  = excluded.last_login
    """, {
        "sub":         info.get("sub"),
        "email":       info.get("email"),
        "name":        info.get("name"),
        "picture":     info.get("picture"),
        "given_name":  info.get("given_name"),
        "family_name": info.get("family_name"),
        "locale":      info.get("locale"),
        "now":         now,
    })
    con.commit()
    row = con.execute("SELECT * FROM users WHERE google_id = ?", (info["sub"],)).fetchone()
    con.close()
    return dict(row)

def get_all_users() -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM users ORDER BY last_login DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    user = session.get("user")
    return render_template("index.html", user=user)

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

@app.route("/dashboard")
def dashboard():
    user = session.get("user")
    if not user:
        return redirect(url_for("index"))
    return render_template("dashboard.html", user=user)

@app.route("/api/users")
def api_users():
    if not session.get("user"):
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(get_all_users())

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
