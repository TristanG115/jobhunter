from flask import Flask, render_template, jsonify, request
import sqlite3, json, os, threading, time
from datetime import datetime
import scraper

app = Flask(__name__)
DB_PATH = "data/jobs.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs("data", exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE,
                title TEXT,
                company TEXT,
                location TEXT,
                lat REAL,
                lng REAL,
                work_type TEXT,
                salary_min INTEGER,
                salary_max INTEGER,
                salary_display TEXT,
                match_score INTEGER,
                match_reasons TEXT,
                description TEXT,
                apply_url TEXT,
                company_url TEXT,
                source TEXT,
                date_found TEXT,
                date_posted TEXT,
                saved INTEGER DEFAULT 0,
                hidden INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                finished_at TEXT,
                jobs_found INTEGER,
                status TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        # Default settings
        defaults = {
            "openai_key": "",
            "jsearch_key": "",
            "auto_scrape": "false",
            "scrape_interval_hours": "24",
            "last_scrape": ""
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()

scrape_status = {"running": False, "progress": "", "log": []}

def get_setting(key):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""

def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/jobs")
def api_jobs():
    filters = {
        "work_type": request.args.get("work_type", ""),
        "min_score": int(request.args.get("min_score", 0)),
        "search": request.args.get("search", ""),
        "saved": request.args.get("saved", ""),
        "sort": request.args.get("sort", "match_score")
    }
    query = "SELECT * FROM jobs WHERE hidden=0"
    params = []
    if filters["work_type"]:
        query += " AND work_type=?"
        params.append(filters["work_type"])
    if filters["min_score"]:
        query += " AND match_score>=?"
        params.append(filters["min_score"])
    if filters["search"]:
        query += " AND (title LIKE ? OR company LIKE ? OR location LIKE ?)"
        s = f"%{filters['search']}%"
        params += [s, s, s]
    if filters["saved"] == "1":
        query += " AND saved=1"

    sort_map = {
        "match_score": "match_score DESC",
        "date_found": "date_found DESC",
        "salary": "salary_max DESC",
        "title": "title ASC"
    }
    query += f" ORDER BY {sort_map.get(filters['sort'], 'match_score DESC')}"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/jobs/<int:job_id>/save", methods=["POST"])
def toggle_save(job_id):
    data = request.json
    with get_db() as conn:
        conn.execute("UPDATE jobs SET saved=? WHERE id=?", (1 if data.get("saved") else 0, job_id))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/jobs/<int:job_id>/hide", methods=["POST"])
def hide_job(job_id):
    with get_db() as conn:
        conn.execute("UPDATE jobs SET hidden=1 WHERE id=?", (job_id,))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/stats")
def api_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE hidden=0").fetchone()["c"]
        saved = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE saved=1 AND hidden=0").fetchone()["c"]
        last_scrape = get_setting("last_scrape")
        last_log = conn.execute("SELECT * FROM scrape_log ORDER BY id DESC LIMIT 1").fetchone()
    return jsonify({
        "total": total,
        "saved": saved,
        "last_scrape": last_scrape,
        "scrape_running": scrape_status["running"],
        "scrape_progress": scrape_status["progress"],
        "last_log": dict(last_log) if last_log else None
    })

@app.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    if scrape_status["running"]:
        return jsonify({"ok": False, "msg": "Scrape already running"})
    openai_key = get_setting("openai_key")
    jsearch_key = get_setting("jsearch_key")
    if not openai_key or not jsearch_key:
        return jsonify({"ok": False, "msg": "Please set API keys in Settings first"})
    thread = threading.Thread(target=run_scrape, args=(openai_key, jsearch_key))
    thread.daemon = True
    thread.start()
    return jsonify({"ok": True})

def run_scrape(openai_key, jsearch_key):
    global scrape_status
    scrape_status["running"] = True
    scrape_status["log"] = []
    started = datetime.now().isoformat()
    jobs_found = 0
    try:
        def log(msg):
            scrape_status["progress"] = msg
            scrape_status["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

        log("Starting scrape...")
        jobs = scraper.scrape_jobs(jsearch_key, log)
        log(f"Found {len(jobs)} raw listings. Running AI matching...")
        matched = scraper.match_jobs(jobs, openai_key, log)
        log(f"Saving {len(matched)} jobs to database...")
        with get_db() as conn:
            for job in matched:
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO jobs
                        (job_id, title, company, location, lat, lng, work_type,
                         salary_min, salary_max, salary_display, match_score,
                         match_reasons, description, apply_url, company_url,
                         source, date_found, date_posted)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        job.get("job_id"), job.get("title"), job.get("company"),
                        job.get("location"), job.get("lat"), job.get("lng"),
                        job.get("work_type"), job.get("salary_min"), job.get("salary_max"),
                        job.get("salary_display"), job.get("match_score"),
                        job.get("match_reasons"), job.get("description"),
                        job.get("apply_url"), job.get("company_url"),
                        job.get("source"), datetime.now().isoformat(), job.get("date_posted")
                    ))
                    jobs_found += 1
                except Exception as e:
                    log(f"Error saving job: {e}")
            conn.commit()
        set_setting("last_scrape", datetime.now().isoformat())
        with get_db() as conn:
            conn.execute("INSERT INTO scrape_log (started_at, finished_at, jobs_found, status) VALUES (?,?,?,?)",
                        (started, datetime.now().isoformat(), jobs_found, "success"))
            conn.commit()
        log(f"Done! {jobs_found} new jobs saved.")
    except Exception as e:
        with get_db() as conn:
            conn.execute("INSERT INTO scrape_log (started_at, finished_at, jobs_found, status) VALUES (?,?,?,?)",
                        (started, datetime.now().isoformat(), jobs_found, f"error: {e}"))
            conn.commit()
        scrape_status["progress"] = f"Error: {e}"
    finally:
        scrape_status["running"] = False

@app.route("/api/settings", methods=["GET"])
def get_settings():
    keys = ["openai_key", "jsearch_key", "auto_scrape", "scrape_interval_hours"]
    result = {}
    for k in keys:
        v = get_setting(k)
        # Mask keys
        if "key" in k and v:
            result[k] = v[:6] + "..." + v[-4:] if len(v) > 10 else "****"
        else:
            result[k] = v
    return jsonify(result)

@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.json
    allowed = ["openai_key", "jsearch_key", "auto_scrape", "scrape_interval_hours"]
    for k in allowed:
        if k in data and data[k] and not data[k].endswith("..."):
            set_setting(k, data[k])
    return jsonify({"ok": True})

@app.route("/api/scrape/status")
def scrape_status_api():
    return jsonify(scrape_status)

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
