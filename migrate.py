#!/usr/bin/env python3
"""
Run this once to migrate your existing JobHunter database to the new multi-user schema.
Only needed if you ran the original version. Safe to run multiple times.
"""
import sqlite3

DB = "data/jobs.db"

def migrate():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    
    print("Running migrations...")

    migrations = [
        # Core tables
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            created_at TEXT DEFAULT (datetime('now')),
            resume_text TEXT, resume_filename TEXT, ai_context TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS jobs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 1, job_id TEXT NOT NULL,
            title TEXT, company TEXT, location TEXT, lat REAL, lng REAL, work_type TEXT,
            salary_min INTEGER, salary_max INTEGER, salary_display TEXT,
            match_score INTEGER DEFAULT -1, match_reasons TEXT, description TEXT,
            apply_url TEXT, company_url TEXT, source TEXT,
            date_found TEXT, date_posted TEXT,
            saved INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0,
            notes TEXT DEFAULT '', app_status TEXT DEFAULT 'none',
            is_new INTEGER DEFAULT 0, scrape_batch_id INTEGER DEFAULT 0,
            UNIQUE(user_id, job_id)
        )""",
        """CREATE TABLE IF NOT EXISTS search_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            city TEXT, state TEXT, label TEXT, radius_miles INTEGER DEFAULT 30, active INTEGER DEFAULT 1
        )""",
        """CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL,
            adzuna_calls INTEGER DEFAULT 0,
            jsearch_calls INTEGER DEFAULT 0, ai_calls INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')), UNIQUE(month)
        )""",
        """CREATE TABLE IF NOT EXISTS api_usage_daily (
            day TEXT PRIMARY KEY,
            adzuna_calls INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS sheets_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, synced_at TEXT, direction TEXT,
            inserted INTEGER DEFAULT 0, updated INTEGER DEFAULT 0,
            pushed INTEGER DEFAULT 0, appended INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0, status TEXT
        )""",
        "ALTER TABLE scrape_log ADD COLUMN user_id INTEGER",
        "ALTER TABLE scrape_log ADD COLUMN jsearch_calls INTEGER DEFAULT 0",
        "ALTER TABLE scrape_log ADD COLUMN ai_calls INTEGER DEFAULT 0",
        "ALTER TABLE scrape_log ADD COLUMN adzuna_calls INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN notes TEXT DEFAULT ''",
        "ALTER TABLE jobs ADD COLUMN app_status TEXT DEFAULT 'none'",
        "ALTER TABLE jobs ADD COLUMN is_new INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN scrape_batch_id INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN sheet_row INTEGER DEFAULT NULL",
        "ALTER TABLE api_usage ADD COLUMN adzuna_calls INTEGER DEFAULT 0",
    ]

    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
            print(f"  ✓ {sql[:60].strip()}...")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                print(f"  → Already done: {sql[:50].strip()}")
            else:
                print(f"  ! Error: {e}")

    # Check if old jobs table needs migration
    try:
        conn.execute("SELECT user_id FROM jobs LIMIT 1")
        print("  → jobs table already has user_id column")
    except:
        print("  Migrating jobs table to add user_id...")
        try:
            conn.execute("INSERT INTO jobs_new SELECT 1,job_id,title,company,location,lat,lng,work_type,salary_min,salary_max,salary_display,match_score,match_reasons,description,apply_url,company_url,source,date_found,date_posted,saved,hidden FROM jobs")
            conn.execute("DROP TABLE jobs")
            conn.execute("ALTER TABLE jobs_new RENAME TO jobs")
            conn.commit()
            print("  ✓ Jobs migrated")
        except Exception as e:
            print(f"  ! Job migration error: {e}")

    # Update settings keys
    old_to_new = {"openai_key": "purdue_api_key"}
    for old, new in old_to_new.items():
        row = conn.execute("SELECT value FROM settings WHERE key=?", (old,)).fetchone()
        if row:
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (new, row["value"]))
            conn.execute("DELETE FROM settings WHERE key=?", (old,))
            conn.commit()
            print(f"  ✓ Renamed setting {old} → {new}")

    new_settings = {
        "purdue_api_url": "https://genai.rcac.purdue.edu/api/chat/completions",
        "purdue_api_model": "gpt-oss:120b",
        "jsearch_monthly_limit": "200",
        "adzuna_daily_limit": "250",
        "adzuna_app_id": "",
        "adzuna_app_key": "",
        "sheets_id": "",
        "sheets_auto_sync": "0",
    }
    for k, v in new_settings.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
    conn.commit()

    # Create default user if none exist
    users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if users == 0:
        conn.execute("INSERT INTO users (username) VALUES ('tristan')")
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE username='tristan'").fetchone()["id"]
        conn.executemany(
            "INSERT OR IGNORE INTO search_locations (user_id, city, state, label, radius_miles, active) VALUES (?,?,?,?,?,1)",
            [(uid,'Indianapolis','IN','Indianapolis, IN',30),(uid,'West Lafayette','IN','West Lafayette, IN',25),(uid,'Plainfield','IN','Plainfield, IN',20)]
        )
        conn.execute("UPDATE jobs SET user_id=?", (uid,))
        conn.commit()
        print(f"  ✓ Created default user 'tristan' (id={uid})")

    conn.close()
    print("\n✓ Migration complete! Run ./run.sh to start.")

if __name__ == "__main__":
    migrate()
