"""
Google Sheets two-way sync for JobHunter.
Uses a service account JSON key for auth — no OAuth browser flow needed.

Setup (one-time, ~5 minutes):
1. Go to https://console.cloud.google.com
2. Create a project (or use existing)
3. Enable "Google Sheets API"
4. IAM & Admin → Service Accounts → Create service account
5. Create JSON key → download → save as credentials/sheets_credentials.json
6. Share your Google Sheet with the service account email (Editor access)
7. Paste your Sheet ID into Settings in JobHunter
"""

import json
import os
import re
import time
from datetime import datetime

# ─── COLUMN MAPPING ───────────────────────────────────────────────────────────
# Maps sheet column letters (0-indexed) to field names
# Sheet columns: Title, Company, Pay, Date Applied, Location, Status,
#                Latest Email Subject, Latest Email Body, Notes
COL_TITLE         = 0   # A
COL_COMPANY       = 1   # B
COL_PAY           = 2   # C
COL_DATE_APPLIED  = 3   # D
COL_LOCATION      = 4   # E
COL_STATUS        = 5   # F
COL_EMAIL_SUBJECT = 6   # G
COL_EMAIL_BODY    = 7   # H
COL_NOTES         = 8   # I

HEADER_ROW = 1  # Row 1 is headers, data starts at row 2

STATUS_MAP_FROM_SHEET = {
    "applied":   "applied",
    "rejected":  "rejected",
    "interview": "interview",
    "offer":     "offer",
    "stale":     "none",
    "interested":"interested",
    "":          "none",
}

STATUS_MAP_TO_SHEET = {
    "applied":   "Applied",
    "rejected":  "Rejected",
    "interview": "Interview",
    "offer":     "Offer",
    "none":      "Applied",
    "interested":"Interested",
}


def _get_service(creds_path: str):
    """Build a Google Sheets API service object from service account JSON."""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google API libraries not installed. Run:\n"
            "pip install google-auth google-auth-httplib2 google-api-python-client"
        )

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return service.spreadsheets()


def _normalize_status(raw: str) -> str:
    return STATUS_MAP_FROM_SHEET.get(raw.strip().lower(), "applied")


def _parse_salary(pay_str: str):
    """Parse salary string like '70000' or '$70,000' into integer."""
    if not pay_str:
        return None
    cleaned = re.sub(r'[^0-9]', '', str(pay_str))
    return int(cleaned) if cleaned else None


def _make_job_key(title: str, company: str) -> str:
    """Normalized key for matching jobs across systems."""
    return re.sub(r'[^a-z0-9]', '', (title + company).lower())


# ─── READ FROM SHEET ──────────────────────────────────────────────────────────

def read_sheet(sheet_id: str, creds_path: str) -> list:
    """
    Read all rows from the sheet. Returns list of dicts with normalized fields.
    """
    sheets = _get_service(creds_path)
    result = sheets.values().get(
        spreadsheetId=sheet_id,
        range="Sheet1!A:I"
    ).execute()

    rows = result.get("values", [])
    if len(rows) < 2:
        return []

    jobs = []
    for i, row in enumerate(rows[1:], start=2):  # Skip header row
        # Pad row to ensure all columns exist
        while len(row) < 9:
            row.append("")

        title   = row[COL_TITLE].strip()
        company = row[COL_COMPANY].strip()
        if not title and not company:
            continue

        sal = _parse_salary(row[COL_PAY])
        jobs.append({
            "sheet_row": i,
            "title":         title,
            "company":       company,
            "salary_min":    sal,
            "salary_max":    sal,
            "salary_display": f"${sal:,}" if sal else "",
            "date_applied":  row[COL_DATE_APPLIED].strip(),
            "location":      row[COL_LOCATION].strip(),
            "app_status":    _normalize_status(row[COL_STATUS]),
            "email_subject": row[COL_EMAIL_SUBJECT].strip(),
            "email_body":    row[COL_EMAIL_BODY].strip(),
            "notes":         row[COL_NOTES].strip(),
            "sheet_key":     _make_job_key(title, company),
        })

    return jobs


