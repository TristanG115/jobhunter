from flask import Flask, render_template, jsonify, request, session, Response
import sqlite3, json, os, threading, csv, io, re
from datetime import datetime
import scraper

app = Flask(__name__)
_secret = os.environ.get("JOBHUNTER_SECRET_KEY")
if not _secret:
    import secrets as _secrets
    _secret = _secrets.token_hex(32)
    print("WARNING: JOBHUNTER_SECRET_KEY not set — generated a random key. "
          "Sessions will be invalidated on every restart. "
          "Set JOBHUNTER_SECRET_KEY in your environment to persist sessions.")
app.secret_key = _secret
DB_PATH = "data/jobs.db"
CREDS_PATH = "credentials/sheets_credentials.json"
os.makedirs("uploads", exist_ok=True)
os.makedirs("credentials", exist_ok=True)

# ─── DATABASE ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs("data", exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                created_at TEXT DEFAULT (datetime('now')),
                resume_text TEXT, resume_filename TEXT, ai_context TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id TEXT NOT NULL,
                title TEXT, company TEXT, location TEXT,
                lat REAL, lng REAL, work_type TEXT,
                salary_min INTEGER, salary_max INTEGER, salary_display TEXT,
                match_score INTEGER DEFAULT -1,
                match_reasons TEXT, description TEXT,
                apply_url TEXT, company_url TEXT, source TEXT,
                date_found TEXT, date_posted TEXT,
                saved INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0,
                notes TEXT DEFAULT '', app_status TEXT DEFAULT 'none',
                is_new INTEGER DEFAULT 1, scrape_batch_id INTEGER DEFAULT 0,
                sheet_row INTEGER DEFAULT NULL,
                UNIQUE(user_id, job_id)
            );
            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                started_at TEXT, finished_at TEXT,
                jobs_found INTEGER DEFAULT 0,
                adzuna_calls INTEGER DEFAULT 0,
                jsearch_calls INTEGER DEFAULT 0,
                ai_calls INTEGER DEFAULT 0,
                status TEXT
            );
            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                day TEXT,
                adzuna_calls INTEGER DEFAULT 0,
                jsearch_calls INTEGER DEFAULT 0,
                ai_calls INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(month)
            );
            CREATE TABLE IF NOT EXISTS api_usage_daily (
                day TEXT PRIMARY KEY,
                adzuna_calls INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS search_profiles (
                user_id INTEGER PRIMARY KEY,
                profile_json TEXT NOT NULL,
                resume_hash TEXT,
                generated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT
            );
            CREATE TABLE IF NOT EXISTS search_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                city TEXT, state TEXT, label TEXT,
                radius_miles INTEGER DEFAULT 30, active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS sheets_sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                synced_at TEXT,
                direction TEXT,
                inserted INTEGER DEFAULT 0,
                updated INTEGER DEFAULT 0,
                pushed INTEGER DEFAULT 0,
                appended INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                status TEXT
            );
        """)
        defaults = {
            "purdue_api_key": "",
            "jsearch_key": "",
            "usajobs_key": "",
            "usajobs_email": "",
            "purdue_api_model": "gpt-oss:120b",
            "purdue_api_url": "https://genai.rcac.purdue.edu/api/chat/completions",
            "jsearch_monthly_limit": "200",
            "sheets_id": "",
            "sheets_auto_sync": "0",
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
        conn.commit()

def get_setting(key, default=""):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
        conn.commit()

def bump_usage(jsearch=0, ai=0):
    month = datetime.now().strftime("%Y-%m")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO api_usage (month, jsearch_calls, ai_calls) VALUES (?,?,?) "
            "ON CONFLICT(month) DO UPDATE SET "
            "jsearch_calls=jsearch_calls+excluded.jsearch_calls, "
            "ai_calls=ai_calls+excluded.ai_calls, "
            "updated_at=datetime('now')",
            (month, jsearch, ai)
        )
        conn.commit()

def get_usage():
    month = datetime.now().strftime("%Y-%m")
    jlimit = int(get_setting("jsearch_monthly_limit", "200"))
    with get_db() as conn:
        mrow = conn.execute("SELECT * FROM api_usage WHERE month=?", (month,)).fetchone()
    j_used = mrow["jsearch_calls"] if mrow else 0
    ai_calls = mrow["ai_calls"] if mrow else 0
    return {
        "month": month,
        "jsearch_used": j_used, "jsearch_limit": jlimit,
        "jsearch_remaining": max(0, jlimit - j_used),
        "jsearch_pct": min(100, round(j_used/jlimit*100)) if jlimit else 0,
        "ai_calls": ai_calls,
    }

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def require_login(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return jsonify({"error": "not_logged_in"}), 401
        return f(*args, **kwargs)
    return decorated

# ─── AUTH ─────────────────────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def login():
    username = (request.json.get("username") or "").strip().lower()
    if not username or len(username) < 2:
        return jsonify({"ok": False, "msg": "Username must be at least 2 characters"})
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            conn.execute("INSERT INTO users (username) VALUES (?)", (username,))
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            # New users start with no locations — they add their own in Settings.
            # This makes the app work for anyone, not just Indiana users.
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"ok": True, "username": user["username"]})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me")
def api_me():
    user = current_user()
    if not user:
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in": True, "id": user["id"], "username": user["username"],
        "has_resume": bool(user["resume_text"]),
        "resume_filename": user["resume_filename"] or "",
        "ai_context": user["ai_context"] or ""
    })

# ─── RESUME ───────────────────────────────────────────────────────────────────

@app.route("/api/resume/upload", methods=["POST"])
@require_login
def upload_resume():
    user = current_user()
    if "file" not in request.files:
        return jsonify({"ok": False, "msg": "No file"})
    f = request.files["file"]
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    try:
        if ext == "pdf":
            import io as _io
            data = f.read()
            try:
                import pdfplumber
                with pdfplumber.open(_io.BytesIO(data)) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            except ImportError:
                import PyPDF2
                reader = PyPDF2.PdfReader(_io.BytesIO(data))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif ext in ("txt","md"):
            text = f.read().decode("utf-8", errors="ignore")
        elif ext in ("doc","docx"):
            import docx2txt, tempfile
            data = f.read()
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(data); tmp_path = tmp.name
            text = docx2txt.process(tmp_path)
            os.unlink(tmp_path)
        else:
            return jsonify({"ok": False, "msg": "Use PDF, TXT, or DOCX"})
        text = text.strip()
        if not text:
            return jsonify({"ok": False, "msg": "Could not extract text"})
        with get_db() as conn:
            conn.execute("UPDATE users SET resume_text=?,resume_filename=? WHERE id=?",
                        (text, f.filename, user["id"]))
            conn.commit()
        return jsonify({"ok": True, "filename": f.filename, "preview": text[:400]})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/api/resume/context", methods=["POST"])
@require_login
def save_context():
    user = current_user()
    ctx = (request.json.get("context") or "").strip()
    with get_db() as conn:
        conn.execute("UPDATE users SET ai_context=? WHERE id=?", (ctx, user["id"]))
        conn.commit()
    return jsonify({"ok": True})

# ─── AI ADVISOR ───────────────────────────────────────────────────────────────

@app.route("/api/ai/recommend", methods=["POST"])
@require_login
def ai_recommend():
    user = current_user()
    if not user["resume_text"]:
        return jsonify({"ok": False, "msg": "Upload your resume first."})
    api_key = get_setting("purdue_api_key")
    if not api_key:
        return jsonify({"ok": False, "msg": "Purdue API key not configured."})
    extra = (request.json.get("context") or user["ai_context"] or "").strip()
    prompt = f"""Analyze this resume and provide structured job search guidance.

