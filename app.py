from dotenv import load_dotenv
load_dotenv()

import os
import sqlite3
import json
from datetime import datetime, date, timedelta
from flask import Flask, redirect, url_for, session, render_template, jsonify, request
from authlib.integrations.flask_client import OAuth
from functools import wraps

# biip for structured GS1/GTIN/NDC barcode parsing
try:
    import biip as biip_lib
    BIIP_AVAILABLE = True
except ImportError:
    BIIP_AVAILABLE = False

# pyzbar for server-side barcode decoding (needs libzbar installed)
try:
    from pyzbar import pyzbar
    from PIL import Image
    import io
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

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
            ndc         TEXT,
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
    # Migrate existing DBs — add ndc column if missing
    try:
        con.execute("ALTER TABLE medications ADD COLUMN ndc TEXT")
        con.commit()
    except Exception:
        pass  # Column already exists
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
    print(f"Redirect URI being sent: {redirect_uri}")
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
        "INSERT INTO medications (user_id, name, dosage, notes, color, ndc, created_at) VALUES (?,?,?,?,?,?,?)",
        (uid, data["name"], data.get("dosage",""), data.get("notes",""), data.get("color","#4F86C6"), data.get("ndc",""), now)
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
        "UPDATE medications SET name=?, dosage=?, notes=?, color=?, ndc=? WHERE id=? AND user_id=?",
        (data["name"], data.get("dosage",""), data.get("notes",""), data.get("color","#4F86C6"), data.get("ndc",""), med_id, uid)
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

# ── Barcode → NDC extraction (biip) ──────────────────────────────────────────
def parse_ndc_from_barcode(raw: str) -> dict:
    """
    Use biip to parse a scanned barcode string and extract:
      - ndc: 10 or 11 digit NDC string (no dashes)
      - gtin: raw GTIN value if found
      - expiry: expiration date string (YYYY-MM-DD) if present
      - lot: lot/batch number if present
    Falls back to treating the raw value as an NDC if it's 10-11 digits.
    """
    result_data = {'ndc': None, 'gtin': None, 'expiry': None, 'lot': None, 'raw': raw}

    if BIIP_AVAILABLE:
        try:
            parsed = biip_lib.parse(raw)
            gtin_value = None

            # GS1 message (GS1-128, DataMatrix) — has AI element strings
            if parsed.gs1_message:
                for es in parsed.gs1_message.element_strings:
                    ai = es.ai.ai
                    if ai == '01':      # GTIN
                        gtin_value = es.value
                    elif ai == '17':    # Expiry date
                        result_data['expiry'] = str(es.date) if es.date else es.value
                    elif ai == '10':    # Lot/batch
                        result_data['lot'] = es.value

            # Plain GTIN (UPC-A, EAN-13, EAN-14)
            if parsed.gtin:
                gtin_value = parsed.gtin.value

            # Convert GTIN → NDC
            if gtin_value:
                result_data['gtin'] = gtin_value
                if len(gtin_value) == 14:
                    # GTIN-14 pharma format: indicator(1) + NDC(11) + check(1)
                    # Drop first and last digit, strip leading zeros, re-pad to 11
                    core = gtin_value[1:-1]           # 12 digits
                    ndc = core.lstrip('0') or core
                    result_data['ndc'] = ndc.zfill(11)
                elif len(gtin_value) == 12:
                    # UPC-A: number_system(1) + NDC(10) + check(1)
                    result_data['ndc'] = gtin_value[1:-1]  # 10 digits

        except Exception:
            pass  # fall through to raw fallback

    # Fallback: if raw is 10-11 digits (or dashed NDC), use it directly
    if not result_data['ndc']:
        digits = raw.replace('-', '').replace(' ', '')
        if digits.isdigit() and len(digits) in (10, 11):
            result_data['ndc'] = digits

    return result_data


# ── API: Barcode scan (pyzbar) ────────────────────────────────────────────────
@app.route("/api/scan/status")
@login_required
def api_scan_status():
    return jsonify({"available": PYZBAR_AVAILABLE})