# ─── WRITE STATUS TO SHEET ────────────────────────────────────────────────────

def write_status_to_sheet(sheet_id: str, creds_path: str, sheet_row: int,
                           status: str, notes: str = None):
    """
    Update a specific row's status (and optionally notes) in the sheet.
    sheet_row is 1-indexed (row 2 = first data row).
    """
    sheets = _get_service(creds_path)
    sheet_status = STATUS_MAP_TO_SHEET.get(status, status.capitalize())

    updates = []
    # Update status column (F)
    updates.append({
        "range": f"Sheet1!F{sheet_row}",
        "values": [[sheet_status]]
    })
    # Update notes column (I) if provided
    if notes is not None:
        updates.append({
            "range": f"Sheet1!I{sheet_row}",
            "values": [[notes]]
        })

    sheets.values().batchUpdate(
        spreadsheetId=sheet_id,
        body={
            "valueInputOption": "USER_ENTERED",
            "data": updates
        }
    ).execute()


def append_job_to_sheet(sheet_id: str, creds_path: str, job: dict):
    """
    Append a new job row to the sheet (when marked Applied in JobHunter).
    """
    sheets = _get_service(creds_path)

    date_applied = datetime.now().strftime("%-m/%-d/%Y")
    salary = ""
    if job.get("salary_max"):
        salary = str(job["salary_max"])
    elif job.get("salary_min"):
        salary = str(job["salary_min"])

    location = job.get("location", "")
    wt = job.get("work_type", "")
    if wt and wt not in location:
        location = f"{location} {wt}".strip()

    row = [
        job.get("title", ""),
        job.get("company", ""),
        salary,
        date_applied,
        location,
        "Applied",
        "",  # email subject (not known yet)
        "",  # email body
        job.get("notes", ""),
    ]

    sheets.values().append(
        spreadsheetId=sheet_id,
        range="Sheet1!A:I",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]}
    ).execute()


# ─── FULL SYNC LOGIC ──────────────────────────────────────────────────────────

def sync_from_sheet(sheet_id: str, creds_path: str, db_conn, user_id: int) -> dict:
    """
    Pull sheet → update JobHunter DB.
    - New rows in sheet that don't exist in DB get inserted
    - Existing rows: if sheet status differs from DB, sheet wins (email script is authoritative)
    Returns summary dict.
    """
    sheet_jobs = read_sheet(sheet_id, creds_path)

    inserted = 0
    updated = 0
    skipped = 0

    for sj in sheet_jobs:
        # Try to find matching job in DB by title+company key
        key = sj["sheet_key"]
        existing = db_conn.execute(
            """SELECT id, app_status, notes, sheet_row FROM jobs
               WHERE user_id=? AND hidden=0
               AND lower(replace(replace(title||company,' ',''),'_','')) LIKE ?""",
            (user_id, f"%{key[:20]}%")
        ).fetchone()

        if existing:
            # Update status and sheet_row tracking if sheet has newer info
            changes = {}
            sheet_status = sj["app_status"]
            if sheet_status and sheet_status != existing["app_status"] and sheet_status != "none":
                changes["app_status"] = sheet_status
            if sj["notes"] and not existing["notes"]:
                changes["notes"] = sj["notes"]
            # Always sync sheet_row so we can write back later
            if existing["sheet_row"] != sj["sheet_row"]:
                changes["sheet_row"] = sj["sheet_row"]

            if changes:
                set_clause = ", ".join(f"{k}=?" for k in changes)
                db_conn.execute(
                    f"UPDATE jobs SET {set_clause} WHERE id=?",
                    list(changes.values()) + [existing["id"]]
                )
                updated += 1
            else:
                skipped += 1
        else:
            # Insert as a new job from sheet history
            # Build a pseudo job_id from title+company+date
            pseudo_id = "sheet_" + re.sub(r'[^a-z0-9]', '', (
                sj["title"] + sj["company"] + sj.get("date_applied", "")
            ).lower())[:40]

            sal = sj.get("salary_min")
            try:
                db_conn.execute("""
                    INSERT OR IGNORE INTO jobs
                    (user_id, job_id, title, company, location, work_type,
                     salary_min, salary_max, salary_display,
                     match_score, match_reasons,
                     apply_url, source, date_found, date_posted,
                     app_status, notes, sheet_row, is_new, saved)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1)
                """, (
                    user_id, pseudo_id,
                    sj["title"], sj["company"],
                    sj["location"], _infer_work_type(sj["location"]),
                    sal, sal,
                    sj["salary_display"],
                    -1, "Imported from Google Sheets",
                    "", "Sheets Import",
                    datetime.now().isoformat(),
                    sj.get("date_applied", ""),
                    sj["app_status"],
                    sj["notes"],
                    sj["sheet_row"],
                ))
                inserted += 1
            except Exception:
                skipped += 1

    db_conn.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "total": len(sheet_jobs)}