RESUME:
{user['resume_text'][:3000]}

CONTEXT: {extra or "None"}

Return ONLY valid JSON, no markdown, no text outside JSON:
{{
  "job_titles": ["10 specific titles matching this resume"],
  "companies": [{{"name":"Company","why":"reason","url":"careers URL if known"}}],
  "job_boards": [{{"name":"Board","url":"https://...","why":"why it fits this candidate"}}],
  "keywords": ["5 search keywords"],
  "advice": "2-3 sentences of honest specific career advice"
}}

Include 10 titles, 6 Indiana/remote companies, 6 job boards (include Dice, Handshake, Built In Indiana, Wellfound, etc.), 5 keywords."""

    try:
        import requests as req
        resp = req.post(
            get_setting("purdue_api_url") or "https://genai.rcac.purdue.edu/api/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": get_setting("purdue_api_model") or "gpt-oss:120b",
                "messages": [
                    {"role": "system", "content": "You are a JSON-only API. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                "stream": False
            },
            timeout=90
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        bump_usage(ai=1)
        content_clean = re.sub(r'^```(?:json)?\s*', '', content)
        content_clean = re.sub(r'\s*```$', '', content_clean).strip()
        obj_match = re.search(r'\{[\s\S]*\}', content_clean)
        if obj_match:
            result = json.loads(obj_match.group())
        else:
            result = {"raw": content}
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

# ─── LOCATIONS ────────────────────────────────────────────────────────────────

def get_search_profile(user_id: int) -> dict | None:
    """Load cached search profile for a user, or None if not generated yet."""
    with get_db() as conn:
        row = conn.execute("SELECT profile_json FROM search_profiles WHERE user_id=?", (user_id,)).fetchone()
    if row:
        try:
            return json.loads(row["profile_json"])
        except Exception:
            return None
    return None


def save_search_profile(user_id: int, profile: dict, resume_hash: str):
    """Cache a generated search profile for a user."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO search_profiles (user_id, profile_json, resume_hash, generated_at) VALUES (?,?,?,datetime('now'))",
            (user_id, json.dumps(profile), resume_hash)
        )
        conn.commit()