@app.route("/api/scan", methods=["POST"])
@login_required
def api_scan():
    """
    Accept a JPEG/PNG frame from the browser camera and decode barcodes with pyzbar.
    Returns the first barcode found, or 404 if none detected.
    """
    if not PYZBAR_AVAILABLE:
        return jsonify({"error": "pyzbar not installed on server"}), 503

    if "frame" not in request.files:
        return jsonify({"error": "No frame uploaded"}), 400

    file = request.files["frame"]
    try:
        img = Image.open(io.BytesIO(file.read())).convert("RGB")
    except Exception as e:
        return jsonify({"error": f"Could not read image: {e}"}), 400

    # Decode with pyzbar — supports UPC, EAN, CODE128, DataMatrix, QR, PDF417, etc.
    decoded = pyzbar.decode(img)

    if not decoded:
        return jsonify({"found": False}), 404

    # Return all barcodes found, sorted by data length (prefer shorter/cleaner codes)
    results = []
    for obj in decoded:
        try:
            data = obj.data.decode("utf-8")
        except Exception:
            data = obj.data.decode("latin-1")
        parsed = parse_ndc_from_barcode(data)
        results.append({
            "data":   data,
            "format": obj.type,
            "ndc":    parsed['ndc'],
            "gtin":   parsed['gtin'],
            "expiry": parsed['expiry'],
            "lot":    parsed['lot'],
        })

    primary = results[0]
    return jsonify({
        "found":   True,
        "barcode": primary["data"],
        "format":  primary["format"],
        "ndc":     primary["ndc"],
        "gtin":    primary["gtin"],
        "expiry":  primary["expiry"],
        "lot":     primary["lot"],
        "all":     results,
    })