def sync_to_sheet(sheet_id: str, creds_path: str, db_conn, user_id: int,
                  changed_job_ids: list = None) -> dict:
    """
    Push JobHunter → sheet.
    If changed_job_ids provided, only sync those jobs.
    Otherwise syncs all jobs that have a sheet_row set.
    Also appends newly-applied jobs that don't have a sheet_row yet.
    """
    if changed_job_ids:
        placeholders = ",".join("?" * len(changed_job_ids))
        jobs = db_conn.execute(
            f"SELECT * FROM jobs WHERE id IN ({placeholders}) AND user_id=?",
            changed_job_ids + [user_id]
        ).fetchall()
    else:
        jobs = db_conn.execute(
            "SELECT * FROM jobs WHERE user_id=? AND (sheet_row IS NOT NULL OR app_status='applied') AND hidden=0",
            (user_id,)
        ).fetchall()

    pushed = 0
    appended = 0
    errors = 0

    for job in jobs:
        try:
            if job["sheet_row"]:
                # Update existing row
                write_status_to_sheet(
                    sheet_id, creds_path,
                    job["sheet_row"],
                    job["app_status"],
                    job["notes"] if job["notes"] else None
                )
                pushed += 1
            elif job["app_status"] == "applied":
                # New application — append row and save the row number
                append_job_to_sheet(sheet_id, creds_path, dict(job))
                # Figure out what row was just appended
                sheet_jobs = read_sheet(sheet_id, creds_path)
                key = _make_job_key(job["title"], job["company"])
                matched = next((s for s in sheet_jobs if s["sheet_key"] == key), None)
                if matched:
                    db_conn.execute(
                        "UPDATE jobs SET sheet_row=? WHERE id=?",
                        (matched["sheet_row"], job["id"])
                    )
                    db_conn.commit()
                appended += 1
            time.sleep(0.2)  # Sheets API rate limit is generous but be polite
        except Exception as e:
            errors += 1
            print(f"Sheets sync error for job {job['id']}: {e}")

    return {"pushed": pushed, "appended": appended, "errors": errors}


def _infer_work_type(location_str: str) -> str:
    loc = (location_str or "").lower()
    if "remote" in loc:
        return "Remote"
    if "hybrid" in loc:
        return "Hybrid"
    return "Onsite"


# ─── VERIFY CREDENTIALS ───────────────────────────────────────────────────────

def verify_connection(sheet_id: str, creds_path: str) -> dict:
    """Test that credentials and sheet ID work. Returns status dict."""
    try:
        if not os.path.exists(creds_path):
            return {"ok": False, "msg": f"Credentials file not found at: {creds_path}"}
        sheets = _get_service(creds_path)
        meta = sheets.get(spreadsheetId=sheet_id).execute()
        title = meta.get("properties", {}).get("title", "Unknown")
        return {"ok": True, "msg": f"Connected to: {title}"}
    except ImportError as e:
        return {"ok": False, "msg": str(e)}
    except Exception as e:
        err = str(e)
        if "404" in err:
            return {"ok": False, "msg": "Sheet not found. Check Sheet ID and that you shared it with the service account."}
        if "403" in err:
            return {"ok": False, "msg": "Permission denied. Share the sheet with the service account email (Editor access)."}
        return {"ok": False, "msg": f"Connection error: {err}"}