def _resume_hash(resume_text: str) -> str:
    import hashlib
    return hashlib.sha256((resume_text or "").encode()).hexdigest()[:16]


@app.route("/api/search_profile", methods=["GET"])
@require_login
def get_profile_route():
    """Return the current user's cached search profile."""
    user = current_user()
    profile = get_search_profile(user["id"])
    if not profile:
        return jsonify({"ok": True, "profile": None, "msg": "No profile generated yet. Run Generate Profile or start a scrape."})
    return jsonify({"ok": True, "profile": profile})


@app.route("/api/search_profile/generate", methods=["POST"])
@require_login
def generate_profile_route():
    """Generate (or regenerate) the AI search profile for this user."""
    user = current_user()
    if not user["resume_text"]:
        return jsonify({"ok": False, "msg": "Upload your resume first."})
    api_key = get_setting("purdue_api_key")
    if not api_key:
        return jsonify({"ok": False, "msg": "AI API key not configured."})

    uid = user["id"]
    with get_db() as conn:
        locations = [dict(r) for r in conn.execute(
            "SELECT * FROM search_locations WHERE user_id=? AND active=1", (uid,)).fetchall()]

    def log(msg):
        pass  # Fire-and-forget; caller gets the result directly

    profile = scraper.generate_search_profile(
        user["resume_text"],
        user["ai_context"] or "",
        locations,
        api_key,
        get_setting("purdue_api_url") or "https://genai.rcac.purdue.edu/api/chat/completions",
        get_setting("purdue_api_model") or "gpt-oss:120b",
        log
    )
    bump_usage(ai=1)
    save_search_profile(uid, profile, _resume_hash(user["resume_text"]))
    return jsonify({"ok": True, "profile": profile})


@app.route("/api/search_profile", methods=["PUT"])
@require_login
def update_profile_route():
    """Manually update specific fields of the search profile."""
    user = current_user()
    profile = get_search_profile(user["id"]) or scraper._fallback_profile()
    updates = request.json or {}
    allowed_keys = {
        "muse_categories", "muse_levels", "remotive_categories",
        "greenhouse_boards", "jsearch_queries", "usajobs_keywords",
        "title_include_keywords", "title_exclude_extra"
    }
    for k, v in updates.items():
        if k in allowed_keys:
            profile[k] = v
    save_search_profile(user["id"], profile, _resume_hash(user.get("resume_text") or ""))
    return jsonify({"ok": True, "profile": profile})

@app.route("/api/locations", methods=["GET"])
@require_login
def get_locations():
    user = current_user()
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM search_locations WHERE user_id=? ORDER BY id", (user["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/locations", methods=["POST"])
@require_login
def add_location():
    user = current_user()
    data = request.json
    city = (data.get("city") or "").strip()
    state = (data.get("state") or "IN").strip().upper()
    if not city:
        return jsonify({"ok": False, "msg": "City required"})
    with get_db() as conn:
        conn.execute(
            "INSERT INTO search_locations (user_id,city,state,label,radius_miles,active) VALUES (?,?,?,?,?,1)",
            (user["id"], city, state, f"{city}, {state}", int(data.get("radius_miles", 30)))
        )
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/locations/<int:loc_id>", methods=["DELETE"])
@require_login
def delete_location(loc_id):
    user = current_user()
    with get_db() as conn:
        conn.execute("DELETE FROM search_locations WHERE id=? AND user_id=?", (loc_id, user["id"]))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/locations/<int:loc_id>/toggle", methods=["POST"])
@require_login
def toggle_location(loc_id):
    user = current_user()
    with get_db() as conn:
        conn.execute("UPDATE search_locations SET active=1-active WHERE id=? AND user_id=?", (loc_id, user["id"]))
        conn.commit()
    return jsonify({"ok": True})

# ─── SEARCH PROFILE (AI-generated per-user scrape configuration) ──────────────

def get_search_profile(user_id: int):
    """Load cached search profile for a user, or None if not generated yet."""
    with get_db() as conn:
        row = conn.execute("SELECT profile_json FROM search_profiles WHERE user_id=?", (user_id,)).fetchone()
    if row:
        try:
            return json.loads(row["profile_json"])
        except Exception:
            return None
    return None