# ── API: OpenFDA drug lookup ───────────────────────────────────────────────────
@app.route("/api/fda/lookup")
@login_required
def api_fda_lookup():
    """
    Proxy OpenFDA drug lookups. Accepts:
      ?ndc=XXXXXXXXXX   — lookup by NDC / UPC barcode
      ?name=DRUGNAME    — fallback text search by brand/generic name
    Returns simplified drug info or 404.
    """
    import urllib.request
    import urllib.parse

    ndc  = request.args.get("ndc", "").strip()
    name = request.args.get("name", "").strip()

    def fetch_fda(url):
        import sys
        api_key = os.environ.get("OPENFDA_API_KEY", "")
        if api_key:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api_key={api_key}"
        try:
            print(f"FDA lookup: {url}", file=sys.stderr)
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 MedMinder/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=8) as r:
                body = json.loads(r.read())
                print(f"FDA result count: {len(body.get('results', []))}", file=sys.stderr)
                return body
        except urllib.error.HTTPError as e:
            print(f"FDA HTTP {e.code}: {url}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"FDA error: {e}: {url}", file=sys.stderr)
            return None

    def parse_result(data):
        """Extract the most useful fields from an openFDA drug result."""
        if not data or not data.get("results"):
            return None
        r = data["results"][0]
        openfda = r.get("openfda", {})

        # /ndc.json has brand_name/generic_name directly on the result
        # /label.json has them nested under openfda{}
        brand   = r.get("brand_name") or (openfda.get("brand_name") or [""])[0]
        generic = r.get("generic_name") or (openfda.get("generic_name") or [""])[0]

        drug_name = (brand or generic or "").title() or None
        generic_clean = (generic or "").title() or None

        # Dosage: active ingredients with strengths, plus dosage form
        ingredients = r.get("active_ingredients", [])
        if ingredients:
            parts = [f"{i['name'].title()} {i['strength']}" for i in ingredients]
            dosage = ", ".join(parts)
            form = r.get("dosage_form", "")
            if form:
                dosage += f" — {form.lower()}"
        else:
            strengths = openfda.get("strength", [])
            forms     = r.get("dosage_form") or (openfda.get("dosage_form") or [""])[0]
            dosage = ", ".join(filter(None, [
                strengths[0] if strengths else None,
                forms.lower() if forms else None,
            ])) or None

        # NDC — prefer package_ndc, fall back to product_ndc
        packaging = r.get("packaging", [])
        ndc_val = (
            packaging[0].get("package_ndc") if packaging
            else r.get("product_ndc")
            or (openfda.get("product_ndc") or openfda.get("package_ndc") or [""])[0]
            or None
        )

        # Route
        route = r.get("route", [])
        route_str = route[0].lower() if route else None

        # Purpose / indications
        purpose = None
        if r.get("purpose"):
            purpose = r["purpose"][0][:200] if isinstance(r["purpose"], list) else str(r["purpose"])[:200]
        elif r.get("indications_and_usage"):
            raw = r["indications_and_usage"]
            purpose = (raw[0] if isinstance(raw, list) else raw)[:200]
        elif r.get("pharm_class"):
            purpose = ", ".join(r["pharm_class"][:3])

        return {
            "name":    drug_name,
            "generic": generic_clean,
            "dosage":  dosage,
            "ndc":     ndc_val,
            "route":   route_str,
            "purpose": purpose,
        }

    result = None

    # ── 1. NDC lookup ──────────────────────────────────────────────────────────
    if ndc:
        clean = ndc.replace("-", "").replace(" ", "")

        # OpenFDA NDC formats (labeler-product or labeler-product-package):
        # UPC barcodes are 11-12 digits; NDC is typically embedded as 5+4 or 5+3+2
        def ndc_variants(raw):
            variants = set()
            variants.add(raw)  # plain digits
            n = len(raw)
            if n >= 10:
                # 5-4-2 → full 11 digit
                variants.add(f"{raw[0:5]}-{raw[5:9]}-{raw[9:11]}" if n >= 11 else "")
                # 5-3-2
                variants.add(f"{raw[0:5]}-{raw[5:8]}-{raw[8:10]}" if n >= 10 else "")
                # Product NDC only (no package): 5-4 and 5-3
                variants.add(f"{raw[0:5]}-{raw[5:9]}")
                variants.add(f"{raw[0:5]}-{raw[5:8]}")
                # 4-4-2
                variants.add(f"{raw[0:4]}-{raw[4:8]}-{raw[8:10]}" if n >= 10 else "")
                # Strip leading zero (FDA sometimes stores without it)
                if raw.startswith("0"):
                    stripped = raw[1:]
                    variants.add(f"{stripped[0:5]}-{stripped[5:9]}" if len(stripped) >= 9 else "")
                    variants.add(f"{stripped[0:4]}-{stripped[4:8]}-{stripped[8:10]}" if len(stripped) >= 10 else "")
            variants.discard("")
            return list(variants)

        variants = ndc_variants(clean)
        for variant in variants:
            for endpoint in ("ndc", "label"):
                if endpoint == "ndc":
                    for field in ("package_ndc", "product_ndc"):
                        url = (
                            f"https://api.fda.gov/drug/ndc.json?"
                            f"search={field}:\"{urllib.parse.quote(variant)}\"&limit=1"
                        )
                        data = fetch_fda(url)
                        result = parse_result(data)
                        if result:
                            break
                else:
                    for field in ("openfda.package_ndc", "openfda.product_ndc"):
                        url = (
                            f"https://api.fda.gov/drug/label.json?"
                            f"search={field}:\"{urllib.parse.quote(variant)}\"&limit=1"
                        )
                        data = fetch_fda(url)
                        result = parse_result(data)
                        if result:
                            break
                if result:
                    break
            if result:
                break

    # ── 2. Name fallback ───────────────────────────────────────────────────────
    if not result and name:
        encoded = urllib.parse.quote(name)
        for field in ("brand_name", "generic_name"):
            url = (
                f"https://api.fda.gov/drug/ndc.json?"
                f"search={field}:{encoded}&limit=1"
            )
            data = fetch_fda(url)
            result = parse_result(data)
            if result:
                break
        # Also try label endpoint
        if not result:
            for field in ("openfda.brand_name", "openfda.generic_name"):
                url = (
                    f"https://api.fda.gov/drug/label.json?"
                    f"search={field}:{encoded}&limit=1"
                )
                data = fetch_fda(url)
                result = parse_result(data)
                if result:
                    break

    if result:
        return jsonify(result)
    return jsonify({"error": "Not found"}), 404

# ── PWA routes ────────────────────────────────────────────────────────────────
@app.route("/sw.js")
def service_worker():
    from flask import send_from_directory
    response = send_from_directory(app.static_folder, 'sw.js')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route("/manifest.json")
def manifest():
    from flask import send_from_directory
    return send_from_directory(app.static_folder, 'manifest.json')

if __name__ == "__main__":
    init_db()
    app.run('0.0.0.0', debug=True, port=5000)