def save_search_profile(user_id: int, profile: dict, resume_hash: str):
    """Cache a generated search profile for a user."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO search_profiles (user_id, profile_json, resume_hash, generated_at) "
            "VALUES (?,?,?,datetime('now'))",
            (user_id, json.dumps(profile), resume_hash)
        )
        conn.commit()


def _resume_hash(resume_text: str) -> str:
    import hashlib
    return hashlib.sha256((resume_text or "").encode()).hexdigest()[:16]


@app.route("/api/search_profile", methods=["GET"])
@require_login
def get_profile_route():
    """Return the current user's cached search profile."""
    user = current_user()
    profile = get_search_profile(user["id"])
    with get_db() as conn:
        row = conn.execute("SELECT generated_at, resume_hash FROM search_profiles WHERE user_id=?",
                           (user["id"],)).fetchone()
    meta = dict(row) if row else {}
    if not profile:
        return jsonify({"ok": True, "profile": None,
                        "msg": "No profile yet — click Generate Profile or run a scrape."})
    return jsonify({"ok": True, "profile": profile, "meta": meta})


@app.route("/api/search_profile/generate", methods=["POST"])
@require_login
def generate_profile_route():
    """Generate (or regenerate) the AI search profile for this user."""
    user = current_user()
    if not user["resume_text"]:
        return jsonify({"ok": False, "msg": "Upload your resume first."})
    api_key = get_setting("purdue_api_key")
    if not api_key:
        return jsonify({"ok": False, "msg": "AI API key not configured."})

    uid = user["id"]
    with get_db() as conn:
        locations = [dict(r) for r in conn.execute(
            "SELECT * FROM search_locations WHERE user_id=? AND active=1", (uid,)).fetchall()]

    logs = []
    profile = scraper.generate_search_profile(
        user["resume_text"],
        user["ai_context"] or "",
        locations,
        api_key,
        get_setting("purdue_api_url") or "https://genai.rcac.purdue.edu/api/chat/completions",
        get_setting("purdue_api_model") or "gpt-oss:120b",
        lambda msg: logs.append(msg)
    )
    bump_usage(ai=1)
    save_search_profile(uid, profile, _resume_hash(user["resume_text"]))
    return jsonify({"ok": True, "profile": profile, "log": logs})


@app.route("/api/search_profile", methods=["PUT"])
@require_login
def update_profile_route():
    """Manually update specific fields of the search profile (for power users)."""
    user = current_user()
    profile = get_search_profile(user["id"]) or scraper._fallback_profile()
    updates = request.json or {}
    allowed_keys = {
        "muse_categories", "muse_levels", "remotive_categories",
        "greenhouse_boards", "jsearch_queries", "usajobs_keywords",
        "title_include_keywords", "title_exclude_extra"
    }
    for k, v in updates.items():
        if k in allowed_keys:
            profile[k] = v
    save_search_profile(user["id"], profile, _resume_hash(user.get("resume_text") or ""))
    return jsonify({"ok": True, "profile": profile})

# ─── JOBS ─────────────────────────────────────────────────────────────────────

@app.route("/api/jobs")
@require_login
def api_jobs():
    user = current_user()
    wt          = request.args.get("work_type","")
    ms          = request.args.get("min_score","0")
    search      = request.args.get("search","")
    saved       = request.args.get("saved","")
    status      = request.args.get("app_status","")
    sort        = request.args.get("sort","match_score")
    hide_uns    = request.args.get("hide_unscored","0")
    source_f    = request.args.get("source","")
    min_score   = int(ms) if ms.lstrip("-").isdigit() else 0

    query = "SELECT * FROM jobs WHERE hidden=0 AND user_id=?"
    params = [user["id"]]
    if wt:      query += " AND work_type=?"; params.append(wt)
    if min_score > 0: query += " AND match_score>=?"; params.append(min_score)
    if hide_uns == "1": query += " AND match_score>=0"
    if search:
        query += " AND (title LIKE ? OR company LIKE ? OR location LIKE ? OR notes LIKE ?)"
        s = f"%{search}%"; params += [s,s,s,s]
    if saved == "1": query += " AND saved=1"
    if status:  query += " AND app_status=?"; params.append(status)
    if source_f: query += " AND source=?"; params.append(source_f)

    sorts = {
        "match_score": "CASE WHEN match_score<0 THEN 1 ELSE 0 END, match_score DESC",
        "date_found":  "date_found DESC",
        "salary":      "salary_max DESC",
        "title":       "title ASC",
        "company":     "company ASC",
    }
    query += f" ORDER BY {sorts.get(sort, sorts['match_score'])}"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/jobs/<int:job_id>/save", methods=["POST"])
@require_login
def toggle_save(job_id):
    user = current_user()
    with get_db() as conn:
        conn.execute("UPDATE jobs SET saved=? WHERE id=? AND user_id=?",
                    (1 if request.json.get("saved") else 0, job_id, user["id"]))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/jobs/<int:job_id>/hide", methods=["POST"])
@require_login
def hide_job(job_id):
    user = current_user()
    with get_db() as conn:
        conn.execute("UPDATE jobs SET hidden=1 WHERE id=? AND user_id=?", (job_id, user["id"]))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/jobs/<int:job_id>/notes", methods=["POST"])
@require_login
def update_notes(job_id):
    user = current_user()
    notes = (request.json.get("notes") or "").strip()
    with get_db() as conn:
        conn.execute("UPDATE jobs SET notes=? WHERE id=? AND user_id=?", (notes, job_id, user["id"]))
        conn.commit()
    # Auto-push to sheet if enabled
    if get_setting("sheets_auto_sync") == "1":
        _push_job_to_sheet_bg(job_id, user["id"])
    return jsonify({"ok": True})

@app.route("/api/jobs/<int:job_id>/status", methods=["POST"])
@require_login
def update_status(job_id):
    user = current_user()
    status = request.json.get("status","none")
    valid = ["none","interested","applied","interview","offer","rejected"]
    if status not in valid:
        return jsonify({"ok": False, "msg": "Invalid status"})
    with get_db() as conn:
        conn.execute("UPDATE jobs SET app_status=? WHERE id=? AND user_id=?", (status, job_id, user["id"]))
        conn.commit()
    # Auto-push to sheet if enabled
    if get_setting("sheets_auto_sync") == "1":
        _push_job_to_sheet_bg(job_id, user["id"])
    return jsonify({"ok": True})

@app.route("/api/jobs/<int:job_id>/mark_seen", methods=["POST"])
@require_login
def mark_seen(job_id):
    user = current_user()
    with get_db() as conn:
        conn.execute("UPDATE jobs SET is_new=0 WHERE id=? AND user_id=?", (job_id, user["id"]))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/jobs/export")
@require_login
def export_jobs():
    user = current_user()
    saved_only = request.args.get("saved_only","0") == "1"
    with get_db() as conn:
        q = "SELECT * FROM jobs WHERE hidden=0 AND user_id=?" + (" AND saved=1" if saved_only else "") + " ORDER BY match_score DESC"
        rows = conn.execute(q, (user["id"],)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Title","Company","Location","Work Type","Salary","Match Score",
                     "Match Reasons","Application Status","Notes","Apply URL","Source","Date Found"])
    for r in rows:
        writer.writerow([r["title"],r["company"],r["location"],r["work_type"],
                        r["salary_display"],r["match_score"],r["match_reasons"],
                        r["app_status"],r["notes"],r["apply_url"],r["source"],
                        (r["date_found"] or "")[:10]])
    output.seek(0)
    fname = f"jobs_{'saved_' if saved_only else ''}export_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(output.getvalue(), mimetype="text/csv",
                   headers={"Content-Disposition": f"attachment; filename={fname}"})

@app.route("/api/jobs/rescore", methods=["POST"])
@require_login
def rescore_jobs():
    user = current_user()
    uid = user["id"]
    if scrape_status.get(uid, {}).get("running"):
        return jsonify({"ok": False, "msg": "A scrape is already running"})
    if not user["resume_text"]:
        return jsonify({"ok": False, "msg": "Upload your resume first"})
    purdue_key = get_setting("purdue_api_key")
    if not purdue_key:
        return jsonify({"ok": False, "msg": "Purdue API key not set"})
    job_ids = request.json.get("job_ids", [])
    with get_db() as conn:
        if job_ids:
            placeholders = ",".join("?" * len(job_ids))
            jobs = [dict(r) for r in conn.execute(
                f"SELECT * FROM jobs WHERE id IN ({placeholders}) AND user_id=?",
                job_ids + [uid]).fetchall()]
        else:
            jobs = [dict(r) for r in conn.execute(
                "SELECT * FROM jobs WHERE match_score=-1 AND user_id=? AND hidden=0 LIMIT 100",
                (uid,)).fetchall()]
    if not jobs:
        return jsonify({"ok": False, "msg": "No unscored jobs found"})
    user_dict = dict(user)
    scrape_status[uid] = {"running": True, "progress": "Rescoring...", "log": []}
    t = threading.Thread(target=run_rescore, args=(uid, user_dict, purdue_key, jobs))
    t.daemon = True; t.start()
    return jsonify({"ok": True, "count": len(jobs)})

def run_rescore(uid, user, purdue_key, jobs):
    def log(msg):
        scrape_status[uid]["progress"] = msg
        scrape_status[uid]["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    try:
        log(f"Rescoring {len(jobs)} jobs...")
        matched, ai_calls = scraper.match_jobs(
            jobs, purdue_key, user["resume_text"], user.get("ai_context") or "",
            get_setting("purdue_api_url") or "https://genai.rcac.purdue.edu/api/chat/completions",
            get_setting("purdue_api_model") or "gpt-oss:120b", log)
        bump_usage(ai=ai_calls)
        with get_db() as conn:
            for job in matched:
                conn.execute(
                    "UPDATE jobs SET match_score=?,match_reasons=?,work_type=? WHERE id=? AND user_id=?",
                    (job["match_score"],job["match_reasons"],job["work_type"],job["id"],uid))
            conn.commit()
        log(f"✓ Rescored {len(matched)} jobs.")
    except Exception as e:
        log(f"ERROR: {e}")
    finally:
        scrape_status[uid]["running"] = False

@app.route("/api/stats")
@require_login
def api_stats():
    user = current_user()
    uid = user["id"]
    with get_db() as conn:
        total    = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE hidden=0 AND user_id=?", (uid,)).fetchone()["c"]
        saved    = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE saved=1 AND hidden=0 AND user_id=?", (uid,)).fetchone()["c"]
        new_c    = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE is_new=1 AND hidden=0 AND user_id=?", (uid,)).fetchone()["c"]
        unscored = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE match_score=-1 AND hidden=0 AND user_id=?", (uid,)).fetchone()["c"]
        sc_rows  = conn.execute("SELECT app_status, COUNT(*) as c FROM jobs WHERE hidden=0 AND user_id=? GROUP BY app_status", (uid,)).fetchall()
        last_log = conn.execute("SELECT * FROM scrape_log WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        last_sync= conn.execute("SELECT * FROM sheets_sync_log WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    st = scrape_status.get(uid, {})
    return jsonify({
        "total": total, "saved": saved, "new_count": new_c, "unscored": unscored,
        "status_counts": {r["app_status"]: r["c"] for r in sc_rows},
        "scrape_running": st.get("running", False),
        "scrape_progress": st.get("progress",""),
        "last_log": dict(last_log) if last_log else None,
        "last_sync": dict(last_sync) if last_sync else None,
        "api_usage": get_usage(),
        "sheets_configured": bool(get_setting("sheets_id") and os.path.exists(CREDS_PATH)),
    })

# ─── SCRAPE ───────────────────────────────────────────────────────────────────

scrape_status = {}

@app.route("/api/scrape", methods=["POST"])
@require_login
def trigger_scrape():
    user = current_user()
    uid = user["id"]
    if scrape_status.get(uid, {}).get("running"):
        return jsonify({"ok": False, "msg": "Scrape already running"})
    if not user["resume_text"]:
        return jsonify({"ok": False, "msg": "Upload your resume first."})

    jsearch_key   = get_setting("jsearch_key")
    usajobs_key   = get_setting("usajobs_key")
    usajobs_email = get_setting("usajobs_email")
    purdue_key    = get_setting("purdue_api_key")

    if not purdue_key:
        return jsonify({"ok": False, "msg": "Purdue API key not set."})

    usage = get_usage()
    skip_jsearch = False
    if jsearch_key and usage["jsearch_remaining"] < 5:
        skip_jsearch = True

    with get_db() as conn:
        locations = [dict(r) for r in conn.execute(
            "SELECT * FROM search_locations WHERE user_id=? AND active=1", (uid,)).fetchall()]
    if not locations:
        return jsonify({"ok": False, "msg": "No active search locations."})

    with get_db() as conn:
        last = conn.execute("SELECT MAX(scrape_batch_id) as m FROM jobs WHERE user_id=?", (uid,)).fetchone()
        batch_id = (last["m"] or 0) + 1

    # Load or generate search profile
    cached_profile = get_search_profile(uid)
    resume_hash = _resume_hash(user["resume_text"])
    # Regenerate if no profile exists, or if resume has changed since last generation
    if not cached_profile:
        # Will be generated at the start of run_scrape
        cached_profile = None
    else:
        with get_db() as conn:
            row = conn.execute("SELECT resume_hash FROM search_profiles WHERE user_id=?", (uid,)).fetchone()
        if row and row["resume_hash"] != resume_hash:
            cached_profile = None  # Resume changed — regenerate

    user_dict = dict(user)
    scrape_status[uid] = {"running": True, "progress": "Starting...", "log": [], "batch_id": batch_id}
    t = threading.Thread(target=run_scrape,
        args=(uid, user_dict, usajobs_key, usajobs_email, jsearch_key, purdue_key,
              locations, batch_id, skip_jsearch, cached_profile))
    t.daemon = True; t.start()
    return jsonify({"ok": True})

def run_scrape(uid, user, usajobs_key, usajobs_email, jsearch_key, purdue_key,
               locations, batch_id, skip_jsearch, search_profile=None):
    started = datetime.now().isoformat()
    jobs_found = jsearch_calls = ai_calls = 0
    source_counts = {}

    def log(msg):
        scrape_status[uid]["progress"] = msg
        scrape_status[uid]["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    try:
        # Generate/load search profile if needed
        if search_profile is None:
            log("Generating personalized search profile from resume...")
            search_profile = scraper.generate_search_profile(
                user["resume_text"],
                user.get("ai_context") or "",
                locations,
                purdue_key,
                get_setting("purdue_api_url") or "https://genai.rcac.purdue.edu/api/chat/completions",
                get_setting("purdue_api_model") or "gpt-oss:120b",
                log
            )
            bump_usage(ai=1)
            save_search_profile(uid, search_profile, _resume_hash(user["resume_text"]))
        else:
            log("Using cached search profile...")

        log("Fetching jobs from all free sources (Muse, Remotive, Greenhouse, USAJobs, JSearch)...")
        jobs, source_counts = scraper.scrape_jobs(
            usajobs_key, usajobs_email, jsearch_key, locations, log,
            skip_jsearch, search_profile=search_profile)

        jsearch_calls = source_counts.get("jsearch", 0)

        with get_db() as conn:
            existing = set(r["job_id"] for r in conn.execute(
                "SELECT job_id FROM jobs WHERE user_id=?", (uid,)).fetchall())

        new_jobs = [j for j in jobs if j.get("job_id") not in existing]
        log(f"Found {len(jobs)} total, {len(new_jobs)} new. AI matching...")

        if new_jobs:
            matched, ai_calls = scraper.match_jobs(
                new_jobs, purdue_key, user["resume_text"], user.get("ai_context") or "",
                get_setting("purdue_api_url") or "https://genai.rcac.purdue.edu/api/chat/completions",
                get_setting("purdue_api_model") or "gpt-oss:120b", log)

            with get_db() as conn:
                for job in matched:
                    try:
                        conn.execute("""INSERT OR IGNORE INTO jobs
                            (user_id,job_id,title,company,location,lat,lng,work_type,
                             salary_min,salary_max,salary_display,match_score,match_reasons,
                             description,apply_url,company_url,source,date_found,date_posted,
                             is_new,scrape_batch_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                            (uid,job.get("job_id"),job.get("title"),job.get("company"),
                             job.get("location"),job.get("lat"),job.get("lng"),job.get("work_type"),
                             job.get("salary_min"),job.get("salary_max"),job.get("salary_display"),
                             job.get("match_score"),job.get("match_reasons"),job.get("description"),
                             job.get("apply_url"),job.get("company_url"),job.get("source"),
                             datetime.now().isoformat(),job.get("date_posted"),batch_id))
                        jobs_found += 1
                    except Exception as e:
                        log(f"DB: {e}")
                conn.commit()

        bump_usage(jsearch=jsearch_calls, ai=ai_calls)

        with get_db() as conn:
            conn.execute(
                "INSERT INTO scrape_log (user_id,started_at,finished_at,jobs_found,jsearch_calls,ai_calls,status) VALUES (?,?,?,?,?,?,?)",
                (uid,started,datetime.now().isoformat(),jobs_found,jsearch_calls,ai_calls,"success"))
            conn.commit()

        src_summary = " | ".join(f"{k.capitalize()}:{v}" for k,v in source_counts.items() if v)
        log(f"✓ Done! {jobs_found} new jobs saved. Sources: {src_summary}")

    except Exception as e:
        bump_usage(jsearch=jsearch_calls, ai=ai_calls)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO scrape_log (user_id,started_at,finished_at,jobs_found,jsearch_calls,ai_calls,status) VALUES (?,?,?,?,?,?,?)",
                (uid,started,datetime.now().isoformat(),jobs_found,jsearch_calls,ai_calls,f"error: {e}"))
            conn.commit()
        log(f"ERROR: {e}")
    finally:
        scrape_status[uid]["running"] = False

@app.route("/api/scrape/status")
@require_login
def scrape_status_route():
    user = current_user()
    return jsonify(scrape_status.get(user["id"], {"running": False, "progress": "", "log": []}))

@app.route("/api/scrape/log")
@require_login
def scrape_log_route():
    user = current_user()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM scrape_log WHERE user_id=? ORDER BY id DESC LIMIT 20", (user["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])

# ─── GOOGLE SHEETS ────────────────────────────────────────────────────────────

@app.route("/api/sheets/verify", methods=["POST"])
@require_login
def sheets_verify():
    import sheets_sync
    sheet_id = get_setting("sheets_id")
    if not sheet_id:
        return jsonify({"ok": False, "msg": "No Sheet ID configured in Settings."})
    result = sheets_sync.verify_connection(sheet_id, CREDS_PATH)
    return jsonify(result)

@app.route("/api/sheets/sync_from", methods=["POST"])
@require_login
def sheets_sync_from():
    """Pull from Google Sheets → JobHunter DB."""
    import sheets_sync
    user = current_user()
    uid = user["id"]
    sheet_id = get_setting("sheets_id")
    if not sheet_id:
        return jsonify({"ok": False, "msg": "No Sheet ID configured."})
    if not os.path.exists(CREDS_PATH):
        return jsonify({"ok": False, "msg": "credentials/sheets_credentials.json not found."})
    try:
        with get_db() as conn:
            result = sheets_sync.sync_from_sheet(sheet_id, CREDS_PATH, conn, uid)
            conn.execute(
                "INSERT INTO sheets_sync_log (user_id,synced_at,direction,inserted,updated,status) VALUES (?,?,?,?,?,?)",
                (uid, datetime.now().isoformat(), "from_sheet",
                 result["inserted"], result["updated"], "success"))
            conn.commit()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/api/sheets/sync_to", methods=["POST"])
@require_login
def sheets_sync_to():
    """Push JobHunter → Google Sheets."""
    import sheets_sync
    user = current_user()
    uid = user["id"]
    sheet_id = get_setting("sheets_id")
    if not sheet_id:
        return jsonify({"ok": False, "msg": "No Sheet ID configured."})
    if not os.path.exists(CREDS_PATH):
        return jsonify({"ok": False, "msg": "credentials/sheets_credentials.json not found."})
    job_ids = request.json.get("job_ids", []) if request.json else []
    try:
        with get_db() as conn:
            result = sheets_sync.sync_to_sheet(sheet_id, CREDS_PATH, conn, uid, job_ids or None)
            conn.execute(
                "INSERT INTO sheets_sync_log (user_id,synced_at,direction,pushed,appended,errors,status) VALUES (?,?,?,?,?,?,?)",
                (uid, datetime.now().isoformat(), "to_sheet",
                 result["pushed"], result["appended"], result["errors"], "success"))
            conn.commit()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/api/sheets/log")
@require_login
def sheets_log():
    user = current_user()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM sheets_sync_log WHERE user_id=? ORDER BY id DESC LIMIT 20", (user["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/sheets/upload_creds", methods=["POST"])
@require_login
def upload_creds():
    """Accept the service account JSON file upload."""
    if "file" not in request.files:
        return jsonify({"ok": False, "msg": "No file"})
    f = request.files["file"]
    if not f.filename.endswith(".json"):
        return jsonify({"ok": False, "msg": "Must be a .json file"})
    try:
        data = json.load(f)
        # Validate it looks like a service account
        if "client_email" not in data or "private_key" not in data:
            return jsonify({"ok": False, "msg": "Doesn't look like a service account JSON. Check the file."})
        os.makedirs("credentials", exist_ok=True)
        with open(CREDS_PATH, "w") as out:
            json.dump(data, out)
        return jsonify({"ok": True, "service_account_email": data["client_email"]})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

def _push_job_to_sheet_bg(job_id, user_id):
    """Background thread to push a single job update to sheet."""
    def _do():
        import sheets_sync
        sheet_id = get_setting("sheets_id")
        if not sheet_id or not os.path.exists(CREDS_PATH):
            return
        try:
            with get_db() as conn:
                sheets_sync.sync_to_sheet(sheet_id, CREDS_PATH, conn, user_id, [job_id])
        except Exception:
            pass
    t = threading.Thread(target=_do)
    t.daemon = True; t.start()

# ─── SETTINGS ─────────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
@require_login
def get_settings():
    def mask(v): return (v[:4]+"..."+v[-3:]) if len(v or "") > 8 else ("(set)" if v else "")
    creds_exists = os.path.exists(CREDS_PATH)
    svc_email = ""
    if creds_exists:
        try:
            with open(CREDS_PATH) as f:
                svc_email = json.load(f).get("client_email","")
        except Exception:
            pass
    return jsonify({
        "purdue_api_key":       mask(get_setting("purdue_api_key")),
        "jsearch_key":          mask(get_setting("jsearch_key")),
        "usajobs_key":          mask(get_setting("usajobs_key")),
        "usajobs_email":        get_setting("usajobs_email"),
        "purdue_api_model":     get_setting("purdue_api_model") or "gpt-oss:120b",
        "purdue_api_url":       get_setting("purdue_api_url") or "https://genai.rcac.purdue.edu/api/chat/completions",
        "jsearch_monthly_limit":get_setting("jsearch_monthly_limit") or "200",
        "sheets_id":            get_setting("sheets_id"),
        "sheets_auto_sync":     get_setting("sheets_auto_sync") or "0",
        "creds_exists":         creds_exists,
        "service_account_email":svc_email,
    })

@app.route("/api/settings", methods=["POST"])
@require_login
def save_settings():
    data = request.json
    for k in ["purdue_api_key","jsearch_key","usajobs_key","usajobs_email",
              "purdue_api_model","purdue_api_url","jsearch_monthly_limit",
              "sheets_id","sheets_auto_sync"]:
        if k in data and data[k] is not None:
            val = str(data[k])
            if "..." not in val and val != "(set)":
                set_setting(k, val)
    return jsonify({"ok": True})

@app.route("/api/usage")
@require_login
def api_usage_route():
    return jsonify(get_usage())

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
