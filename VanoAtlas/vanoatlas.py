#!/usr/bin/env python3

import json
import mimetypes
import os
import platform
import shutil
import signal
import time
import socket
import sqlite3
import struct
import subprocess
import threading
import webbrowser
import zlib
import sys
import hashlib as _hashlib
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build as _gcal_build
    from googleapiclient.http import MediaFileUpload
    _GCAL_AVAILABLE = True
except ImportError:
    _GCAL_AVAILABLE = False

try:
    import caldav
    _CALDAV_AVAILABLE = True
except ImportError:
    _CALDAV_AVAILABLE = False

GCAL_SCOPES       = ["https://www.googleapis.com/auth/calendar",
                      "https://www.googleapis.com/auth/drive.file"]
DB_DIR            = Path(os.environ.get("VANOATLAS_HOME") or (Path.home() / ".vanoatlas")).expanduser()
DB_PATH           = DB_DIR / "tasks.db"
GCAL_TOKEN        = DB_DIR / "gcal_token.json"
GCAL_CREDS        = DB_DIR / "gcal_credentials.json"
ICLOUD_CREDS      = DB_DIR / "icloud_credentials.json"
ICLOUD_URL        = "https://caldav.icloud.com/"
DRIVE_BACKUP_NAME = "vanoatlas_backup.db"
DRIVE_BACKUP_EVERY_SEC = 6 * 3600
PORT              = int(os.environ.get("VANOATLAS_PORT") or 8770)
APP_VERSION       = "1.0.0"

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, description TEXT DEFAULT '',
    status TEXT DEFAULT 'In Progress', domain TEXT DEFAULT '',
    deadline TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id),
    title TEXT NOT NULL, description TEXT DEFAULT '',
    status TEXT DEFAULT 'To Do', priority TEXT DEFAULT 'Normal',
    task_type TEXT DEFAULT 'Other', deadline TEXT DEFAULT '',
    tags TEXT DEFAULT '', subtasks TEXT DEFAULT '',
    planned_date TEXT DEFAULT '', estimate_min INTEGER DEFAULT 0,
    energy TEXT DEFAULT 'Medium', completed_at TEXT DEFAULT '',
    created_at TEXT);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, content TEXT DEFAULT '',
    tags TEXT DEFAULT '', project_id INTEGER,
    category TEXT DEFAULT 'New ideas', note_date TEXT DEFAULT '',
    created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, win_type TEXT DEFAULT 'Milestone',
    description TEXT DEFAULT '', win_date TEXT DEFAULT '',
    project_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, meeting_date TEXT DEFAULT '',
    meeting_time TEXT DEFAULT '', location TEXT DEFAULT '',
    meeting_type TEXT DEFAULT 'Other', attendees TEXT DEFAULT '',
    description TEXT DEFAULT '', project_id INTEGER, created_at TEXT,
    start_time TEXT DEFAULT '', end_time TEXT DEFAULT '',
    notes TEXT DEFAULT '', google_id TEXT DEFAULT '', caldav_uid TEXT DEFAULT '',
    recur_freq TEXT DEFAULT '', recur_until TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS daily_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, category TEXT DEFAULT 'Personal',
    done INTEGER DEFAULT 0, task_date TEXT DEFAULT '', task_time TEXT DEFAULT '',
    task_time_end TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS budget_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL, budgeted REAL DEFAULT 0, actual REAL DEFAULT 0,
    note TEXT DEFAULT '', month TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, amount REAL DEFAULT 0, category TEXT DEFAULT 'Other',
    exp_date TEXT DEFAULT '', note TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, amount REAL DEFAULT 0, creditor TEXT DEFAULT '',
    due_date TEXT DEFAULT '', paid INTEGER DEFAULT 0, note TEXT DEFAULT '',
    created_at TEXT);
CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, description TEXT DEFAULT '',
    milestone_date TEXT DEFAULT '', project_id INTEGER,
    done INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, author TEXT DEFAULT '', year TEXT DEFAULT '',
    genre TEXT DEFAULT '', rating INTEGER DEFAULT 0, read_date TEXT DEFAULT '',
    notes TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL, started_at TEXT NOT NULL, ended_at TEXT DEFAULT '',
    duration_min INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS ts_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT DEFAULT 'journal', title TEXT DEFAULT '', content TEXT DEFAULT '',
    mood_score INTEGER, status TEXT DEFAULT 'active',
    entry_date TEXT DEFAULT '', target_date TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, source TEXT DEFAULT 'Capture', note TEXT DEFAULT '',
    created_at TEXT);
CREATE TABLE IF NOT EXISTS refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bibkey TEXT DEFAULT '', title TEXT NOT NULL, authors TEXT DEFAULT '',
    year TEXT DEFAULT '', venue TEXT DEFAULT '', doi TEXT DEFAULT '',
    arxiv_id TEXT DEFAULT '', url TEXT DEFAULT '', status TEXT DEFAULT 'To read',
    tags TEXT DEFAULT '', abstract TEXT DEFAULT '', notes TEXT DEFAULT '',
    bibtex TEXT DEFAULT '', project_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT DEFAULT '', title TEXT NOT NULL, kind TEXT DEFAULT 'Lemma',
    status TEXT DEFAULT 'In progress', statement TEXT DEFAULT '',
    deps TEXT DEFAULT '', ref_key TEXT DEFAULT '', project_id INTEGER,
    notes TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS radar_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id TEXT DEFAULT '', title TEXT NOT NULL, authors TEXT DEFAULT '',
    abstract TEXT DEFAULT '', cats TEXT DEFAULT '', published TEXT DEFAULT '',
    score INTEGER DEFAULT 0, why TEXT DEFAULT '', state TEXT DEFAULT 'new',
    doi TEXT DEFAULT '', journal_ref TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL, author TEXT DEFAULT '', source TEXT DEFAULT '',
    category TEXT DEFAULT 'Research', fav INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0,
    created_at TEXT);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notif_log (
    key TEXT PRIMARY KEY, sent_at TEXT);
"""

TABLES = {
    "projects":     ["title", "description", "status", "domain", "deadline"],
    "tasks":        ["project_id", "title", "description", "status",
                     "priority", "task_type", "deadline", "tags", "subtasks",
                     "planned_date", "estimate_min", "energy", "completed_at",
                     "planned_time"],
    "notes":        ["title", "content", "tags", "project_id", "category", "note_date"],
    "achievements": ["title", "win_type", "description", "win_date", "project_id"],
    "meetings":     ["title", "meeting_date", "meeting_time", "location",
                     "meeting_type", "attendees", "description", "project_id",
                     "start_time", "end_time", "notes", "google_id",
                     "recur_freq", "recur_until"],
    "daily_tasks":  ["title", "category", "done", "task_date", "task_time", "task_time_end"],
    "budget_items": ["category", "budgeted", "actual", "note", "month"],
    "expenses":     ["title", "amount", "category", "exp_date", "note"],
    "debts":        ["title", "amount", "creditor", "due_date", "paid", "note"],
    "milestones":   ["title", "description", "milestone_date", "project_id", "done"],
    "books":        ["title", "author", "year", "genre", "rating", "read_date", "notes"],
    "time_entries": ["task_id", "started_at", "ended_at", "duration_min"],
    "ts_entries":   ["kind", "title", "content", "mood_score", "status", "entry_date", "target_date"],
    "inbox":        ["title", "source", "note"],
    "refs":         ["bibkey", "title", "authors", "year", "venue", "doi", "arxiv_id",
                     "url", "status", "tags", "abstract", "notes", "bibtex", "project_id"],
    "results":      ["label", "title", "kind", "status", "statement", "deps", "ref_key",
                     "project_id", "notes"],
    "radar_items":  ["arxiv_id", "title", "authors", "abstract", "cats", "published",
                     "score", "why", "state", "doi", "journal_ref"],
    "quotes":       ["text", "author", "source", "category", "fav", "hidden"],
}


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = db()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    conn.close()


def _migrate(conn):
    note_cols = {r["name"] for r in conn.execute("PRAGMA table_info(notes)").fetchall()}
    if "category" not in note_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN category TEXT DEFAULT 'New ideas'")
        conn.execute("UPDATE notes SET category='New ideas' WHERE category IS NULL OR category=''")
        conn.commit()
    if "note_date" not in note_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN note_date TEXT DEFAULT ''")
        conn.execute("UPDATE notes SET note_date=substr(created_at,1,10) "
                      "WHERE (note_date IS NULL OR note_date='') AND created_at IS NOT NULL")
        conn.commit()
    daily_cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_tasks)").fetchall()}
    if "task_time" not in daily_cols:
        conn.execute("ALTER TABLE daily_tasks ADD COLUMN task_time TEXT DEFAULT ''")
        conn.commit()
        daily_cols.add("task_time")
    if "task_time_end" not in daily_cols:
        conn.execute("ALTER TABLE daily_tasks ADD COLUMN task_time_end TEXT DEFAULT ''")
        conn.commit()
    meeting_cols = {r["name"] for r in conn.execute("PRAGMA table_info(meetings)").fetchall()}
    if "caldav_uid" not in meeting_cols:
        conn.execute("ALTER TABLE meetings ADD COLUMN caldav_uid TEXT DEFAULT ''")
        conn.commit()
    if "recur_freq" not in meeting_cols:
        conn.execute("ALTER TABLE meetings ADD COLUMN recur_freq TEXT DEFAULT ''")
        conn.commit()
    if "recur_until" not in meeting_cols:
        conn.execute("ALTER TABLE meetings ADD COLUMN recur_until TEXT DEFAULT ''")
        conn.commit()
    task_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "tags" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN tags TEXT DEFAULT ''")
        conn.commit()
    if "subtasks" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN subtasks TEXT DEFAULT ''")
        conn.commit()
        task_cols.add("subtasks")
    if "planned_date" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN planned_date TEXT DEFAULT ''")
        conn.commit()
        task_cols.add("planned_date")
    if "estimate_min" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN estimate_min INTEGER DEFAULT 0")
        conn.commit()
        task_cols.add("estimate_min")
    if "energy" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN energy TEXT DEFAULT 'Medium'")
        conn.commit()
        task_cols.add("energy")
    if "completed_at" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT DEFAULT ''")
        conn.commit()
    if "planned_time" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN planned_time TEXT DEFAULT ''")
        conn.commit()
    for stmt in ("CREATE INDEX IF NOT EXISTS ix_tasks_project ON tasks(project_id)",
                 "CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status)",
                 "CREATE INDEX IF NOT EXISTS ix_time_task ON time_entries(task_id)",
                 "CREATE UNIQUE INDEX IF NOT EXISTS ix_radar_aid ON radar_items(arxiv_id)"):
        try:
            conn.execute(stmt)
        except sqlite3.Error:
            pass
    conn.commit()


def db_all():
    conn = db()
    out = {}
    for t in TABLES:
        out[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
    conn.close()
    return out


def db_insert(table, data):
    data = dict(data)
    now_iso = datetime.now().isoformat(timespec="seconds")
    if table == "tasks":
        try:
            data["estimate_min"] = max(0, int(float(data.get("estimate_min") or 0)))
        except (TypeError, ValueError):
            data["estimate_min"] = 0
        if data.get("status") == "Done" and not data.get("completed_at"):
            data["completed_at"] = now_iso
    cols = [c for c in TABLES[table] if c in data]
    vals = [data[c] for c in cols]
    cols.append("created_at")
    vals.append(now_iso)
    if table == "notes":
        cols.append("updated_at")
        vals.append(now_iso)
    q = f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
    conn = db()
    cur = conn.execute(q, vals)
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def db_update(table, rid, data):
    data = dict(data)
    if table == "notes":
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if table == "tasks":
        if "estimate_min" in data:
            try:
                data["estimate_min"] = max(0, int(float(data.get("estimate_min") or 0)))
            except (TypeError, ValueError):
                data["estimate_min"] = 0
        if "status" in data:
            conn0 = db()
            row = conn0.execute("SELECT status,completed_at FROM tasks WHERE id=?", (rid,)).fetchone()
            conn0.close()
            if data.get("status") == "Done" and row and not row["completed_at"]:
                data["completed_at"] = datetime.now().isoformat(timespec="seconds")
            elif data.get("status") != "Done" and row and row["completed_at"]:
                data["completed_at"] = ""
    cols = [c for c in TABLES[table] if c in data]
    if table == "notes" and "updated_at" not in cols:
        cols = cols + ["updated_at"]
    if not cols:
        return
    q = f"UPDATE {table} SET {','.join(c + '=?' for c in cols)} WHERE id=?"
    conn = db()
    conn.execute(q, [data[c] for c in cols] + [rid])
    conn.commit()
    conn.close()


def db_delete(table, rid):
    conn = db()
    conn.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
    conn.commit()
    conn.close()


def _backup_db(label="cleanup"):
    backup_dir = DB_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"tasks_{label}_{stamp}.db"
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, dst)
    return str(dst)


def data_health():
    conn = db()
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in TABLES}
        orphan_time = conn.execute(
            "SELECT COUNT(*) c FROM time_entries te LEFT JOIN tasks t ON t.id=te.task_id WHERE t.id IS NULL"
        ).fetchone()["c"]
        blank_titles = {}
        for t in ("tasks", "projects", "notes", "meetings", "daily_tasks"):
            blank_titles[t] = conn.execute(
                f"SELECT COUNT(*) c FROM {t} WHERE title IS NULL OR trim(title)=''"
            ).fetchone()["c"]
        duplicate_tasks = conn.execute(
            "SELECT COUNT(*) c FROM (SELECT lower(trim(title)) t, ifnull(project_id,0) p, "
            "ifnull(deadline,'') d, COUNT(*) n FROM tasks WHERE trim(title)<>'' GROUP BY t,p,d HAVING n>1)"
        ).fetchone()["c"]
        duplicate_google = conn.execute(
            "SELECT COUNT(*) c FROM (SELECT google_id,COUNT(*) n FROM meetings WHERE google_id<>'' GROUP BY google_id HAVING n>1)"
        ).fetchone()["c"]
        duplicate_caldav = conn.execute(
            "SELECT COUNT(*) c FROM (SELECT caldav_uid,COUNT(*) n FROM meetings WHERE caldav_uid<>'' GROUP BY caldav_uid HAVING n>1)"
        ).fetchone()["c"]
        stale_timers = conn.execute(
            "SELECT COUNT(*) c FROM time_entries WHERE (ended_at IS NULL OR ended_at='') "
            "AND started_at<>'' AND datetime(started_at) < datetime('now','-24 hours')"
        ).fetchone()["c"]
        return {
            "ok": True, "counts": counts, "orphan_time_entries": orphan_time,
            "blank_titles": blank_titles, "duplicate_task_groups": duplicate_tasks,
            "duplicate_google_groups": duplicate_google, "duplicate_caldav_groups": duplicate_caldav,
            "stale_timers": stale_timers,
        }
    finally:
        conn.close()


def safe_data_cleanup():
    backup = _backup_db("before_safe_cleanup")
    conn = db()
    result = {"backup": backup, "normalized": 0, "orphan_time_entries": 0,
              "calendar_duplicates": 0, "notif_pruned": 0}
    try:
        for table in ("tasks", "projects", "notes", "meetings", "daily_tasks", "achievements", "books"):
            before = conn.total_changes
            conn.execute(f"UPDATE {table} SET title=trim(title) WHERE title IS NOT NULL AND title<>trim(title)")
            result["normalized"] += conn.total_changes - before

        before = conn.total_changes
        conn.execute("DELETE FROM time_entries WHERE task_id NOT IN (SELECT id FROM tasks)")
        result["orphan_time_entries"] = conn.total_changes - before

        for col in ("google_id", "caldav_uid"):
            rows = conn.execute(
                f"SELECT {col} v, MAX(id) keep_id FROM meetings WHERE {col}<>'' GROUP BY {col} HAVING COUNT(*)>1"
            ).fetchall()
            for row in rows:
                before = conn.total_changes
                conn.execute(f"DELETE FROM meetings WHERE {col}=? AND id<>?", (row["v"], row["keep_id"]))
                result["calendar_duplicates"] += conn.total_changes - before

        before = conn.total_changes
        conn.execute("DELETE FROM notif_log WHERE datetime(sent_at) < datetime('now','-180 days')")
        result["notif_pruned"] = conn.total_changes - before
        conn.commit()
        result["ok"] = True
        return result
    finally:
        conn.close()


def cleanup_on_upgrade():
    conn = db()
    row = conn.execute("SELECT value FROM app_settings WHERE key='safe_cleanup_v5'").fetchone()
    conn.close()
    if row and row["value"] == "1":
        return
    safe_data_cleanup()
    conn = db()
    conn.execute("INSERT INTO app_settings(key,value) VALUES('safe_cleanup_v5','1') "
                 "ON CONFLICT(key) DO UPDATE SET value='1'")
    conn.commit()
    conn.close()


DEFAULT_SETTINGS = {
    "notif_enabled": "1",
    "notif_tasks": "1",
    "notif_meetings": "1",
    "notif_daily_time": "08:00",
    "notif_lead_minutes": "15",
    "radar_keywords": "",
    "radar_categories": "math.SG, math.AG, math.DG, math.QA",
    "radar_authors": "",
    "radar_auto": "1",
    "radar_last": "",
}


def get_settings():
    conn = db()
    rows = {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM app_settings").fetchall()}
    conn.close()
    out = dict(DEFAULT_SETTINGS)
    out.update(rows)
    return out


def set_settings(patch):
    conn = db()
    for k, v in patch.items():
        if k not in DEFAULT_SETTINGS:
            continue
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    conn.commit()
    conn.close()
    return get_settings()


def _notif_already_sent(key):
    conn = db()
    row = conn.execute("SELECT 1 FROM notif_log WHERE key=?", (key,)).fetchone()
    conn.close()
    return row is not None


def _notif_mark_sent(key):
    conn = db()
    conn.execute("INSERT OR REPLACE INTO notif_log(key,sent_at) VALUES(?,?)",
                 (key, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()


def mac_notify(title, message, sound="Glass"):
    if platform.system() != "Darwin":
        return
    def _esc(s):
        return str(s).replace("\\", "\\\\").replace('"', '\\"')
    _nc = lambda t: str(t)
    script = 'display notification "%s" with title "%s"' % (_esc(_nc(message)), _esc(_nc(title)))
    if sound:
        script += ' sound name "%s"' % _esc(sound)
    try:
        subprocess.run(["osascript", "-e", script], timeout=5,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _meeting_occurs_on(m, day):
    if not m["meeting_date"]:
        return False
    try:
        start = datetime.strptime(m["meeting_date"], "%Y-%m-%d").date()
    except ValueError:
        return False
    if day < start:
        return False
    if (m["recur_freq"] or "") == "weekly" and m["recur_until"]:
        try:
            until = datetime.strptime(m["recur_until"], "%Y-%m-%d").date()
        except ValueError:
            return day == start
        return day <= until and (day - start).days % 7 == 0
    return day == start


def _notify_tick():
    settings = get_settings()
    if settings.get("notif_enabled") != "1":
        return
    now = datetime.now()
    today = now.date()
    today_iso = today.isoformat()
    conn = db()
    try:
        if settings.get("notif_tasks") == "1":
            try:
                hh, mm = (settings.get("notif_daily_time") or "08:00").split(":")
                target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            except ValueError:
                target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            key = f"daily-{today_iso}"
            if target <= now < target + timedelta(minutes=5) and not _notif_already_sent(key):
                due = conn.execute(
                    "SELECT COUNT(*) c FROM tasks WHERE status!='Done' AND deadline!='' AND deadline<=?",
                    (today_iso,)).fetchone()["c"]
                daily = conn.execute(
                    "SELECT COUNT(*) c FROM daily_tasks WHERE task_date=? AND done=0",
                    (today_iso,)).fetchone()["c"]
                meetings_today = [m for m in conn.execute("SELECT * FROM meetings").fetchall()
                                   if _meeting_occurs_on(m, today)]
                if due or daily or meetings_today:
                    parts = []
                    if due: parts.append(f"{due} task{'s' if due != 1 else ''} due")
                    if daily: parts.append(f"{daily} routine item{'s' if daily != 1 else ''}")
                    if meetings_today: parts.append(f"{len(meetings_today)} meeting{'s' if len(meetings_today) != 1 else ''}")
                    mac_notify("Today on VanoAtlas", ", ".join(parts))
                _notif_mark_sent(key)
        if settings.get("notif_meetings") == "1":
            try:
                lead = int(settings.get("notif_lead_minutes") or 15)
            except ValueError:
                lead = 15
            for m in conn.execute("SELECT * FROM meetings").fetchall():
                if not _meeting_occurs_on(m, today):
                    continue
                t = m["start_time"] or m["meeting_time"]
                if not t:
                    continue
                try:
                    hh, mm = t.split(":")[:2]
                    start_dt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                except ValueError:
                    continue
                key = f"meeting-{m['id']}-{today_iso}"
                if start_dt - timedelta(minutes=lead) <= now < start_dt and not _notif_already_sent(key):
                    where = f" @ {m['location']}" if m["location"] else ""
                    mac_notify("Upcoming: " + m["title"], f"{t}{where}")
                    _notif_mark_sent(key)
    finally:
        conn.close()


def _notify_loop():
    while True:
        try:
            _notify_tick()
        except Exception:
            pass
        threading.Event().wait(60)


PDF_DIR = DB_DIR / "latex_pdfs"


def find_latex_engine(prefer=None):
    order = ["pdflatex", "xelatex", "tectonic"]
    if prefer and prefer in order:
        order = [prefer] + [e for e in order if e != prefer]
    for exe in order:
        found = shutil.which(exe)
        if found:
            return exe
    return None


_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
                   (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


def _is_arabic_char(ch):
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _ARABIC_RANGES)


def _is_cjk_char(ch):
    cp = ord(ch)
    return 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF


def _detect_scripts(text):
    scripts = set()
    for ch in text or "":
        if _is_arabic_char(ch):
            scripts.add("arabic")
        elif _is_cjk_char(ch):
            scripts.add("cjk")
        if len(scripts) == 2:
            break
    return scripts


def _default_arabic_font(system=None):
    system = system or platform.system()
    if system == "Windows":
        return "Segoe UI"
    if system == "Darwin":
        return "Geeza Pro"
    return "Noto Sans Arabic"


def _default_cjk_font(system=None):
    system = system or platform.system()
    if system == "Windows":
        return "Microsoft YaHei"
    if system == "Darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


def _wrap_arabic_runs(text):
    out = []
    i, n = 0, len(text)
    while i < n:
        if _is_arabic_char(text[i]):
            j = i
            last = i
            while j < n:
                if _is_arabic_char(text[j]):
                    last = j
                    j += 1
                elif text[j] in " \t":
                    k = j
                    while k < n and text[k] in " \t":
                        k += 1
                    if k < n and _is_arabic_char(text[k]):
                        j = k
                    else:
                        break
                else:
                    break
            out.append("\\begin{arabic}" + text[i:last + 1] + "\\end{arabic}")
            i = last + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def wrap_note_as_latex(title, content):
    content = content or ""
    if "\\documentclass" in content:
        return content, None
    escaped_title = (title or "Note").replace("_", r"\_").replace("&", r"\&")
    scripts = _detect_scripts(content) | _detect_scripts(title or "")

    if not scripts:
        return r"""\documentclass[11pt]{article}
\usepackage[a4paper,margin=1in]{geometry}
\usepackage[T1]{fontenc}
\usepackage{amsmath,amssymb,amsthm}
\usepackage[normalem]{ulem}
\usepackage{xcolor}
\usepackage{hyperref}
\title{%s}
\date{\today}
\begin{document}
\maketitle
%s
\end{document}
""" % (escaped_title, content), None

    body = content
    lang_setup = [r"\usepackage{polyglossia}", r"\setmainlanguage{english}"]
    if "arabic" in scripts:
        lang_setup.append(r"\setotherlanguage{arabic}")
        lang_setup.append(r"\newfontfamily\arabicfont[Script=Arabic]{%s}" % _default_arabic_font())
        body = _wrap_arabic_runs(body)
    if "cjk" in scripts:
        lang_setup.append(r"\usepackage{xeCJK}")
        lang_setup.append(r"\setCJKmainfont{%s}" % _default_cjk_font())
    return r"""\documentclass[11pt]{article}
\usepackage[a4paper,margin=1in]{geometry}
\usepackage{fontspec}
%s
\usepackage{amsmath,amssymb,amsthm}
\usepackage[normalem]{ulem}
\usepackage{xcolor}
\usepackage{hyperref}
\title{%s}
\date{\today}
\begin{document}
\maketitle
%s
\end{document}
""" % ("\n".join(lang_setup), escaped_title, body), "xelatex"


def compile_latex_to_pdf(tex_source, base_name="note", prefer_engine=None):
    engine = find_latex_engine(prefer=prefer_engine)
    if not engine:
        return None, (
            "No LaTeX engine found on this machine (pdflatex / xelatex / "
            "tectonic). Install MacTeX or BasicTeX "
            "(brew install --cask basictex), then restart the server."
        )
    if prefer_engine and engine != prefer_engine:
        return None, (
            f"This note contains Arabic and/or Chinese text, which needs "
            f"the '{prefer_engine}' engine to render (plain pdflatex can't "
            f"shape those scripts), but only '{engine}' is installed. "
            f"Install the full distribution with "
            f"'brew install --cask mactex' (or, if you already have "
            f"BasicTeX, run 'sudo tlmgr install {prefer_engine} polyglossia "
            f"xecjk'), then restart the server."
        )

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if (c.isalnum() or c in "-_") else "_" for c in base_name) or "note"
    tex_path = PDF_DIR / f"{safe_name}.tex"
    pdf_path = PDF_DIR / f"{safe_name}.pdf"
    tex_path.write_text(tex_source, encoding="utf-8")

    try:
        result = None
        for _ in range(2):
            result = subprocess.run(
                [engine, "-interaction=nonstopmode", "-halt-on-error",
                 "-output-directory", str(PDF_DIR), str(tex_path)],
                cwd=str(PDF_DIR), capture_output=True, text=True, timeout=60,
            )
        if result.returncode != 0 or not pdf_path.exists():
            log_tail = "\n".join((result.stdout or "").splitlines()[-30:])
            return None, f"LaTeX compilation failed:\n{log_tail}"
        return pdf_path, None
    except subprocess.TimeoutExpired:
        return None, "LaTeX compilation timed out after 60s."
    except Exception as e:
        return None, f"Compilation error: {e}"


def _google_creds():
    if not _GCAL_AVAILABLE:
        raise RuntimeError(
            "Google libraries missing. Run:\npip3 install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib")
    if not GCAL_CREDS.exists():
        raise RuntimeError(
            f"No credentials file at {GCAL_CREDS}. Download OAuth credentials "
            "from Google Cloud Console and save them there.")
    creds = None
    if GCAL_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(GCAL_TOKEN), GCAL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(str(GCAL_CREDS), GCAL_SCOPES)
            creds = flow.run_local_server(port=0)
        GCAL_TOKEN.write_text(creds.to_json())
    return creds


def drive_backup():
    try:
        creds = _google_creds()
        service = _gcal_build("drive", "v3", credentials=creds)
        existing = service.files().list(
            q=f"name='{DRIVE_BACKUP_NAME}' and trashed=false",
            spaces="drive", fields="files(id,name)").execute().get("files", [])
        media = MediaFileUpload(str(DB_PATH), mimetype="application/x-sqlite3")
        if existing:
            service.files().update(fileId=existing[0]["id"], media_body=media).execute()
        else:
            service.files().create(
                body={"name": DRIVE_BACKUP_NAME}, media_body=media, fields="id").execute()
        return {"ok": True, "time": datetime.now().isoformat(timespec="seconds")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _auto_backup_loop():
    import time
    time.sleep(15)
    while True:
        if GCAL_CREDS.exists():
            r = drive_backup()
            print(f"[drive backup] {r}")
        time.sleep(DRIVE_BACKUP_EVERY_SEC)


CALENDAR_SYNC_EVERY_SEC = 10 * 60


def _auto_calendar_sync_loop():
    import time
    time.sleep(20)
    while True:
        if GCAL_TOKEN.exists():
            try:
                print(f"[auto gcal sync] {gcal_sync()}")
            except Exception as e:
                print(f"[auto gcal sync] error: {e}")
        if ICLOUD_CREDS.exists():
            try:
                print(f"[auto icloud sync] {icloud_sync()}")
            except Exception as e:
                print(f"[auto icloud sync] error: {e}")
        time.sleep(CALENDAR_SYNC_EVERY_SEC)


def gcal_sync():
    try:
        creds = _google_creds()
        service = _gcal_build("calendar", "v3", credentials=creds)
        now = datetime.utcnow().isoformat() + "Z"
        result = service.events().list(calendarId="primary", timeMin=now,
                                       maxResults=200, singleEvents=True,
                                       orderBy="startTime").execute()
        events = result.get("items", [])
        imported = updated = 0
        conn = db()
        for ev in events:
            gid   = ev.get("id", "")
            title = ev.get("summary", "(No title)")
            loc   = ev.get("location", "")
            desc  = ev.get("description", "")
            start = ev["start"].get("dateTime", ev["start"].get("date", ""))
            end_s = ev["end"].get("dateTime", ev["end"].get("date", ""))
            if "T" in start:
                ds = datetime.fromisoformat(start.replace("Z", "+00:00"))
                ev_date, ev_time = str(ds.date()), ds.strftime("%H:%M")
            else:
                ev_date, ev_time = start, ""
            if "T" in end_s:
                de = datetime.fromisoformat(end_s.replace("Z", "+00:00"))
                ev_end = de.strftime("%H:%M")
            else:
                ev_end = ""
            row = conn.execute("SELECT id FROM meetings WHERE google_id=?", (gid,)).fetchone()
            if row:
                conn.execute("UPDATE meetings SET title=?,meeting_date=?,start_time=?,"
                             "end_time=?,location=?,notes=? WHERE google_id=?",
                             (title, ev_date, ev_time, ev_end, loc, desc, gid))
                updated += 1
            else:
                conn.execute("INSERT INTO meetings(title,meeting_date,start_time,end_time,"
                             "location,notes,meeting_type,created_at,google_id) "
                             "VALUES(?,?,?,?,?,?,?,?,?)",
                             (title, ev_date, ev_time, ev_end, loc, desc, "Other",
                              datetime.now().isoformat(timespec="seconds"), gid))
                imported += 1
        conn.commit()
        conn.close()
        return {"ok": True, "imported": imported, "updated": updated}
    except Exception as ex:
        return {"ok": False, "error": f"Sync failed: {ex}"}


def _icloud_creds():
    if not ICLOUD_CREDS.exists():
        return None
    try:
        return json.loads(ICLOUD_CREDS.read_text())
    except Exception:
        return None


def _save_icloud_creds(email, app_password):
    DB_DIR.mkdir(parents=True, exist_ok=True)
    ICLOUD_CREDS.write_text(json.dumps({"email": email, "app_password": app_password}))
    try:
        os.chmod(ICLOUD_CREDS, 0o600)
    except Exception:
        pass


def _icloud_disconnect():
    try:
        if ICLOUD_CREDS.exists():
            ICLOUD_CREDS.unlink()
    except Exception:
        pass


def icloud_status():
    creds = _icloud_creds()
    return {"connected": bool(creds), "email": (creds or {}).get("email", "")}


def _ical_text(vevent, key, default=""):
    v = vevent.get(key)
    return str(v) if v is not None else default


def icloud_sync():
    try:
        if not _CALDAV_AVAILABLE:
            raise RuntimeError(
                "The 'caldav' library isn't installed. Run:\npip3 install caldav")
        creds = _icloud_creds()
        if not creds or not creds.get("email") or not creds.get("app_password"):
            raise RuntimeError(
                "iCloud isn't connected yet — click 'Connect iCloud Calendar' first.")

        client = caldav.DAVClient(url=ICLOUD_URL, username=creds["email"],
                                   password=creds["app_password"])
        principal = client.principal()
        calendars = principal.calendars()

        window_start = datetime.now() - timedelta(days=30)
        window_end = datetime.now() + timedelta(days=180)

        imported = updated = 0
        conn = db()
        for cal in calendars:
            try:
                if hasattr(cal, "search"):
                    events = cal.search(start=window_start, end=window_end,
                                         event=True, expand=True)
                else:
                    events = cal.date_search(start=window_start, end=window_end)
            except Exception:
                continue
            for ev in events:
                try:
                    vevents = ev.icalendar_instance.walk("VEVENT")
                    if not vevents:
                        continue
                    vevent = vevents[0]
                except Exception:
                    continue
                uid = _ical_text(vevent, "uid")
                if not uid:
                    continue
                title = _ical_text(vevent, "summary", "(No title)")
                location = _ical_text(vevent, "location")
                description = _ical_text(vevent, "description")
                dtstart_prop = vevent.get("dtstart")
                if dtstart_prop is None:
                    continue
                dtstart = dtstart_prop.dt
                dtend_prop = vevent.get("dtend")
                dtend = dtend_prop.dt if dtend_prop is not None else None
                if isinstance(dtstart, datetime):
                    ev_date, ev_time = dtstart.date().isoformat(), dtstart.strftime("%H:%M")
                else:
                    ev_date, ev_time = dtstart.isoformat(), ""
                ev_end = dtend.strftime("%H:%M") if isinstance(dtend, datetime) else ""

                row = conn.execute("SELECT id FROM meetings WHERE caldav_uid=?", (uid,)).fetchone()
                if row:
                    conn.execute(
                        "UPDATE meetings SET title=?,meeting_date=?,start_time=?,"
                        "end_time=?,location=?,notes=? WHERE caldav_uid=?",
                        (title, ev_date, ev_time, ev_end, location, description, uid))
                    updated += 1
                else:
                    conn.execute(
                        "INSERT INTO meetings(title,meeting_date,start_time,end_time,"
                        "location,notes,meeting_type,created_at,caldav_uid) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (title, ev_date, ev_time, ev_end, location, description, "Other",
                         datetime.now().isoformat(timespec="seconds"), uid))
                    imported += 1
        conn.commit()
        conn.close()
        return {"ok": True, "imported": imported, "updated": updated}
    except Exception as ex:
        return {"ok": False, "error": f"Sync failed: {ex}"}


HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#8a6dff">
<title>VanoAtlas</title>
<link rel="manifest" href="/manifest.json">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="VanoAtlas">
<style>
:root{
  --bg:rgba(251,247,239,.88); --panel:rgba(255,253,248,.97); --panel2:rgba(240,232,212,.95); --panel3:rgba(244,236,220,.95);
  --side:rgba(244,236,220,.96); --line:#DDD5C4; --line2:#C9BFA8;
  --photowash:linear-gradient(180deg,rgba(251,247,239,.62),rgba(251,247,239,.88));
  --txt:#2C2620; --mut:#5C5347; --dim:#948A78;
  --acc:#1F4E37; --acc2:#1F4E37; --accbg:#EAF2EA;
  --red:#93360F; --redbg:#FDF0E8;
  --amber:#C9881E; --amberbg:rgba(201,136,30,.14);
  --green:#4E7A4E; --greenbg:#E8F2E8;
  --teal:#C9881E; --tealbg:rgba(201,136,30,.14);
  --pink:#BD4B1B; --r:10px;
  --st-todo:#BD4B1B; --st-wip:#1F4E37; --st-rev:#4E7A4E; --st-done:#4E7A4E;
  --pri-hi:#93360F; --pri-no:#1F4E37; --pri-lo:#948A78;
  --pc0:#1F4E37; --pc1:#BD4B1B; --pc2:#C9881E; --pc3:#4E7A4E;
  --pc4:#93360F; --pc5:#7A5C2E; --pc6:#3E6B54; --pc7:#A0561F;
  --logo1:#1F4E37; --logo2:#4E7A4E; --oninv:#FBF7EF;
  --sh1:0 4px 14px rgba(44,38,32,.10);
  --sh2:0 24px 70px rgba(44,38,32,.22);
  --sh3:0 10px 30px rgba(44,38,32,.18);
  --ovlc:rgba(44,38,32,.38);
  color-scheme:light;
}
:root[data-theme="night"]{
  --bg:rgba(10,14,28,.88); --panel:rgba(16,22,45,.97); --panel2:rgba(22,30,60,.95); --panel3:rgba(28,37,71,.95);
  --side:rgba(16,22,45,.96); --line:#232d54; --line2:#2d3a6b;
  --photowash:linear-gradient(180deg,rgba(4,7,18,.62),rgba(4,7,18,.90));
  --txt:#e8edfb; --mut:#8a95bb; --dim:#5c6690;
  --acc:#6366f1; --acc2:#818cf8; --accbg:rgba(99,102,241,.14);
  --red:#f87171; --redbg:rgba(248,113,113,.13);
  --amber:#fbbf24; --amberbg:rgba(251,191,36,.12);
  --green:#34d399; --greenbg:rgba(52,211,153,.12);
  --teal:#2dd4bf; --tealbg:rgba(45,212,191,.12);
  --pink:#f472b6;
  --st-todo:#8a95bb; --st-wip:#818cf8; --st-rev:#fbbf24; --st-done:#34d399;
  --pri-hi:#f87171; --pri-no:#818cf8; --pri-lo:#5c6690;
  --pc0:#6366f1; --pc1:#2dd4bf; --pc2:#f472b6; --pc3:#fbbf24;
  --pc4:#34d399; --pc5:#f87171; --pc6:#818cf8; --pc7:#fb923c;
  --logo1:#6366f1; --logo2:#8b5cf6; --oninv:#0a0e1c;
  --sh1:0 4px 14px rgba(0,0,0,.35);
  --sh2:0 24px 70px rgba(0,0,0,.5);
  --sh3:0 10px 30px rgba(0,0,0,.4);
  --ovlc:rgba(4,7,18,.66);
  color-scheme:dark;
}
:root[data-theme="quantum"]{
  --bg:rgba(8,6,24,.90); --panel:rgba(15,10,38,.97); --panel2:rgba(22,14,52,.95); --panel3:rgba(28,18,64,.95);
  --side:rgba(15,10,38,.96); --line:#2c1f5c; --line2:#3d2a7a;
  --photowash:linear-gradient(180deg,rgba(6,4,20,.65),rgba(6,4,20,.92));
  --txt:#eae6ff; --mut:#9c8fd6; --dim:#635698;
  --acc:#a855f7; --acc2:#22d3ee; --accbg:rgba(168,85,247,.16);
  --red:#fb7185; --redbg:rgba(251,113,133,.14);
  --amber:#facc15; --amberbg:rgba(250,204,21,.12);
  --green:#34d399; --greenbg:rgba(52,211,153,.12);
  --teal:#22d3ee; --tealbg:rgba(34,211,238,.13);
  --pink:#f472b6;
  --st-todo:#9c8fd6; --st-wip:#a855f7; --st-rev:#facc15; --st-done:#34d399;
  --pri-hi:#fb7185; --pri-no:#a855f7; --pri-lo:#635698;
  --pc0:#a855f7; --pc1:#22d3ee; --pc2:#f472b6; --pc3:#facc15;
  --pc4:#34d399; --pc5:#fb7185; --pc6:#818cf8; --pc7:#fb923c;
  --logo1:#a855f7; --logo2:#22d3ee; --oninv:#08061a;
  --sh1:0 4px 18px rgba(168,85,247,.25);
  --sh2:0 24px 80px rgba(80,20,180,.45);
  --sh3:0 10px 34px rgba(168,85,247,.32);
  --ovlc:rgba(6,4,20,.72);
  color-scheme:dark;
}
:root[data-theme="quantum"] .mark{animation:quantumGlow 3s ease-in-out infinite}
@keyframes quantumGlow{
  0%,100%{box-shadow:0 0 6px 0 rgba(168,85,247,.55),0 0 0 0 rgba(34,211,238,0)}
  50%{box-shadow:0 0 16px 4px rgba(168,85,247,.75),0 0 22px 6px rgba(34,211,238,.45)}
}
#view.viewenter{animation:viewIn .28s cubic-bezier(.2,.7,.3,1)}
@keyframes viewIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.card,.panel,.kpi,.col,.task,.chip,input,select,textarea,#side,#top,#modal{
  transition:background-color .25s ease,border-color .25s ease,color .25s ease,box-shadow .25s ease;
}
.btn{transition:background-color .18s ease,color .18s ease,border-color .18s ease,transform .12s ease}
.btn:active{transform:scale(.96)}
.btn.ghost.on{background:var(--accbg);color:var(--acc2);border-color:transparent}
.task{transition:transform .15s ease,box-shadow .15s ease}
.task:hover{transform:translateY(-2px)}
.gnode circle{transition:r .15s,fill .15s}
.gnode:hover circle{r:12;fill:var(--acc2)}
.gnode:hover text{fill:var(--acc2);font-weight:600}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{
  background:transparent;color:var(--txt);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Inter,Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;overflow:hidden;
}
.bgphoto{position:fixed;inset:0;display:flex}
.bgphoto>div{flex:1 1 50%;background-size:cover;background-position:center}
.bgwash{position:fixed;inset:0;background:var(--photowash);pointer-events:none}
::-webkit-scrollbar{width:9px;height:9px}
::-webkit-scrollbar-thumb{background:var(--line2);border-radius:6px}
::-webkit-scrollbar-track{background:transparent}
button{font:inherit;color:inherit;background:none;border:none;cursor:pointer}
input,select,textarea{
  font:inherit;color:var(--txt);background:var(--panel3);
  border:1px solid var(--line2);border-radius:8px;padding:7px 10px;outline:none;
  transition:border-color .12s;
}
input:focus,select:focus,textarea:focus{border-color:var(--acc)}
textarea{resize:vertical;min-height:80px}

#app{display:flex;flex-direction:column;height:100vh}
#side{
  width:100%;background:var(--side);border-right:none;border-bottom:1px solid var(--line);
  display:flex;flex-direction:row;align-items:center;padding:8px 14px;gap:2px;overflow-x:auto;flex:none;
}
#logo{display:flex;align-items:center;gap:8px;padding:0 12px 0 0;flex:none}
#logo .mark{
  width:28px;height:28px;border-radius:8px;display:grid;place-items:center;
  background:linear-gradient(135deg,var(--logo1),var(--logo2));color:#fff;font-weight:700;font-size:14px;
}
#logo b{font-size:14px;letter-spacing:.2px}
#logo span{display:none}
.nav{
  display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:8px;
  color:var(--mut);font-weight:500;width:auto;text-align:left;transition:all .12s;
  font-size:13px;white-space:nowrap;flex:none;
}
.nav:hover{background:var(--panel2);color:var(--txt)}
.nav.on{background:var(--accbg);color:var(--acc2)}
.nav svg{width:16px;height:16px;flex:none}
.nav .k{display:none}
#side .grow{flex:1;min-width:8px}
#sync,#theme,#icloudsync,#icloudcfg,#drivebk,#notifbtn{margin:0 0 0 6px;padding:8px 12px;border-radius:8px;background:var(--panel2);
  color:var(--mut);font-size:12.5px;display:flex;gap:8px;align-items:center;justify-content:center;transition:all .12s;flex:none;white-space:nowrap}
#sync:hover,#theme:hover,#icloudsync:hover,#icloudcfg:hover,#drivebk:hover,#notifbtn:hover{color:var(--txt);background:var(--panel3)}

#main{flex:1;display:flex;flex-direction:column;min-width:0}
#top{
  height:56px;min-height:56px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:12px;padding:0 20px;background:var(--panel);
}
#vtitle{font-size:16px;font-weight:650}
#search{width:250px;background:var(--panel2);border-color:var(--line)}
#top .sp{flex:1}
.btn{
  display:inline-flex;align-items:center;gap:7px;padding:8px 14px;border-radius:8px;
  font-weight:550;font-size:13px;transition:all .12s;white-space:nowrap;
}
.btn.pri{background:var(--acc);color:#fff}
.btn.pri:hover{background:#5457e8}
.btn.ghost{background:var(--panel2);color:var(--mut);border:1px solid var(--line)}
.btn.ghost:hover{color:var(--txt);border-color:var(--line2)}
.btn.danger{background:var(--redbg);color:var(--red)}
#view{flex:1;overflow:auto;padding:20px;background:var(--bg)}

.chip{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;
  padding:2px 8px;border-radius:20px;white-space:nowrap}
.chip.timerange{background:var(--panel3);color:var(--mut);font-size:10.5px;flex:none;
  font-variant-numeric:tabular-nums;letter-spacing:.2px}
.chip.timerange .tarrow{width:16px;height:1.5px;background:var(--dim);position:relative;flex:none;margin:0 1px}
.chip.timerange .tarrow::after{content:'';position:absolute;right:-1px;top:-2.5px;border:3px solid transparent;
  border-left-color:var(--dim)}
.dot{width:7px;height:7px;border-radius:50%;flex:none}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r)}
.empty{color:var(--dim);text-align:center;padding:40px 10px;font-size:13px}
h3.sec{font-size:12px;font-weight:650;color:var(--mut);text-transform:uppercase;
  letter-spacing:.7px;margin:0 0 10px}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.kpi{padding:16px;background:var(--panel);border:1px solid var(--line);border-radius:var(--r)}
.kpi b{font-size:26px;font-weight:700;display:block;line-height:1.15}
.kpi span{font-size:12px;color:var(--mut)}
.dashgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px}
.row{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:8px;
  transition:background .12s;cursor:pointer}
.row:hover{background:var(--panel2)}
.row .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}
.row .d{font-size:11.5px;color:var(--mut);white-space:nowrap}
.pbar{height:6px;background:var(--panel3);border-radius:4px;overflow:hidden;flex:1}
.pbar i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),var(--acc2));border-radius:4px;transition:width .3s}

#board{display:flex;gap:14px;align-items:flex-start;height:100%;min-height:400px}
.col{
  width:272px;min-width:272px;max-width:272px;background:var(--panel);border:1px solid var(--line);
  border-radius:var(--r);display:flex;flex-direction:column;max-height:100%;
}
.col.over{border-color:var(--acc);background:var(--panel2)}
.colhead{display:flex;align-items:center;gap:8px;padding:12px 14px;font-weight:650;font-size:13px}
.colhead .n{color:var(--dim);font-weight:600;font-size:12px}
.colhead .add{margin-left:auto;color:var(--dim);font-size:16px;padding:0 4px;border-radius:6px}
.colhead .add:hover{color:var(--acc2)}
.collist{padding:4px 10px 12px;overflow-y:auto;overflow-x:hidden;display:flex;flex-direction:column;gap:8px;min-height:30px;min-width:0}
.task{
  background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:11px 12px;
  cursor:grab;transition:transform .12s,border-color .12s,box-shadow .12s;min-width:0;max-width:100%;box-sizing:border-box;
}
.task:hover{border-color:var(--line2);transform:translateY(-1px);box-shadow:var(--sh1)}
.task.drag{opacity:.4}
.task .tt{font-weight:550;font-size:13px;line-height:1.35;display:flex;gap:7px;align-items:flex-start;min-width:0}
.task .tt span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.task .tt .dot{margin-top:5px}
.task .meta{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px;min-width:0}
.task .meta .chip{max-width:100%;overflow:hidden;text-overflow:ellipsis;min-width:0}
.task .desc{font-size:12px;color:var(--mut);margin-top:5px;display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tmeta{display:flex;align-items:center;gap:7px;margin-top:8px;padding-top:7px;border-top:1px solid var(--line)}
.tbtn{width:22px;height:22px;flex:none;border-radius:50%;background:var(--panel3);color:var(--mut);
  display:flex;align-items:center;justify-content:center;font-size:10px;cursor:pointer;transition:all .12s}
.tbtn:hover{background:var(--accbg);color:var(--acc2)}
.tbtn.on{background:var(--red);color:#fff}
.ttime{font-size:11px;color:var(--dim)}

.projgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px}
.proj{padding:16px;cursor:pointer;transition:transform .12s,border-color .12s}
.proj:hover{transform:translateY(-2px);border-color:var(--line2)}
.proj h4{font-size:14.5px;margin-bottom:4px}
.proj .dom{font-size:11.5px;color:var(--mut)}
.proj .stats{display:flex;align-items:center;gap:10px;margin-top:12px;font-size:12px;color:var(--mut)}

#aghead{display:flex;align-items:center;gap:10px;margin-bottom:14px}
#agtitle{font-size:16px;font-weight:650;min-width:170px}
.aggrid{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.agdow{font-size:11px;font-weight:650;color:var(--dim);text-transform:uppercase;
  letter-spacing:.5px;text-align:center;padding:4px 0}
.agcell{
  background:var(--panel);border:1px solid var(--line);border-radius:8px;min-height:96px;
  padding:6px;cursor:pointer;transition:border-color .12s;overflow:hidden;
}
.agcell:hover{border-color:var(--line2)}
.agcell.out{opacity:.38}
.agcell.today{border-color:var(--acc)}
.agcell.sel{border-color:var(--acc2);box-shadow:0 0 0 1px var(--acc2)}
.agcell .dn{font-size:11.5px;font-weight:600;color:var(--mut);margin-bottom:4px}
.agcell.today .dn{color:var(--acc2)}
.agev{font-size:10.5px;font-weight:550;padding:1.5px 6px;border-radius:5px;margin-bottom:3px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agmore{font-size:10px;color:var(--dim)}
.agdots{display:none}
#agsplit{display:grid;grid-template-columns:1fr 300px;gap:16px;align-items:start}
#agside{position:sticky;top:0}
.agweek{display:grid;grid-template-columns:repeat(7,1fr);gap:8px}
.agwcol{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  min-height:300px;padding:8px}
.agwcol.today{border-color:var(--acc)}
.agwcol h5{font-size:11px;color:var(--mut);text-align:center;margin-bottom:8px;font-weight:600}
.agwcol.today h5{color:var(--acc2)}
.agwev{font-size:11px;padding:5px 7px;border-radius:6px;margin-bottom:5px;cursor:pointer;line-height:1.3}
.agwev b{display:block;font-size:10px;opacity:.75;font-weight:600}

.dailywrap{max-width:640px}
.dcat{margin-bottom:18px}
.drow{display:flex;align-items:center;gap:10px;padding:9px 12px;background:var(--panel);
  border:1px solid var(--line);border-radius:8px;margin-bottom:6px;transition:opacity .15s}
.drow.done .t{text-decoration:line-through;color:var(--dim)}
.chk{width:18px;height:18px;border-radius:5px;border:1.5px solid var(--line2);flex:none;
  display:grid;place-items:center;font-size:11px;color:#fff;
  transition:background-color .15s,border-color .15s,transform .22s cubic-bezier(.34,1.56,.64,1)}
.chk.on{background:var(--green);border-color:var(--green);color:var(--oninv);transform:scale(1.08)}

#noteswrap{display:grid;grid-template-columns:240px 1fr;gap:16px;height:100%}
.notesidebar{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
  padding:10px 8px;display:flex;flex-direction:column;min-height:0;align-self:start;
  max-height:100%;box-shadow:var(--sh1)}
#notelist{overflow-y:auto;display:flex;flex-direction:column;gap:1px;padding-right:2px}
.notecat{display:flex;flex-direction:column}
.notecathead{display:flex;align-items:center;gap:6px;padding:7px 6px;cursor:pointer;
  border-radius:6px;font-size:11.5px;font-weight:650;color:var(--mut);
  text-transform:uppercase;letter-spacing:.3px;user-select:none}
.notecathead:hover{background:var(--panel2);color:var(--txt)}
.notecathead .chev{width:10px;flex:none;font-size:9px;transition:transform .12s;opacity:.7;text-transform:none}
.notecat.collapsed .notecathead .chev{transform:rotate(-90deg)}
.notecathead .cname{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.notecathead .ccount{font-size:10.5px;color:var(--dim);font-weight:600}
.notecathead .cadd{width:16px;height:16px;border-radius:5px;flex:none;display:grid;place-items:center;
  font-size:13px;color:var(--mut);opacity:0;transition:opacity .12s,background .12s;text-transform:none}
.notecathead:hover .cadd{opacity:1}
.notecathead .cadd:hover{background:var(--panel3);color:var(--acc2)}
.notecat-notes{display:flex;flex-direction:column;gap:0;margin-bottom:6px}
.notecat.collapsed .notecat-notes{display:none}
.noteitem{display:flex;align-items:center;gap:4px;padding:5px 6px 5px 20px;cursor:pointer;
  border-radius:6px;font-size:12.5px;color:var(--txt)}
.noteitem:hover{background:var(--panel2)}
.noteitem.on{background:var(--accbg);color:var(--acc2);font-weight:600}
.noteitem .nt{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.noteitem select.notemove{opacity:0;flex:none;width:14px;font-size:10px;background:none;border:none;
  color:var(--dim);padding:0;cursor:pointer;transition:opacity .12s;appearance:none;-webkit-appearance:none;text-align:center}
.noteitem:hover select.notemove,.noteitem select.notemove:focus{opacity:.85}
.notecat-empty{padding:4px 6px 6px 20px;font-size:11px;color:var(--dim);font-style:italic}
#noteedit{display:flex;flex-direction:column;gap:10px;min-height:0;height:100%}
.notetoolbar{display:flex;flex-wrap:wrap;gap:4px;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--panel3)}
.notetoolbar button{border:1px solid transparent;background:transparent;color:var(--txt);border-radius:6px;
  padding:5px 9px;font-size:12.5px;cursor:pointer;line-height:1;transition:background .12s,border-color .12s}
.notetoolbar button:hover{background:var(--panel2);border-color:var(--line)}
.notetoolbar button.micbtn{font-weight:600;flex:none}
.notetoolbar button.micbtn.rec{background:rgba(220,60,60,.14);border-color:rgba(220,60,60,.5);color:#c53535;
  animation:micpulse 1.1s ease-in-out infinite}
@keyframes micpulse{0%,100%{box-shadow:0 0 0 0 rgba(220,60,60,.35)}50%{box-shadow:0 0 0 5px rgba(220,60,60,0)}}
.notetoolbar .tbsep{width:1px;align-self:stretch;background:var(--line);margin:2px 3px;flex:none}
.notetoolbar button.tb-b{font-weight:800}
.notetoolbar button.tb-i{font-style:italic}
.notetoolbar button.tb-u{text-decoration:underline}
.notetoolbar button.tb-s{text-decoration:line-through}
.notetoolbar select.dictlang{border:1px solid var(--line);border-radius:6px;background:var(--panel2);
  color:var(--txt);font-size:11.5px;padding:4px 6px;flex:none;max-width:150px}
.notesplit{display:flex;gap:10px;flex:1;min-height:60vh}
.notesplit textarea{flex:1;min-height:300px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.notebody-word{flex:1;min-height:300px;background:#fff;color:#1a1a1a;border:1px solid var(--line);
  border-radius:8px;padding:22px 26px;font-family:Georgia,'Times New Roman',serif;font-size:14.5px;
  line-height:1.65;overflow:auto;outline:none}
.notebody-word h1{font-size:22px;margin:.6em 0 .3em}
.notebody-word h2{font-size:18px;margin:.6em 0 .3em}
.notebody-word h3{font-size:15.5px;margin:.6em 0 .3em}
.notebody-word p{margin:0 0 .9em}
.notebody-word blockquote{margin:.6em 0;padding:4px 16px;border-left:3px solid #c9a86a;color:#555;font-style:italic}
.notebody-word ul,.notebody-word ol{margin:.4em 0 .9em;padding-left:26px}
.notebody-word pre{background:#f3f1ea;border-radius:6px;padding:10px 12px;font-family:ui-monospace,Menlo,monospace;
  font-size:12.5px;overflow:auto;white-space:pre-wrap}
.notebody-word hr{border:none;border-top:1px solid #ccc;margin:1em 0}
.notewordtable{border-collapse:collapse;margin:.6em 0}
.notewordtable td{border:1px solid #ccc;padding:6px 10px;min-width:60px}
.notewordbreak{text-align:center;color:#999;font-size:11px;letter-spacing:1px;margin:1em 0;
  border-top:1px dashed #ccc;border-bottom:1px dashed #ccc;padding:4px 0}
.notewordfn{color:#8a5a2b;cursor:help;font-weight:600}
.latexmath{display:inline-block;background:#eef2fb;color:#2b4a8a;border:1px solid #d3ddf5;border-radius:4px;
  padding:0 5px;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;cursor:default}
.notemodebtn{font-weight:600}
.pdfpane{flex:1.15;min-width:0;border:1px solid var(--line);border-radius:8px;background:#525659;
  display:flex;align-items:center;justify-content:center;overflow:hidden}
.splith{width:6px;flex:none;cursor:col-resize;background:var(--line);border-radius:3px;transition:background .12s}
.splith:hover,.splith.active{background:var(--acc)}
.pdfpane iframe{width:100%;height:100%;border:0;background:#fff}
.pdfpane .empty{color:#c7c9cb;font-size:12.5px}
.pdferr{padding:12px;font-size:11.5px;color:var(--red);white-space:pre-wrap;overflow:auto;
  height:100%;width:100%;box-sizing:border-box;margin:0;font-family:ui-monospace,Menlo,monospace}
@media (max-width:900px){ .notesplit{flex-direction:column} .pdfpane{min-height:260px} }

.winrow{display:flex;gap:12px;padding:13px 15px;margin-bottom:8px;align-items:flex-start}
.winrow .ic{font-size:19px;width:30px;text-align:center;flex:none}
.winrow h5{font-size:13.5px}
.winrow p{font-size:12px;color:var(--mut)}
.stars{color:var(--amber);letter-spacing:1.5px;font-size:12px}

.tsintro{font-size:12.5px;color:var(--mut);margin-bottom:14px;max-width:640px;line-height:1.5}
.fintabs{display:flex;gap:6px;margin-bottom:16px}
.fintab{padding:7px 14px;border-radius:8px;font-size:13px;font-weight:550;color:var(--mut);
  background:var(--panel);border:1px solid var(--line)}
.fintab.on{background:var(--accbg);color:var(--acc2);border-color:transparent}
table.tbl{width:100%;border-collapse:collapse}
.tbl th{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim);
  text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
.tbl td{padding:9px 10px;border-bottom:1px solid var(--line);font-size:13px}
.tbl tr:hover td{background:var(--panel2)}

#ovl{position:fixed;inset:0;background:var(--ovlc);backdrop-filter:blur(3px);
  display:none;place-items:center;z-index:50}
#ovl.show{display:grid}
#modal{width:460px;max-width:94vw;max-height:88vh;overflow-y:auto;background:var(--panel2);
  border:1px solid var(--line2);border-radius:14px;padding:20px;
  box-shadow:var(--sh2);animation:pop .16s ease}
@keyframes pop{from{transform:scale(.96);opacity:0}to{transform:scale(1);opacity:1}}
#modal h3{font-size:15px;margin-bottom:16px}
.field{margin-bottom:12px}
.field label{display:block;font-size:11.5px;font-weight:600;color:var(--mut);margin-bottom:5px}
.field input,.field select,.field textarea{width:100%}
.frow{display:grid;grid-template-columns:1fr 1fr;gap:10px}
#modal .acts{display:flex;gap:8px;margin-top:18px}
#modal .acts .sp{flex:1}

@media (max-width:1180px){
  #search{width:170px}
}
@media (max-width:1000px){
  #side{gap:1px}
  .nav{padding:9px 10px;font-size:0;gap:0;justify-content:center}
  .nav svg{width:18px;height:18px}
  #sync span,#theme span,#icloudsync span,#drivebk span,#notifbtn span{display:none}
  #sync,#theme,#icloudsync,#drivebk,#notifbtn{padding:9px 10px}
  #search{width:130px}
}

@media (max-width:760px){
  #app{flex-direction:column-reverse}
  #side{
    width:100%;min-width:0;height:60px;flex-direction:row;align-items:center;
    padding:6px 8px calc(6px + env(safe-area-inset-bottom));gap:2px;
    border-right:none;border-top:1px solid var(--line);overflow-x:auto;
  }
  #logo{display:none}
  .nav{width:auto;padding:9px 11px;font-size:0;gap:0;justify-content:center}
  .nav svg{width:20px;height:20px}
  .nav .k{display:none}
  #side .grow{min-width:4px}
  #sync,#theme,#icloudsync,#icloudcfg,#drivebk{margin:0 0 0 2px;padding:9px 11px}
  #synctxt,#themetxt,#icloudtxt,#drivetxt{display:none}
  #top{height:52px;min-height:52px;padding:0 12px;gap:8px}
  #vtitle{font-size:15px}
  #search{width:110px;flex:1;min-width:0}
  #view{padding:12px;-webkit-overflow-scrolling:touch}
  .col{width:248px;min-width:248px}
  .kpis{grid-template-columns:repeat(3,1fr);gap:8px}
  .kpi{padding:12px}
  .kpi b{font-size:20px}
  .dashgrid{grid-template-columns:1fr}
  .projgrid{grid-template-columns:1fr}
  #noteswrap{grid-template-columns:1fr;grid-template-rows:auto 1fr}
  #notelist{max-height:34vh;flex-direction:column;overflow-x:hidden;overflow-y:auto}
  .agcell{min-height:64px;padding:4px}
  .agev{display:none}
  .agcell .dn{margin-bottom:2px}
  .agdots{display:flex;gap:2px;flex-wrap:wrap}
  #aghead{flex-wrap:wrap}
  .agweek{grid-template-columns:1fr;gap:6px}
  .agwcol{min-height:0}
  #modal{width:100%;max-width:100vw;max-height:92vh;border-radius:14px 14px 0 0;
    align-self:end}
  .frow{grid-template-columns:1fr}
}
@media (max-width:760px){ #ovl.show{align-items:end} }

#side,#top,#view,.card,.panel,.kpi,.col,.task,#modal,.tbl,.agwcol,.agcell,.drow,.row,.notesidebar,
input,select,textarea,.pbar,#toast,#sync,#theme,#icloudsync,#icloudcfg,#drivebk{
  backdrop-filter:blur(18px) saturate(1.15);
  -webkit-backdrop-filter:blur(18px) saturate(1.15);
}

#toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%) translateY(80px);
  background:var(--panel3);border:1px solid var(--line2);color:var(--txt);
  padding:10px 18px;border-radius:10px;font-size:13px;z-index:60;
  transition:transform .25s ease;box-shadow:var(--sh3)}
#toast.show{transform:translateX(-50%) translateY(0)}

.hwovl{position:fixed;inset:0;background:rgba(4,7,18,.55);backdrop-filter:blur(6px);
  -webkit-backdrop-filter:blur(6px);display:none;place-items:center;z-index:70;padding:24px}
.hwovl.show{display:grid}
.hwpanel{display:flex;gap:16px;max-width:1100px;width:100%;max-height:92vh}
.hwpage-wrap{flex:1.3;overflow:auto;border-radius:10px;box-shadow:var(--sh2);
  display:flex;justify-content:center;padding:12px;background:rgba(0,0,0,.15)}
.hw-page{width:640px;min-height:880px;padding:56px 48px;box-sizing:border-box;
  font-size:22px;line-height:2;word-wrap:break-word;flex:none}
.hw-h{font-weight:400}
.hw-h1{font-size:1.7em;margin-bottom:.3em}
.hw-h2{font-size:1.35em;margin-bottom:.25em}
.hw-h3{font-size:1.15em;margin-bottom:.2em}
.hw-p{margin-bottom:.5em}
.hw-li{display:flex;gap:8px;margin-bottom:.3em}
.hw-bullet{opacity:.7}
.hw-math{font-family:KaTeX_Main,serif;display:inline-block;vertical-align:middle;margin:0 .15em}
.hw-math-fallback{font-family:ui-monospace,Menlo,monospace;font-size:.7em;opacity:.75}
.hwside{width:230px;flex:none;background:var(--panel);border:1px solid var(--line);
  border-radius:var(--r);padding:16px;display:flex;flex-direction:column;gap:12px;overflow-y:auto}
.hwside h3{font-size:14px}
.hwside .field label{display:block;font-size:11px;font-weight:600;color:var(--mut);margin-bottom:5px}
.hwside select,.hwside input[type=range]{width:100%}
.hwside-acts{display:flex;flex-direction:column;gap:8px;margin-top:auto}
.hwstatus{font-size:11px;color:var(--dim);min-height:14px}
@media (max-width:800px){ .hwpanel{flex-direction:column;max-height:96vh} .hw-page{width:100%;min-width:0} }
@media print{
  .bgphoto,.bgwash,#app,#ovl,#toast{display:none!important}
  .hwovl{position:static!important;background:none!important;padding:0!important;
    display:block!important;backdrop-filter:none!important;-webkit-backdrop-filter:none!important}
  .hwpanel{display:block!important;max-width:none!important;max-height:none!important}
  .hwpage-wrap{all:unset!important;display:block!important;overflow:visible!important;background:none!important}
  .hwside{display:none!important}
  #hwPage{width:auto!important;min-height:0!important;box-shadow:none!important;margin:0!important;
    backdrop-filter:none!important;-webkit-backdrop-filter:none!important}
  @page{ size:A4; margin:12mm; }
}

:root{
  --bg:rgba(8,12,31,.86); --panel:rgba(14,18,42,.76); --panel2:rgba(22,28,64,.74); --panel3:rgba(27,34,79,.74);
  --side:rgba(8,11,28,.82); --line:rgba(137,152,255,.14); --line2:rgba(137,152,255,.25);
  --photowash:linear-gradient(180deg,rgba(6,8,20,.62),rgba(6,8,20,.88)); --txt:#f5f7ff; --mut:#b7bfeb; --dim:#7982b9;
  --acc:#8a6dff; --acc2:#b18cff; --accbg:rgba(138,109,255,.16); --red:#ff708e; --redbg:rgba(255,112,142,.14);
  --amber:#ffca57; --amberbg:rgba(255,202,87,.14); --green:#32d7a1; --greenbg:rgba(50,215,161,.12);
  --teal:#41c4ff; --tealbg:rgba(65,196,255,.14); --pink:#f67dff; --r:16px;
  --st-todo:#aab4f7; --st-wip:#8a6dff; --st-rev:#ffca57; --st-done:#32d7a1;
  --pri-hi:#ff708e; --pri-no:#5ea8ff; --pri-lo:#32d7a1; --pc0:#8a6dff; --pc1:#41c4ff; --pc2:#f67dff; --pc3:#32d7a1;
  --pc4:#ffca57; --pc5:#ff708e; --pc6:#9ea8ff; --pc7:#79d3ff; --logo1:#8a6dff; --logo2:#f67dff; --oninv:#09101e;
  --sh1:0 8px 20px rgba(8,10,28,.25); --sh2:0 25px 70px rgba(4,8,20,.46); --sh3:0 12px 32px rgba(14,12,44,.38);
  --ovlc:rgba(5,7,18,.66); color-scheme:dark;
}
:root[data-theme="autumn"]{
  --bg:rgba(251,247,239,.98); --panel:rgba(255,253,248,.97); --panel2:rgba(240,232,212,.95); --panel3:rgba(244,236,220,.96);
  --side:rgba(244,236,220,.98); --line:#DDD5C4; --line2:#C9BFA8; --photowash:none;
  --txt:#2C2620; --mut:#5C5347; --dim:#948A78; --acc:#1F4E37; --acc2:#4E7A4E; --accbg:#EAF2EA;
  --red:#93360F; --redbg:#FDF0E8; --amber:#C9881E; --amberbg:rgba(201,136,30,.14); --green:#4E7A4E; --greenbg:#E8F2E8;
  --teal:#C9881E; --tealbg:rgba(201,136,30,.14); --pink:#BD4B1B; --st-todo:#BD4B1B; --st-wip:#1F4E37; --st-rev:#C9881E; --st-done:#4E7A4E;
  --pri-hi:#93360F; --pri-no:#1F4E37; --pri-lo:#948A78; --pc0:#1F4E37; --pc1:#BD4B1B; --pc2:#C9881E; --pc3:#4E7A4E; --pc4:#93360F; --pc5:#7A5C2E; --pc6:#3E6B54; --pc7:#A0561F;
  --logo1:#1F4E37; --logo2:#4E7A4E; --oninv:#FBF7EF; --sh1:0 6px 18px rgba(44,38,32,.08); --sh2:0 24px 60px rgba(44,38,32,.14); --sh3:0 10px 26px rgba(44,38,32,.10); --ovlc:rgba(44,38,32,.20); color-scheme:light;
}
:root[data-theme="cyber"]{
  --bg:rgba(1,10,12,.9); --panel:rgba(3,18,23,.78); --panel2:rgba(6,26,32,.8); --panel3:rgba(5,33,42,.82);
  --side:rgba(1,12,16,.84); --line:rgba(0,231,255,.14); --line2:rgba(0,231,255,.24); --photowash:linear-gradient(180deg,rgba(0,8,11,.66),rgba(0,8,11,.9));
  --txt:#eaffff; --mut:#89b7c1; --dim:#54818d; --acc:#00f0ff; --acc2:#42ffb3; --accbg:rgba(0,240,255,.12); --red:#ff4e84; --redbg:rgba(255,78,132,.12);
  --amber:#ffe05b; --amberbg:rgba(255,224,91,.12); --green:#42ffb3; --greenbg:rgba(66,255,179,.10); --teal:#00f0ff; --tealbg:rgba(0,240,255,.12); --pink:#cd7dff;
  --st-todo:#8ccdd5; --st-wip:#00f0ff; --st-rev:#ffe05b; --st-done:#42ffb3; --pri-hi:#ff4e84; --pri-no:#00f0ff; --pri-lo:#42ffb3; --pc0:#00f0ff; --pc1:#42ffb3; --pc2:#cd7dff; --pc3:#ffe05b; --pc4:#ff4e84; --pc5:#53d7ff; --pc6:#7efff3; --pc7:#4ef8ca; --logo1:#00f0ff; --logo2:#42ffb3; --oninv:#001114; --sh1:0 8px 22px rgba(0,0,0,.28); --sh2:0 28px 80px rgba(0,240,255,.12); --sh3:0 14px 38px rgba(0,240,255,.14);
}
body{background:transparent;color:var(--txt)}
.bgphoto{display:none!important}
.bgphoto>div{display:none}
.bgwash{backdrop-filter:blur(6px);background:var(--photowash)}
#app{display:grid;grid-template-columns:220px 1fr;height:100vh}
#side{width:220px;display:flex;flex-direction:column;align-items:stretch;padding:16px 14px;border-right:1px solid var(--line);background:linear-gradient(180deg,color-mix(in srgb,var(--side) 96%,transparent), color-mix(in srgb,var(--side) 86%,transparent));box-shadow:var(--sh2);gap:6px;overflow-y:auto;overflow-x:hidden}
#logo{display:flex;align-items:center;gap:12px;padding:4px 10px 16px;margin-bottom:4px}
#logo .mark{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(135deg,var(--logo1),var(--logo2));color:#fff;font-weight:700;font-size:14px;box-shadow:0 12px 22px color-mix(in srgb,var(--logo1) 22%, transparent)}
#logo .mark svg{width:28px;height:28px;display:block}
#logo b{font-size:14px;display:block;line-height:1.1}
#logo span{display:block;font-size:11px;color:var(--mut);margin-top:2px}
.nav{width:100%;padding:10px 12px;border-radius:12px;color:var(--mut);font-size:13px;gap:10px}
.nav:hover{background:var(--panel2);color:var(--txt)}
.nav.on{background:var(--accbg);color:var(--txt);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--acc2) 32%, transparent)}
.nav .k{display:none}
#side .grow{flex:1;min-height:16px}
#sync,#theme,#icloudsync,#icloudcfg,#drivebk,#notifbtn{margin:0;padding:10px 12px;border-radius:12px;background:var(--panel2);border:1px solid var(--line);justify-content:flex-start;color:var(--mut)}
#sync:hover,#theme:hover,#icloudsync:hover,#icloudcfg:hover,#drivebk:hover,#notifbtn:hover{color:var(--txt);border-color:var(--line2);background:var(--panel3)}
#main{min-width:0;display:flex;flex-direction:column}
#top{height:70px;min-height:70px;padding:0 22px;background:color-mix(in srgb,var(--panel) 94%, transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);gap:14px}
#vtitle{font-size:18px;font-weight:700}
#search{width:340px;max-width:44vw;background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:11px 16px}
#topactions{display:flex;align-items:center;gap:10px}
.topicon{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;background:var(--panel2);border:1px solid var(--line);color:var(--mut)}
.topicon:hover{color:var(--txt);border-color:var(--line2)}
#profilepill{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;background:linear-gradient(135deg,var(--logo1),var(--logo2));color:#fff;font-weight:700;box-shadow:var(--sh1)}
#newbtn{border-radius:999px;padding:10px 16px}
#view{padding:18px 20px 22px;background:transparent}
.panel,.card,.kpi,.col,.task,#top,#modal{backdrop-filter:blur(8px)}
.ydash{display:flex;flex-direction:column;gap:16px}
.hero-banner{position:relative;min-height:198px;border:1px solid var(--line);border-radius:24px;overflow:hidden;background-size:cover;background-position:center;box-shadow:var(--sh2);padding:26px 26px;display:flex;align-items:flex-end;justify-content:space-between}
.hero-banner::before{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(6,8,18,.58),rgba(6,8,18,.2) 45%,rgba(6,8,18,.62))}
:root[data-theme="autumn"] .hero-banner::before{background:linear-gradient(135deg,rgba(255,253,248,.18),rgba(255,253,248,.06) 45%,rgba(31,78,55,.08))}
.hero-banner>*{position:relative;z-index:1}
.hero-copy h1{font-size:22px;line-height:1.15;margin-bottom:8px}
.hero-copy p{font-size:12px;color:var(--mut);margin-bottom:12px;max-width:580px}
.hero-line{width:72px;height:3px;border-radius:999px;background:linear-gradient(90deg,var(--acc2),transparent)}
.hero-date{width:190px;min-height:148px;border-radius:18px;padding:18px 16px;background:color-mix(in srgb,var(--panel) 82%, transparent);backdrop-filter:blur(10px);border:1px solid var(--line);box-shadow:var(--sh1)}
.hero-date .d1{font-size:15px;font-weight:700;margin-bottom:4px}.hero-date .d2{font-size:13px;margin-bottom:12px}
.hero-date .q{font-size:12px;color:var(--mut);line-height:1.5}
.kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.show-kpi{padding:16px 18px;border-radius:18px;border:1px solid var(--line);background:color-mix(in srgb,var(--panel) 92%, transparent);box-shadow:var(--sh1);display:flex;align-items:center;gap:14px}
.show-kpi .ico{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;font-size:18px;background:color-mix(in srgb,var(--panel3) 88%, transparent)}
.show-kpi b{display:block;font-size:16px;line-height:1.15}.show-kpi span{font-size:12px;color:var(--mut)}
.dashboard-grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(280px,.9fr);gap:16px}
.dashboard-stack{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.show-card{border:1px solid var(--line);border-radius:22px;background:color-mix(in srgb,var(--panel) 93%, transparent);box-shadow:var(--sh1);padding:16px 16px 14px}
.show-card h3{font-size:13px;font-weight:700;margin-bottom:12px}
.show-card .head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.filter-pills{display:flex;gap:8px;flex-wrap:wrap}
.filter-pills button,.theme-mini{border:none;cursor:pointer;padding:6px 12px;border-radius:999px;background:var(--panel2);color:var(--mut);font:inherit}
.filter-pills button.on,.theme-mini.on{background:var(--accbg);color:var(--txt)}
.taskline,.meetline,.noteline{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:start;padding:10px 0;border-top:1px solid color-mix(in srgb,var(--line) 70%, transparent)}
.taskline:first-of-type,.meetline:first-of-type,.noteline:first-of-type{border-top:none;padding-top:0}
.ck{width:16px;height:16px;border-radius:6px;border:1px solid var(--line2);margin-top:2px;background:var(--panel2)}
.taskline .meta,.meetline .meta,.noteline .meta{font-size:11px;color:var(--mut);margin-top:2px}
.priority-badge,.soft-badge{font-size:11px;padding:4px 10px;border-radius:999px;border:1px solid transparent;white-space:nowrap}
.right-rail{display:grid;gap:16px}
.quote-card{min-height:170px;background-size:cover;background-position:center;position:relative;overflow:hidden}
.quote-card::before{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.16),rgba(0,0,0,.58))}
:root[data-theme="autumn"] .quote-card::before{background:linear-gradient(180deg,rgba(255,253,248,.04),rgba(44,38,32,.10))}
.quote-card>*{position:relative;z-index:1}
.quote-card .quote{font-size:13px;line-height:1.45;margin-top:46px;max-width:75%}
.progress-bars{display:grid;gap:12px}
.progress-row .label{display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px}
.theme-strip{display:grid;grid-template-columns:1.3fr 1.1fr .8fr .8fr;gap:12px}
.tstrip-card{border:1px solid var(--line);border-radius:18px;background:color-mix(in srgb,var(--panel) 94%, transparent);box-shadow:var(--sh1);padding:14px}
.tstrip-card h4{font-size:12px;margin-bottom:10px}
.theme-previews{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.theme-preview{cursor:pointer;border-radius:14px;overflow:hidden;border:2px solid transparent;background:var(--panel2)}
.theme-preview.on{border-color:color-mix(in srgb,var(--acc2) 72%, transparent);box-shadow:0 0 0 1px color-mix(in srgb,var(--acc2) 22%, transparent)}
.theme-preview img{display:block;width:100%;height:86px;object-fit:cover}.theme-preview .lab{padding:8px 8px 10px;font-size:12px}
.swatches{display:flex;gap:10px;flex-wrap:wrap}.swatch{width:22px;height:22px;border-radius:50%;border:2px solid rgba(255,255,255,.28)}
.shortcut-list{display:grid;gap:8px}.shortcut{display:flex;align-items:center;gap:10px;font-size:12px}.shortcut kbd{min-width:22px;padding:4px 7px;border-radius:7px;background:var(--panel2);border:1px solid var(--line);font:inherit;text-align:center}
.zenbtn{display:inline-flex;align-items:center;justify-content:center;padding:12px 16px;border-radius:999px;background:linear-gradient(135deg,var(--logo1),var(--logo2));color:white;font-weight:700;width:100%;margin-top:8px}
.demo-note{font-size:11px;color:var(--mut)}
@media (max-width:1180px){
  .kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .dashboard-grid{grid-template-columns:1fr}
  .dashboard-stack{grid-template-columns:1fr}
  .theme-strip{grid-template-columns:1fr 1fr}
}
@media (max-width:900px){
  #app{display:flex}
  #side{width:100%;height:auto;flex-direction:row;align-items:center;overflow-x:auto;overflow-y:hidden;border-right:none;border-bottom:1px solid var(--line)}
  #logo{padding:4px 12px 4px 2px;margin-bottom:0}.nav{width:auto;white-space:nowrap}
  #side .grow{display:none}
  #sync span,#theme span,#icloudsync span,#drivebk span,#notifbtn span{display:none}
  #top{padding:0 14px;gap:10px}#search{width:100%;max-width:none}
  .hero-banner{padding:18px}.hero-date{display:none}
  .theme-strip{grid-template-columns:1fr}
}


html,body{width:100%;height:100%;overflow:hidden;background:var(--app-bg)!important;scroll-behavior:smooth}
:root{
  --app-bg:#0b1028;
  --hero-bg:linear-gradient(135deg,#22285d 0%,#171d49 42%,#251a4e 100%);
  --hero-glow:radial-gradient(circle at 78% 22%,rgba(151,111,255,.35),transparent 34%),radial-gradient(circle at 28% 72%,rgba(66,196,255,.18),transparent 40%);
}
:root[data-theme="autumn"]{
  --app-bg:#FBF7EF;
  --hero-bg:linear-gradient(135deg,#F7EEDB 0%,#EFE2C6 48%,#E6EEDC 100%);
  --hero-glow:radial-gradient(circle at 78% 22%,rgba(201,136,30,.16),transparent 32%),radial-gradient(circle at 26% 74%,rgba(31,78,55,.13),transparent 38%);
}
:root[data-theme="cyber"]{
  --app-bg:#020b0e;
  --hero-bg:linear-gradient(135deg,#03151b 0%,#061f29 48%,#021318 100%);
  --hero-glow:radial-gradient(circle at 78% 22%,rgba(0,240,255,.22),transparent 32%),radial-gradient(circle at 24% 74%,rgba(66,255,179,.16),transparent 40%);
}
.bgphoto,.bgwash{display:none!important;background:none!important}
#app{height:100dvh;min-height:0;background:var(--app-bg);overflow:hidden}
#side{height:100dvh;min-height:0;position:sticky;top:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;scrollbar-gutter:stable;box-shadow:none!important;backdrop-filter:none!important;-webkit-backdrop-filter:none!important}
#main{height:100dvh;min-height:0;overflow:hidden}
#top{position:sticky;top:0;z-index:30;flex:0 0 70px;backdrop-filter:none!important;-webkit-backdrop-filter:none!important;box-shadow:0 1px 0 var(--line)}
#view{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;scrollbar-gutter:stable both-edges;padding:20px clamp(16px,2vw,28px) 34px;background:var(--app-bg)!important;scroll-behavior:smooth}
#view>*{max-width:1680px;margin-left:auto;margin-right:auto}
.panel,.card,.kpi,.col,.task,#modal,.show-card,.show-kpi,.tstrip-card,.hero-date{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}
.card,.panel,.kpi,.col,.task,.show-card,.show-kpi,.tstrip-card,.hero-banner,.nav,.btn,input,select,textarea{
  transition:background-color .22s cubic-bezier(.2,.7,.2,1),border-color .22s cubic-bezier(.2,.7,.2,1),color .18s ease,box-shadow .22s ease,transform .18s cubic-bezier(.2,.7,.2,1);
}
.card:hover,.show-card:hover,.show-kpi:hover,.tstrip-card:hover{transform:translateY(-2px)}
#view.viewenter{animation:viewInSmooth .34s cubic-bezier(.2,.75,.2,1)}
@keyframes viewInSmooth{from{opacity:0;transform:translate3d(0,10px,0)}to{opacity:1;transform:translate3d(0,0,0)}}
.ydash{gap:18px;min-width:0}
.hero-banner{min-height:210px;background-image:var(--hero-glow),var(--hero-bg)!important;background-size:cover!important;background-position:center!important;box-shadow:var(--sh1)!important;isolation:isolate}
.hero-banner::before{background:linear-gradient(135deg,rgba(255,255,255,.035),transparent 42%,rgba(0,0,0,.10))!important}
:root[data-theme="autumn"] .hero-banner::before{background:linear-gradient(135deg,rgba(255,253,248,.48),transparent 48%,rgba(31,78,55,.06))!important}
.quote-card{background-image:var(--hero-glow),var(--hero-bg)!important;min-height:150px;box-shadow:var(--sh1)!important}
.quote-card::before{background:linear-gradient(180deg,transparent,rgba(0,0,0,.10))!important}
:root[data-theme="autumn"] .quote-card::before{background:linear-gradient(180deg,rgba(255,253,248,.16),rgba(44,38,32,.04))!important}
.kpi-grid{grid-template-columns:repeat(4,minmax(170px,1fr));gap:14px}
.dashboard-grid{align-items:start;grid-template-columns:minmax(0,1.55fr) minmax(300px,.85fr)}
.dashboard-stack{align-items:start;grid-template-columns:repeat(2,minmax(0,1fr))}
.right-rail{align-self:start}
.show-card,.tstrip-card{min-width:0;overflow:hidden}
.taskline,.meetline,.noteline{min-width:0}
.taskline>div:nth-child(2),.meetline>div:nth-child(2),.noteline>div:nth-child(2){min-width:0}
.taskline>div:nth-child(2)>div:first-child,.meetline>div:nth-child(2)>div:first-child,.noteline>div:nth-child(2)>div:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.theme-strip{grid-template-columns:minmax(0,1.6fr) minmax(220px,.8fr) minmax(220px,.8fr);align-items:start}
.theme-previews{grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.theme-preview{position:relative;min-width:0;height:138px;border-radius:16px;overflow:hidden;background:var(--panel2);border:1px solid var(--line);box-shadow:none}
.theme-preview .theme-art{height:92px;position:relative;overflow:hidden;background:var(--preview-bg)}
.theme-preview .theme-art::before{content:'';position:absolute;left:10px;top:10px;width:25%;bottom:10px;border-radius:8px;background:var(--preview-side)}
.theme-preview .theme-art::after{content:'';position:absolute;left:39%;right:10px;top:14px;height:18px;border-radius:8px;background:var(--preview-card);box-shadow:0 28px 0 var(--preview-card),0 56px 0 var(--preview-card)}
.theme-preview[data-theme-preview="aurora"]{--preview-bg:linear-gradient(135deg,#181d4d,#251852);--preview-side:rgba(13,17,42,.92);--preview-card:rgba(128,101,255,.28)}
.theme-preview[data-theme-preview="autumn"]{--preview-bg:linear-gradient(135deg,#FBF7EF,#EFE2C6);--preview-side:#F4ECDC;--preview-card:#DCE7D8}
.theme-preview[data-theme-preview="cyber"]{--preview-bg:linear-gradient(135deg,#03141a,#04252f);--preview-side:rgba(1,12,16,.96);--preview-card:rgba(0,240,255,.18)}
:root[data-theme="ivory"]{
  --bg:#F7F5F0; --panel:#FFFFFF; --panel2:#F1EEE7; --panel3:#F6F3ED; --side:#F3F0E9; --line:#E4DED2; --line2:#D3CBBB; --photowash:none;
  --txt:#1C1B19; --mut:#5D5A52; --dim:#9A958A; --acc:#8C6A2F; --acc2:#B08A45; --accbg:#F4ECDD;
  --red:#A33A2B; --redbg:rgba(163,58,43,.14); --amber:#B7791F; --amberbg:rgba(183,121,31,.14); --green:#4F7A52; --greenbg:rgba(79,122,82,.14);
  --teal:#2F6F7A; --tealbg:rgba(47,111,122,.14); --pink:#A34D6B;
  --st-todo:#A34D6B; --st-wip:#8C6A2F; --st-rev:#B7791F; --st-done:#4F7A52; --pri-hi:#A33A2B; --pri-no:#8C6A2F; --pri-lo:#9A958A; --pc0:#8C6A2F;--pc1:#2F6F7A;--pc2:#A34D6B;--pc3:#4F7A52;--pc4:#B7791F;--pc5:#6B5B95;--pc6:#3E6B8A;--pc7:#A0561F;
  --logo1:#1C1B19; --logo2:#8C6A2F; --oninv:#F7F5F0; --sh1:0 6px 18px rgba(30,30,40,.07); --sh2:0 24px 60px rgba(30,30,40,.13); --sh3:0 10px 26px rgba(30,30,40,.10); --ovlc:rgba(30,30,40,.22);
  --app-bg:#F7F5F0; --hero-bg:linear-gradient(135deg,#FFFFFF 0%,#F4EFE4 55%,#EDE6D6 100%); --hero-glow:radial-gradient(circle at 78% 22%,rgba(176,138,69,.16),transparent 34%),radial-gradient(circle at 26% 74%,rgba(47,111,122,.08),transparent 40%); color-scheme:light;
}
.theme-preview[data-theme-preview="ivory"]{--preview-bg:linear-gradient(135deg,#FFFFFF,#EDE6D6);--preview-side:#F3F0E9;--preview-card:#E9DCC2}
:root[data-theme="ivory"] .hero-banner::before{background:linear-gradient(135deg,rgba(255,255,255,.5),transparent 48%,rgba(0,0,0,.03))!important}:root[data-theme="ivory"] .quote-card::before{background:linear-gradient(180deg,rgba(255,255,255,.18),rgba(0,0,0,.03))!important}
:root[data-theme="midnight"]{
  --bg:#0A0A0D; --panel:#131318; --panel2:#1A1A21; --panel3:#202029; --side:#0E0E12; --line:rgba(255,255,255,.07); --line2:rgba(255,255,255,.13); --photowash:none;
  --txt:#EDEAE3; --mut:#A8A397; --dim:#6E6A62; --acc:#D6B57E; --acc2:#E9CD98; --accbg:rgba(214,181,126,.13);
  --red:#F08A80; --redbg:rgba(240,138,128,.14); --amber:#E7B75F; --amberbg:rgba(231,183,95,.14); --green:#8BC79A; --greenbg:rgba(139,199,154,.14);
  --teal:#7FB8C8; --tealbg:rgba(127,184,200,.14); --pink:#E59AB8;
  --st-todo:#E59AB8; --st-wip:#D6B57E; --st-rev:#E7B75F; --st-done:#8BC79A; --pri-hi:#F08A80; --pri-no:#D6B57E; --pri-lo:#6E6A62; --pc0:#D6B57E;--pc1:#7FB8C8;--pc2:#E59AB8;--pc3:#8BC79A;--pc4:#E7B75F;--pc5:#F08A80;--pc6:#B4A6E8;--pc7:#9CC7E8;
  --logo1:#2A2A33; --logo2:#D6B57E; --oninv:#0A0A0D; --sh1:0 8px 20px rgba(0,0,0,.30); --sh2:0 25px 70px rgba(0,0,0,.50); --sh3:0 12px 32px rgba(0,0,0,.40); --ovlc:rgba(0,0,0,.62);
  --app-bg:#0A0A0D; --hero-bg:linear-gradient(135deg,#18181E 0%,#101014 55%,#1B1712 100%); --hero-glow:radial-gradient(circle at 78% 22%,rgba(214,181,126,.16),transparent 34%),radial-gradient(circle at 24% 76%,rgba(127,184,200,.08),transparent 40%); color-scheme:dark;
}
.theme-preview[data-theme-preview="midnight"]{--preview-bg:linear-gradient(135deg,#18181E,#0A0A0D);--preview-side:#0E0E12;--preview-card:rgba(214,181,126,.30)}
:root[data-theme="ocean"]{
  --bg:#06111D; --panel:#0B1A2B; --panel2:#10233A; --panel3:#142B46; --side:#071425; --line:rgba(125,211,252,.10); --line2:rgba(125,211,252,.19); --photowash:none;
  --txt:#E6F1FA; --mut:#93AEC6; --dim:#5E7891; --acc:#38BDF8; --acc2:#5EEAD4; --accbg:rgba(56,189,248,.14);
  --red:#FB7185; --redbg:rgba(251,113,133,.14); --amber:#FBBF24; --amberbg:rgba(251,191,36,.14); --green:#34D399; --greenbg:rgba(52,211,153,.14);
  --teal:#5EEAD4; --tealbg:rgba(94,234,212,.14); --pink:#C084FC;
  --st-todo:#C084FC; --st-wip:#38BDF8; --st-rev:#FBBF24; --st-done:#34D399; --pri-hi:#FB7185; --pri-no:#38BDF8; --pri-lo:#5E7891; --pc0:#38BDF8;--pc1:#5EEAD4;--pc2:#C084FC;--pc3:#34D399;--pc4:#FBBF24;--pc5:#FB7185;--pc6:#818CF8;--pc7:#7DD3FC;
  --logo1:#0EA5E9; --logo2:#14B8A6; --oninv:#06111D; --sh1:0 8px 20px rgba(0,0,0,.30); --sh2:0 25px 70px rgba(0,0,0,.50); --sh3:0 12px 32px rgba(0,0,0,.40); --ovlc:rgba(0,0,0,.62);
  --app-bg:#06111D; --hero-bg:linear-gradient(135deg,#0B2540 0%,#082033 50%,#0A2E3A 100%); --hero-glow:radial-gradient(circle at 78% 22%,rgba(56,189,248,.24),transparent 34%),radial-gradient(circle at 24% 76%,rgba(94,234,212,.13),transparent 40%); color-scheme:dark;
}
.theme-preview[data-theme-preview="ocean"]{--preview-bg:linear-gradient(135deg,#0B2540,#06111D);--preview-side:#071425;--preview-card:rgba(56,189,248,.30)}
:root[data-theme="sakura"]{
  --bg:#FFF8F9; --panel:#FFFFFF; --panel2:#FBECEF; --panel3:#FDF3F5; --side:#FCF1F3; --line:#F0DDE2; --line2:#E4C6CF; --photowash:none;
  --txt:#2B1E23; --mut:#6E5560; --dim:#A68E98; --acc:#B03A66; --acc2:#D46A8F; --accbg:#FBE6EE;
  --red:#B4232C; --redbg:rgba(180,35,44,.14); --amber:#C0841A; --amberbg:rgba(192,132,26,.14); --green:#4C8A62; --greenbg:rgba(76,138,98,.14);
  --teal:#5C7FB0; --tealbg:rgba(92,127,176,.14); --pink:#B03A66;
  --st-todo:#B03A66; --st-wip:#B03A66; --st-rev:#C0841A; --st-done:#4C8A62; --pri-hi:#B4232C; --pri-no:#B03A66; --pri-lo:#A68E98; --pc0:#B03A66;--pc1:#5C7FB0;--pc2:#C0841A;--pc3:#4C8A62;--pc4:#8E5BA6;--pc5:#D46A8F;--pc6:#2F8F8A;--pc7:#A0561F;
  --logo1:#B03A66; --logo2:#E89AB4; --oninv:#FFF8F9; --sh1:0 6px 18px rgba(30,30,40,.07); --sh2:0 24px 60px rgba(30,30,40,.13); --sh3:0 10px 26px rgba(30,30,40,.10); --ovlc:rgba(30,30,40,.22);
  --app-bg:#FFF8F9; --hero-bg:linear-gradient(135deg,#FFF1F4 0%,#FDE4EC 50%,#F6E9F4 100%); --hero-glow:radial-gradient(circle at 78% 22%,rgba(212,106,143,.18),transparent 34%),radial-gradient(circle at 26% 74%,rgba(92,127,176,.08),transparent 40%); color-scheme:light;
}
.theme-preview[data-theme-preview="sakura"]{--preview-bg:linear-gradient(135deg,#FFF1F4,#F6E9F4);--preview-side:#FCF1F3;--preview-card:#F6CFDC}
:root[data-theme="sakura"] .hero-banner::before{background:linear-gradient(135deg,rgba(255,255,255,.5),transparent 48%,rgba(0,0,0,.03))!important}:root[data-theme="sakura"] .quote-card::before{background:linear-gradient(180deg,rgba(255,255,255,.18),rgba(0,0,0,.03))!important}
:root[data-theme="nord"]{
  --bg:#EEF1F6; --panel:#FFFFFF; --panel2:#E6EBF2; --panel3:#EDF1F7; --side:#E8EDF4; --line:#D6DEE9; --line2:#C3CEDD; --photowash:none;
  --txt:#1E2633; --mut:#4C566A; --dim:#8792A6; --acc:#3B5BA9; --acc2:#5E81AC; --accbg:#E3EAF6;
  --red:#BF616A; --redbg:rgba(191,97,106,.14); --amber:#C68A2B; --amberbg:rgba(198,138,43,.14); --green:#5E8C61; --greenbg:rgba(94,140,97,.14);
  --teal:#3C8D93; --tealbg:rgba(60,141,147,.14); --pink:#B48EAD;
  --st-todo:#B48EAD; --st-wip:#3B5BA9; --st-rev:#C68A2B; --st-done:#5E8C61; --pri-hi:#BF616A; --pri-no:#3B5BA9; --pri-lo:#8792A6; --pc0:#3B5BA9;--pc1:#3C8D93;--pc2:#B48EAD;--pc3:#5E8C61;--pc4:#C68A2B;--pc5:#BF616A;--pc6:#5E81AC;--pc7:#88C0D0;
  --logo1:#2E3440; --logo2:#5E81AC; --oninv:#EEF1F6; --sh1:0 6px 18px rgba(30,30,40,.07); --sh2:0 24px 60px rgba(30,30,40,.13); --sh3:0 10px 26px rgba(30,30,40,.10); --ovlc:rgba(30,30,40,.22);
  --app-bg:#EEF1F6; --hero-bg:linear-gradient(135deg,#F5F8FC 0%,#E4EBF5 55%,#DCE6F2 100%); --hero-glow:radial-gradient(circle at 78% 22%,rgba(94,129,172,.18),transparent 34%),radial-gradient(circle at 24% 76%,rgba(136,192,208,.14),transparent 40%); color-scheme:light;
}
.theme-preview[data-theme-preview="nord"]{--preview-bg:linear-gradient(135deg,#F5F8FC,#DCE6F2);--preview-side:#E8EDF4;--preview-card:#C9D7EA}
:root[data-theme="nord"] .hero-banner::before{background:linear-gradient(135deg,rgba(255,255,255,.5),transparent 48%,rgba(0,0,0,.03))!important}:root[data-theme="nord"] .quote-card::before{background:linear-gradient(180deg,rgba(255,255,255,.18),rgba(0,0,0,.03))!important}
:root[data-theme="forest"]{
  --bg:#0B1410; --panel:#111D17; --panel2:#17271F; --panel3:#1C3026; --side:#0D1813; --line:rgba(167,211,160,.10); --line2:rgba(167,211,160,.19); --photowash:none;
  --txt:#E7F0E8; --mut:#A3B8A7; --dim:#6A8270; --acc:#7FB685; --acc2:#B5D99C; --accbg:rgba(127,182,133,.15);
  --red:#E58C7A; --redbg:rgba(229,140,122,.14); --amber:#E2C275; --amberbg:rgba(226,194,117,.14); --green:#7FB685; --greenbg:rgba(127,182,133,.14);
  --teal:#8CC7B5; --tealbg:rgba(140,199,181,.14); --pink:#D8A0C0;
  --st-todo:#D8A0C0; --st-wip:#7FB685; --st-rev:#E2C275; --st-done:#7FB685; --pri-hi:#E58C7A; --pri-no:#7FB685; --pri-lo:#6A8270; --pc0:#7FB685;--pc1:#8CC7B5;--pc2:#D8A0C0;--pc3:#E2C275;--pc4:#E58C7A;--pc5:#B5D99C;--pc6:#9FB7E0;--pc7:#C9A27E;
  --logo1:#2F5D3A; --logo2:#7FB685; --oninv:#0B1410; --sh1:0 8px 20px rgba(0,0,0,.30); --sh2:0 25px 70px rgba(0,0,0,.50); --sh3:0 12px 32px rgba(0,0,0,.40); --ovlc:rgba(0,0,0,.62);
  --app-bg:#0B1410; --hero-bg:linear-gradient(135deg,#15281D 0%,#0F1F17 55%,#1C2A1A 100%); --hero-glow:radial-gradient(circle at 78% 22%,rgba(181,217,156,.16),transparent 34%),radial-gradient(circle at 24% 76%,rgba(140,199,181,.10),transparent 40%); color-scheme:dark;
}
.theme-preview[data-theme-preview="forest"]{--preview-bg:linear-gradient(135deg,#15281D,#0B1410);--preview-side:#0D1813;--preview-card:rgba(127,182,133,.32)}
body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
.show-kpi b,.kpi b{font-variant-numeric:tabular-nums}
.theme-previews{grid-template-columns:repeat(auto-fill,minmax(118px,1fr))!important;gap:10px!important}
.theme-preview .lab{height:auto!important;padding:8px 10px 9px!important}.theme-preview .lab .demo-note{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.theme-preview{height:auto!important;transition:transform .25s cubic-bezier(.2,.75,.2,1),box-shadow .25s ease,border-color .2s ease}
.theme-preview .theme-art{height:74px!important}
.theme-preview:hover{transform:translateY(-3px);box-shadow:var(--sh3)}
::view-transition-old(root),::view-transition-new(root){animation-duration:.5s;animation-timing-function:cubic-bezier(.2,.75,.2,1)}
@keyframes vaRise{from{opacity:0;transform:translate3d(0,14px,0) scale(.985)}to{opacity:1;transform:none}}
@keyframes vaGrow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes vaDrift{0%{transform:translate3d(-6%,-4%,0) scale(1)}100%{transform:translate3d(6%,5%,0) scale(1.08)}}
@keyframes vaToast{from{opacity:0;transform:translateX(-50%) translateY(14px) scale(.97)}to{opacity:1;transform:translateX(-50%) translateY(0) scale(1)}}
@keyframes vaShine{from{background-position:-200% 0}to{background-position:200% 0}}
@media (prefers-reduced-motion:no-preference){
  #view.va-stagger :is(.kpi-grid,.dashboard-stack,.right-rail,.theme-strip,.ss-col,#rdList,.cols,.grid,.sk-grid)>*{animation:vaRise .5s cubic-bezier(.2,.75,.2,1) both}
  #view.va-stagger :is(.kpi-grid,.dashboard-stack,.right-rail,.theme-strip,.ss-col,#rdList,.cols,.grid,.sk-grid)>*:nth-child(2){animation-delay:.04s}
  #view.va-stagger :is(.kpi-grid,.dashboard-stack,.right-rail,.theme-strip,.ss-col,#rdList,.cols,.grid,.sk-grid)>*:nth-child(3){animation-delay:.08s}
  #view.va-stagger :is(.kpi-grid,.dashboard-stack,.right-rail,.theme-strip,.ss-col,#rdList,.cols,.grid,.sk-grid)>*:nth-child(4){animation-delay:.12s}
  #view.va-stagger :is(.kpi-grid,.dashboard-stack,.right-rail,.theme-strip,.ss-col,#rdList,.cols,.grid,.sk-grid)>*:nth-child(5){animation-delay:.16s}
  #view.va-stagger :is(.kpi-grid,.dashboard-stack,.right-rail,.theme-strip,.ss-col,#rdList,.cols,.grid,.sk-grid)>*:nth-child(6){animation-delay:.2s}
  #view.va-stagger :is(.kpi-grid,.dashboard-stack,.right-rail,.theme-strip,.ss-col,#rdList,.cols,.grid,.sk-grid)>*:nth-child(n+7){animation-delay:.24s}
  #view.va-stagger .pbar i{transform-origin:left center;animation:vaGrow 1.1s cubic-bezier(.2,.75,.2,1) both}
  .hero-banner::after{content:'';position:absolute;inset:-35%;z-index:0;pointer-events:none;
    background:radial-gradient(circle at 35% 40%,color-mix(in srgb,var(--acc2) 16%,transparent),transparent 38%);animation:vaDrift 16s ease-in-out infinite alternate}
  .btn:active,.nav:active,.topicon:active,.ss-tab:active,.theme-mini:active,.filter-pills button:active{transform:scale(.97)}
  .nav{transition:background-color .2s ease,color .2s ease,transform .15s ease}
  .nav:hover{transform:translateX(2px)}
  #toast.show{animation:vaToast .32s cubic-bezier(.2,.75,.2,1)}
  .ss-card,.rd-card{transition:transform .2s cubic-bezier(.2,.75,.2,1),border-color .15s,box-shadow .2s}
  .ss-card:hover{transform:translateX(3px)}
}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}

.theme-preview .lab{height:46px;display:flex;flex-direction:column;justify-content:center;padding:7px 10px}
#board{overflow-x:auto;overflow-y:visible;padding-bottom:8px;scroll-snap-type:x proximity;overscroll-behavior-inline:contain}
.col{scroll-snap-align:start}
@media (max-width:1280px){
  .kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .dashboard-grid{grid-template-columns:1fr}
  .theme-strip{grid-template-columns:1fr 1fr}
  .theme-strip>.tstrip-card:first-child{grid-column:1/-1!important}
}
@media (max-width:980px){
  #app{display:flex;flex-direction:column;height:100dvh}
  #side{width:100%;height:auto;max-height:none;min-height:58px;flex:0 0 auto;position:relative;top:auto;flex-direction:row;align-items:center;overflow-x:auto;overflow-y:hidden;padding:8px 10px;scrollbar-width:none;box-shadow:0 1px 0 var(--line)!important}
  #side::-webkit-scrollbar{display:none}
  #logo{margin:0;padding:0 12px 0 2px;position:sticky;left:0;z-index:3;background:var(--side)}
  #logo span{display:none}.nav{width:auto;min-width:max-content;padding:9px 11px}.nav svg{width:15px;height:15px}
  #side .grow{display:none}
  #sync,#theme,#icloudsync,#icloudcfg,#drivebk,#notifbtn{min-width:42px;width:auto;padding:9px 10px;justify-content:center}
  #sync span,#theme span,#icloudsync span,#drivebk span,#notifbtn span{display:none!important}
  #main{height:auto;flex:1;min-height:0}
  #top{height:62px;min-height:62px;flex-basis:62px;padding:0 12px}
  #vtitle{display:none}#search{flex:1;width:auto;max-width:none;min-width:0}.topicon{display:none}#langbtn{display:grid!important}#profilepill{width:34px;height:34px}#newbtn{padding:9px 12px}
  #view{padding:14px 12px 26px}
  .hero-banner{min-height:180px;padding:20px}.hero-date{display:none}
  .dashboard-stack{grid-template-columns:1fr}
}
@media (max-width:680px){
  .kpi-grid{grid-template-columns:1fr 1fr;gap:10px}.show-kpi{padding:13px}.show-kpi .ico{width:36px;height:36px}.show-kpi b{font-size:15px}
  .theme-strip{grid-template-columns:1fr}.theme-strip>.tstrip-card:first-child{grid-column:auto!important}
  .theme-previews{grid-template-columns:1fr}.theme-preview{height:126px}.theme-preview .theme-art{height:80px}
  .hero-banner{border-radius:18px}.show-card,.tstrip-card{border-radius:16px}
}
@media (prefers-reduced-motion:reduce){
  html,#view{scroll-behavior:auto}.card,.panel,.kpi,.col,.task,.show-card,.show-kpi,.tstrip-card,.hero-banner,.nav,.btn,input,select,textarea{transition:none!important}#view.viewenter{animation:none!important}
}


.nav-group{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);padding:14px 12px 5px;font-weight:700}
#focusbtn,#healthbtn{margin:0;padding:10px 12px;border-radius:12px;background:var(--panel2);border:1px solid var(--line);color:var(--mut);display:flex;align-items:center;gap:9px;justify-content:flex-start;font-size:12.5px}
#focusbtn:hover,#healthbtn:hover{color:var(--txt);background:var(--panel3);border-color:var(--line2)}
.analytics-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:16px}
.metric-card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:16px;min-width:0}
.metric-card .big{font-size:25px;font-weight:760;letter-spacing:-.03em}.metric-card .sub{font-size:11px;color:var(--mut);margin-top:4px}
.analytics-layout{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(280px,.75fr);gap:16px;align-items:start}
.chart-card{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:18px;overflow:hidden;min-width:0}
.chart-head{display:flex;align-items:center;gap:12px;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap}.chart-head h3{font-size:14px}
.chart-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:var(--mut)}.legend-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:5px}
.month-chart{width:100%;height:auto;min-height:260px;display:block;overflow:visible}.month-chart text{font-family:inherit}
.insight-list{display:grid;gap:10px}.insight{padding:12px;border-radius:14px;background:var(--panel2);border:1px solid var(--line)}.insight b{display:block;font-size:12px}.insight span{display:block;color:var(--mut);font-size:11px;margin-top:3px;line-height:1.45}
.matrix{display:grid;grid-template-columns:1fr 1fr;gap:14px}.matrix-q{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:16px;min-height:260px}.matrix-q h3{font-size:13px;margin-bottom:4px}.matrix-q .qhelp{font-size:11px;color:var(--mut);margin-bottom:12px}.matrix-task{padding:10px;border-radius:12px;background:var(--panel2);border:1px solid var(--line);margin-bottom:8px;cursor:pointer;transition:transform .15s ease,border-color .15s ease}.matrix-task:hover{transform:translateY(-1px);border-color:var(--line2)}
.focus-overlay{position:fixed;inset:0;z-index:1500;background:color-mix(in srgb,var(--app-bg) 96%, transparent);display:none;align-items:center;justify-content:center;padding:24px}.focus-overlay.show{display:flex}.focus-panel{width:min(680px,96vw);border-radius:28px;background:var(--panel);border:1px solid var(--line);box-shadow:var(--sh2);padding:28px;text-align:center}.focus-ring{width:210px;height:210px;margin:22px auto;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--acc) var(--focus-pct,0%),var(--panel2) 0);position:relative}.focus-ring::after{content:'';position:absolute;inset:12px;border-radius:50%;background:var(--panel)}.focus-time{font-size:44px;font-weight:780;letter-spacing:-.04em;position:relative;z-index:2}.focus-task{font-size:15px;font-weight:650;margin-top:5px}.focus-sub{font-size:12px;color:var(--mut);margin-top:4px}.focus-actions{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:18px}
.cmd-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.cmd-action{padding:13px 14px;border-radius:14px;background:var(--panel2);border:1px solid var(--line);text-align:left;cursor:pointer}.cmd-action:hover{border-color:var(--line2);background:var(--panel3)}.cmd-action b{display:block;font-size:12px}.cmd-action span{display:block;font-size:10.5px;color:var(--mut);margin-top:3px}
.health-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}.health-cell{padding:12px;border-radius:13px;background:var(--panel2);border:1px solid var(--line)}.health-cell b{font-size:18px;display:block}.health-cell span{font-size:10.5px;color:var(--mut)}
.planner-bar{height:10px;border-radius:999px;background:var(--panel3);overflow:hidden}.planner-bar i{height:100%;display:block;border-radius:inherit;background:linear-gradient(90deg,var(--acc),var(--acc2));transition:width .25s ease}
body.zen-shell #side,body.zen-shell #top{display:none!important}body.zen-shell #app{grid-template-columns:1fr!important}body.zen-shell #main{height:100dvh!important}body.zen-shell #view{padding-top:28px!important}
@media(max-width:1100px){.analytics-grid{grid-template-columns:repeat(2,1fr)}.analytics-layout{grid-template-columns:1fr}.matrix{grid-template-columns:1fr}}
@media(max-width:680px){.analytics-grid{grid-template-columns:1fr 1fr}.health-grid{grid-template-columns:1fr 1fr}.cmd-grid{grid-template-columns:1fr}.focus-ring{width:175px;height:175px}.focus-time{font-size:36px}}


.nav .v6new{margin-left:auto;font-size:9px;font-weight:800;letter-spacing:.04em;padding:2px 6px;border-radius:6px;background:var(--accbg);color:var(--acc2)}
#palovl{position:fixed;inset:0;z-index:300;display:none;align-items:flex-start;justify-content:center;padding-top:11vh;background:var(--ovlc);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
#palovl.show{display:flex}
.pal-box{width:660px;max-width:94vw;background:var(--panel);border:1px solid var(--line2);border-radius:18px;box-shadow:var(--sh2);overflow:hidden;animation:v6pop .14s ease-out}
@keyframes v6pop{from{transform:translateY(-6px) scale(.985);opacity:0}to{transform:none;opacity:1}}
#palin{width:100%;border:0!important;border-bottom:1px solid var(--line)!important;border-radius:0!important;background:transparent!important;font-size:16px;padding:16px 18px;box-shadow:none!important}
#palres{max-height:56vh;overflow:auto;padding:6px}
.pal-head{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);padding:10px 12px 4px;font-weight:700}
.pal-row{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:10px;cursor:pointer}
.pal-row.sel{background:var(--accbg)}
.pal-row kbd,.pal-foot kbd,.v6-keys kbd{font-size:10.5px;background:var(--panel3);border:1px solid var(--line);border-radius:5px;padding:1px 6px;color:var(--mut);font-family:inherit}
.pal-row kbd{margin-left:auto}
.pal-ty{flex:none;width:58px;font-size:9.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);font-weight:700}
.pal-main{flex:1;min-width:0;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pal-main small{display:block;font-size:11px;color:var(--mut);overflow:hidden;text-overflow:ellipsis}
.pal-main mark{background:none;color:var(--acc2);font-weight:700}
.pal-capture{margin:6px;padding:12px;border-radius:12px;background:var(--accbg);border:1px dashed var(--line2)}
.pal-tip{padding:6px 12px 10px;font-size:12px;color:var(--mut);line-height:1.6}
.pal-foot{display:flex;gap:14px;flex-wrap:wrap;padding:9px 14px;border-top:1px solid var(--line);font-size:11px;color:var(--dim)}
.v6-keys{display:grid;grid-template-columns:1fr 1fr;gap:8px 18px;margin-top:12px}
.v6-keys div{display:flex;gap:10px;align-items:center;font-size:12.5px}.v6-keys kbd{min-width:74px;text-align:center}
.v6-irow{display:flex;gap:12px;align-items:flex-start;padding:12px 8px;border-top:1px solid var(--line)}.v6-irow:first-child{border-top:0}
.v6-acts{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}.v6-acts.col{flex-direction:column;flex-wrap:nowrap}
.v6-acts .btn{padding:5px 10px;font-size:11.5px;white-space:nowrap}
.v6-plan{display:grid;grid-template-columns:290px 1fr;gap:14px;align-items:start}
.v6-pool{position:sticky;top:0;max-height:calc(100vh - 150px);overflow:auto}
.v6-chipcard{position:relative;padding:9px 11px 9px 14px;border-radius:11px;background:var(--panel2);border:1px solid var(--line);margin-bottom:7px;cursor:grab}
.v6-chipcard:hover{border-color:var(--line2)}.v6-chipcard.dragging,.v6-blk.dragging{opacity:.45}
.v6-pri{position:absolute;left:5px;top:10px;bottom:10px;width:3px;border-radius:3px}
.v6-unsched{margin-top:10px;padding:12px;border:1.5px dashed var(--line2);border-radius:11px;text-align:center;font-size:11.5px;color:var(--dim)}
.v6-unsched.drop,.v6-lane.drop{background:var(--accbg);border-color:var(--acc)}
.v6-grid{position:relative;display:grid;grid-template-columns:56px 1fr}
.v6-hours{position:relative}.v6-hours div{position:absolute;right:8px;transform:translateY(-6px);font-size:10.5px;color:var(--dim)}
.v6-lane{position:relative;border-left:1px solid var(--line);border-radius:0 8px 8px 0}
.v6-hline{position:absolute;left:0;right:0;border-top:1px solid var(--line);opacity:.7}
.v6-now{position:absolute;left:-4px;right:0;border-top:2px solid var(--red);z-index:3}.v6-now::before{content:'';position:absolute;left:-4px;top:-5px;width:8px;height:8px;border-radius:50%;background:var(--red)}
.v6-blk{position:absolute;border:1px solid;border-radius:10px;padding:4px 9px;font-size:11.5px;overflow:hidden;cursor:pointer;z-index:2;line-height:1.35;box-shadow:var(--sh1)}
.v6-blk b{font-weight:700;opacity:.85;margin-right:4px}.v6-blk.done{opacity:.5;text-decoration:line-through}
.v6-blk[draggable]{cursor:grab}
.v6-blk-acts{position:absolute;right:4px;top:3px;display:none;gap:2px}.v6-blk:hover .v6-blk-acts{display:flex}
.v6-blk-acts button{width:20px;height:20px;border-radius:6px;background:var(--panel);color:var(--txt);font-size:12px;line-height:1;border:1px solid var(--line)}
.v6-tl{position:relative;overflow-x:auto;padding:14px 16px 18px}
.v6-tlrow{display:grid;grid-template-columns:230px 1fr;gap:12px;align-items:center;min-height:56px;position:relative;z-index:1}
.v6-tlhead{min-height:26px}
.v6-tlname{display:flex;gap:9px;align-items:center;cursor:pointer;min-width:0}.v6-tlname .dot{width:10px;height:10px;border-radius:50%;flex:none}
.v6-tltrack{position:relative;height:40px;min-width:520px}
.v6-tlhead .v6-tltrack{height:22px}
.v6-tlmonth{position:absolute;top:2px;font-size:10.5px;color:var(--dim);transform:translateX(-50%)}
.v6-tltoday{position:absolute;top:0;font-size:10px;color:var(--red);transform:translateX(-50%)}
.v6-tlbar{position:absolute;top:9px;height:22px;border:1.5px solid;border-radius:8px;overflow:hidden;cursor:pointer}
.v6-tlbar i{display:block;height:100%}
.v6-tldot{position:absolute;top:16px;width:9px;height:9px;border-radius:50%;transform:translateX(-50%);border:2px solid var(--panel);cursor:pointer;z-index:2}
.v6-tlms{position:absolute;top:13px;width:13px;height:13px;transform:translateX(-50%) rotate(45deg);border:2px solid var(--panel);cursor:pointer;z-index:2;border-radius:2px}
.v6-tlms.done{opacity:.45}
.v6-tlmslab{position:absolute;top:31px;font-size:9.5px;color:var(--mut);transform:translateX(-50%);white-space:nowrap;pointer-events:none}
.v6-tlgrid{position:absolute;left:258px;right:16px;top:14px;bottom:18px;pointer-events:none}
.v6-tlgrid i{position:absolute;top:0;bottom:0;border-left:1px dashed var(--line)}.v6-tlgrid i.today{border-left:2px solid var(--red);opacity:.7}
.v6-paper{display:flex;gap:14px;align-items:flex-start;padding:14px 8px;border-top:1px solid var(--line)}.v6-paper:first-child{border-top:0}
.v6-score{flex:none;width:44px;height:44px;border-radius:50%;border:2px solid;display:grid;place-items:center;font-weight:800;font-size:14px}
.v6-ptitle{font-weight:650;font-size:14px;color:var(--txt);text-decoration:none}.v6-ptitle:hover{color:var(--acc2);text-decoration:underline}
.v6-abs{margin-top:6px;font-size:12px;color:var(--mut);line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;cursor:pointer}
.v6-abs.open{-webkit-line-clamp:unset;display:block}
.v6-table{width:100%;border-collapse:collapse;font-size:12.5px}
.v6-table th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);padding:8px 10px;font-weight:700}
.v6-table td{padding:10px;border-top:1px solid var(--line);vertical-align:top}
.v6-table tr:hover td{background:var(--panel2)}
.v6-key{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--acc2);background:var(--accbg);padding:3px 8px;border-radius:7px}
.v6-bib{margin:8px 0 0;padding:10px;border-radius:9px;background:var(--panel3);font-size:11.5px;white-space:pre-wrap;color:var(--mut);font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.v6-res{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:14px;align-items:start}
.v6-resrow{display:flex;gap:12px;align-items:flex-start;padding:11px 8px;border-top:1px solid var(--line);cursor:pointer;border-radius:8px}.v6-resrow:first-child{border-top:0}.v6-resrow:hover{background:var(--panel2)}
.v6-stmt{font-size:12px;color:var(--mut);margin-top:4px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;line-height:1.45}
.v6-kg{display:block;max-height:calc(100vh - 210px)}
.v6-kg g:hover circle{stroke:var(--txt);stroke-width:2}
.v6-rev{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;align-items:start}
.v6-check{display:flex;gap:10px;align-items:center;padding:8px 6px;border-top:1px solid var(--line);font-size:13px;cursor:pointer}.v6-check:first-of-type{border-top:0}
.v6-ins{padding:9px 4px;border-top:1px solid var(--line);font-size:12.5px;line-height:1.5}.v6-ins:first-of-type{border-top:0}
.v6-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:14px 0 4px}
.v6-tile{display:flex;gap:12px;align-items:center;text-align:left;padding:14px;border:1px solid var(--line);border-radius:18px;background:color-mix(in srgb,var(--panel) 94%,transparent);box-shadow:var(--sh1);cursor:pointer;color:var(--txt)}
.v6-tile:hover{border-color:var(--line2);transform:translateY(-2px)}
.v6-ic{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;background:var(--accbg);font-size:17px;flex:none}
.v6-tile b{display:block;font-size:20px;line-height:1.1}.v6-tile small{display:block;font-size:11.5px;color:var(--mut)}.v6-tile em{display:block;font-style:normal;font-size:10.5px;color:var(--acc2)}
.v6-next{padding:14px;border:1px solid var(--line2);border-radius:18px;background:linear-gradient(135deg,var(--accbg),color-mix(in srgb,var(--panel) 90%,transparent));box-shadow:var(--sh1)}
.v6-next-t{font-weight:700;font-size:13.5px;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media(max-width:1100px){.v6-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.v6-res,.v6-rev{grid-template-columns:1fr}}
@media(max-width:760px){.v6-plan{grid-template-columns:1fr}.v6-pool{position:static;max-height:none}.v6-tlrow{grid-template-columns:130px 1fr}.v6-tlgrid{left:158px}.v6-keys{grid-template-columns:1fr}.v6-acts.col{flex-direction:row;flex-wrap:wrap}.v6-paper{flex-wrap:wrap}}

.v6-vlrow{display:flex;flex-direction:column;gap:8px;padding:12px 6px;border-top:1px solid var(--line)}
.v6-vlrow>div:first-child{width:100%}.v6-vlrow .v6-acts{justify-content:flex-start}
.v6-vlrow:first-of-type{border-top:0}
.v6-vlt{font-weight:620;font-size:13px;line-height:1.35}
.v6-vlframe{display:block;width:100%;height:calc(100vh - 170px);min-height:520px;border:1px solid var(--line);border-radius:16px;background:var(--panel);box-shadow:var(--sh1)}
.v6-vlweek{display:flex;gap:8px;align-items:flex-end;height:90px}
.v6-vlweek div{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;height:100%;justify-content:flex-end}
.v6-vlweek i{display:block;width:100%;min-height:3px;border-radius:6px 6px 2px 2px;background:linear-gradient(180deg,var(--acc2),var(--acc))}
.v6-vlweek span{font-size:10px;color:var(--dim)}
.v6-next{grid-column:span 2}

:root[data-theme="printemps"]{
  --bg:#fdeef3; --panel:rgba(255,255,255,.86); --panel2:color-mix(in srgb,#a02c5a 9%,rgba(255,255,255,.86)); --panel3:color-mix(in srgb,#a02c5a 5%,rgba(255,255,255,.86)); --side:rgba(255,255,255,.86); --line:rgba(43,36,51,.14); --line2:color-mix(in srgb,#2b2433 26%,transparent); --photowash:none;
  --txt:#2b2433; --mut:#5a4f66; --dim:color-mix(in srgb,#5a4f66 70%,#fdeef3); --acc:#a02c5a; --acc2:#d46a8f; --accbg:color-mix(in srgb,#a02c5a 14%,transparent);
  --red:#c0392b; --redbg:rgba(192,57,43,.14); --amber:#c0841a; --amberbg:rgba(192,132,26,.14); --green:#3f8a5a; --greenbg:rgba(63,138,90,.14);
  --teal:#3f7fb0; --tealbg:rgba(63,127,176,.14); --pink:#a02c5a;
  --st-todo:#a02c5a; --st-wip:#a02c5a; --st-rev:#c0841a; --st-done:#3f8a5a; --pri-hi:#c0392b; --pri-no:#a02c5a; --pri-lo:#5a4f66; --pc0:#a02c5a;--pc1:#3f7fb0;--pc2:#c0841a;--pc3:#3f8a5a;--pc4:#8e5ba6;--pc5:#d46a8f;--pc6:#2f8f8a;--pc7:#a0561f;
  --logo1:#a02c5a; --logo2:#d46a8f; --oninv:#ffffff; --sh1:0 6px 18px rgba(30,30,40,.10); --sh2:0 10px 40px rgba(30,30,40,.10); --sh3:0 10px 26px rgba(30,30,40,.10); --ovlc:rgba(30,30,40,.22);
  --app-bg:#fdeef3; --season-bg:linear-gradient(160deg,#fdeef3,#eaf6e4); --amb1:rgba(244,166,192,.55); --amb2:rgba(170,214,160,.55);
  --hero-bg:linear-gradient(135deg,color-mix(in srgb,rgba(255,255,255,.86) 92%,#a02c5a),rgba(255,255,255,.86)); --hero-glow:radial-gradient(circle at 78% 22%,rgba(244,166,192,.55),transparent 40%),radial-gradient(circle at 26% 74%,rgba(170,214,160,.55),transparent 44%); color-scheme:light;
}
.theme-preview[data-theme-preview="printemps"]{--preview-bg:linear-gradient(160deg,#fdeef3,#eaf6e4);--preview-side:rgba(255,255,255,.86);--preview-card:color-mix(in srgb,#a02c5a 30%,transparent)}
:root[data-theme="ete"]{
  --bg:#fff6d9; --panel:rgba(255,252,240,.88); --panel2:color-mix(in srgb,#8a4b00 9%,rgba(255,252,240,.88)); --panel3:color-mix(in srgb,#8a4b00 5%,rgba(255,252,240,.88)); --side:rgba(255,252,240,.88); --line:rgba(42,36,18,.16); --line2:color-mix(in srgb,#2a2412 26%,transparent); --photowash:none;
  --txt:#2a2412; --mut:#5c4f2a; --dim:color-mix(in srgb,#5c4f2a 70%,#fff6d9); --acc:#8a4b00; --acc2:#c47a12; --accbg:color-mix(in srgb,#8a4b00 14%,transparent);
  --red:#c0392b; --redbg:rgba(192,57,43,.14); --amber:#c0841a; --amberbg:rgba(192,132,26,.14); --green:#3f8a5a; --greenbg:rgba(63,138,90,.14);
  --teal:#3f7fb0; --tealbg:rgba(63,127,176,.14); --pink:#8a4b00;
  --st-todo:#8a4b00; --st-wip:#8a4b00; --st-rev:#c0841a; --st-done:#3f8a5a; --pri-hi:#c0392b; --pri-no:#8a4b00; --pri-lo:#5c4f2a; --pc0:#8a4b00;--pc1:#3f7fb0;--pc2:#c0841a;--pc3:#3f8a5a;--pc4:#8e5ba6;--pc5:#c47a12;--pc6:#2f8f8a;--pc7:#a0561f;
  --logo1:#8a4b00; --logo2:#c47a12; --oninv:#ffffff; --sh1:0 6px 18px rgba(30,30,40,.10); --sh2:0 10px 40px rgba(30,30,40,.10); --sh3:0 10px 26px rgba(30,30,40,.10); --ovlc:rgba(30,30,40,.22);
  --app-bg:#fff6d9; --season-bg:linear-gradient(160deg,#fff6d9,#ffe6b8); --amb1:rgba(255,206,92,.55); --amb2:rgba(255,160,90,.40);
  --hero-bg:linear-gradient(135deg,color-mix(in srgb,rgba(255,252,240,.88) 92%,#8a4b00),rgba(255,252,240,.88)); --hero-glow:radial-gradient(circle at 78% 22%,rgba(255,206,92,.55),transparent 40%),radial-gradient(circle at 26% 74%,rgba(255,160,90,.40),transparent 44%); color-scheme:light;
}
.theme-preview[data-theme-preview="ete"]{--preview-bg:linear-gradient(160deg,#fff6d9,#ffe6b8);--preview-side:rgba(255,252,240,.88);--preview-card:color-mix(in srgb,#8a4b00 30%,transparent)}
:root[data-theme="automne"]{
  --bg:#fbe9d6; --panel:rgba(255,248,238,.88); --panel2:color-mix(in srgb,#8f3410 9%,rgba(255,248,238,.88)); --panel3:color-mix(in srgb,#8f3410 5%,rgba(255,248,238,.88)); --side:rgba(255,248,238,.88); --line:rgba(51,32,15,.16); --line2:color-mix(in srgb,#33200f 26%,transparent); --photowash:none;
  --txt:#33200f; --mut:#644a30; --dim:color-mix(in srgb,#644a30 70%,#fbe9d6); --acc:#8f3410; --acc2:#c8501e; --accbg:color-mix(in srgb,#8f3410 14%,transparent);
  --red:#c0392b; --redbg:rgba(192,57,43,.14); --amber:#c0841a; --amberbg:rgba(192,132,26,.14); --green:#3f8a5a; --greenbg:rgba(63,138,90,.14);
  --teal:#3f7fb0; --tealbg:rgba(63,127,176,.14); --pink:#8f3410;
  --st-todo:#8f3410; --st-wip:#8f3410; --st-rev:#c0841a; --st-done:#3f8a5a; --pri-hi:#c0392b; --pri-no:#8f3410; --pri-lo:#644a30; --pc0:#8f3410;--pc1:#3f7fb0;--pc2:#c0841a;--pc3:#3f8a5a;--pc4:#8e5ba6;--pc5:#c8501e;--pc6:#2f8f8a;--pc7:#a0561f;
  --logo1:#8f3410; --logo2:#c8501e; --oninv:#ffffff; --sh1:0 6px 18px rgba(30,30,40,.10); --sh2:0 10px 40px rgba(30,30,40,.10); --sh3:0 10px 26px rgba(30,30,40,.10); --ovlc:rgba(30,30,40,.22);
  --app-bg:#fbe9d6; --season-bg:linear-gradient(160deg,#fbe9d6,#f1cfa8); --amb1:rgba(214,110,40,.40); --amb2:rgba(168,70,30,.32);
  --hero-bg:linear-gradient(135deg,color-mix(in srgb,rgba(255,248,238,.88) 92%,#8f3410),rgba(255,248,238,.88)); --hero-glow:radial-gradient(circle at 78% 22%,rgba(214,110,40,.40),transparent 40%),radial-gradient(circle at 26% 74%,rgba(168,70,30,.32),transparent 44%); color-scheme:light;
}
.theme-preview[data-theme-preview="automne"]{--preview-bg:linear-gradient(160deg,#fbe9d6,#f1cfa8);--preview-side:rgba(255,248,238,.88);--preview-card:color-mix(in srgb,#8f3410 30%,transparent)}
:root[data-theme="hiver"]{
  --bg:#0e1a2c; --panel:rgba(12,22,38,.80); --panel2:color-mix(in srgb,#8fc2ff 9%,rgba(12,22,38,.80)); --panel3:color-mix(in srgb,#8fc2ff 5%,rgba(12,22,38,.80)); --side:rgba(12,22,38,.80); --line:rgba(233,241,251,.20); --line2:color-mix(in srgb,#e9f1fb 26%,transparent); --photowash:none;
  --txt:#e9f1fb; --mut:#b3c4da; --dim:color-mix(in srgb,#b3c4da 70%,#0e1a2c); --acc:#8fc2ff; --acc2:#b9d8ff; --accbg:color-mix(in srgb,#8fc2ff 14%,transparent);
  --red:#c0392b; --redbg:rgba(192,57,43,.14); --amber:#c0841a; --amberbg:rgba(192,132,26,.14); --green:#3f8a5a; --greenbg:rgba(63,138,90,.14);
  --teal:#3f7fb0; --tealbg:rgba(63,127,176,.14); --pink:#8fc2ff;
  --st-todo:#8fc2ff; --st-wip:#8fc2ff; --st-rev:#c0841a; --st-done:#3f8a5a; --pri-hi:#c0392b; --pri-no:#8fc2ff; --pri-lo:#b3c4da; --pc0:#8fc2ff;--pc1:#3f7fb0;--pc2:#c0841a;--pc3:#3f8a5a;--pc4:#8e5ba6;--pc5:#b9d8ff;--pc6:#2f8f8a;--pc7:#a0561f;
  --logo1:#8fc2ff; --logo2:#b9d8ff; --oninv:#0e1a2c; --sh1:0 6px 18px rgba(0,0,0,.35); --sh2:0 10px 40px rgba(0,0,0,.35); --sh3:0 10px 26px rgba(0,0,0,.35); --ovlc:rgba(0,0,0,.45);
  --app-bg:#0e1a2c; --season-bg:linear-gradient(160deg,#0e1a2c,#1c3350); --amb1:rgba(90,140,210,.35); --amb2:rgba(130,110,200,.25);
  --hero-bg:linear-gradient(135deg,color-mix(in srgb,rgba(12,22,38,.80) 92%,#8fc2ff),rgba(12,22,38,.80)); --hero-glow:radial-gradient(circle at 78% 22%,rgba(90,140,210,.35),transparent 40%),radial-gradient(circle at 26% 74%,rgba(130,110,200,.25),transparent 44%); color-scheme:dark;
}
.theme-preview[data-theme-preview="hiver"]{--preview-bg:linear-gradient(160deg,#0e1a2c,#1c3350);--preview-side:rgba(12,22,38,.80);--preview-card:color-mix(in srgb,#8fc2ff 30%,transparent)}
html.season-on{background:var(--season-bg)!important;transition:background 1.2s ease}
html.season-on body,html.season-on #app,html.season-on #view{background:transparent!important}
html.season-on #app{position:relative;z-index:1}
html.season-on .panel,html.season-on .card,html.season-on .kpi,html.season-on #side,html.season-on #top{backdrop-filter:blur(6px)!important;-webkit-backdrop-filter:blur(6px)!important}
#seasonAura,#seasonFx{position:fixed;inset:0;pointer-events:none;z-index:0;display:none}
#seasonFx{z-index:40}
html.season-on #seasonAura,html.season-on #seasonFx{display:block}
#seasonAura{overflow:hidden}
#seasonAura i{position:absolute;width:70vmax;height:70vmax;border-radius:50%;will-change:transform}
#seasonAura i:nth-child(1){background:radial-gradient(circle,var(--amb1),transparent 65%);top:-25vmax;left:-20vmax;animation:seasonDrift1 48s ease-in-out infinite alternate}
#seasonAura i:nth-child(2){background:radial-gradient(circle,var(--amb2),transparent 65%);bottom:-30vmax;right:-25vmax;animation:seasonDrift2 62s ease-in-out infinite alternate}
@keyframes seasonDrift1{to{transform:translate3d(28vw,18vh,0) scale(1.15)}}
@keyframes seasonDrift2{to{transform:translate3d(-26vw,-16vh,0) scale(1.1)}}
html.season-on body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif!important;line-height:1.6;transition:color .8s ease}
html.season-on .panel,html.season-on .card,html.season-on .kpi{border:1px solid var(--line)!important;border-radius:20px!important;box-shadow:0 10px 40px rgba(0,0,0,.10)!important;transition:background .8s ease,border-color .8s ease}

:root[data-theme="terracotta"]{
  --bg:#F5F4ED; --panel:#FFFFFF; --panel2:#F0EEE6; --panel3:#FAF9F5; --side:#EEECE2; --line:#E5E2D6; --line2:#D6D1C1; --photowash:none;
  --txt:#1F1E1D; --mut:#5E5D59; --dim:#97948A; --acc:#C96442; --acc2:#D97757; --accbg:#F7E7DE;
  --red:#B53333; --redbg:rgba(181,51,51,.12); --amber:#B7791F; --amberbg:rgba(183,121,31,.13); --green:#4F7A52; --greenbg:rgba(79,122,82,.13);
  --teal:#3E6E78; --tealbg:rgba(62,110,120,.13); --pink:#A6496B;
  --st-todo:#A6496B; --st-wip:#D97757; --st-rev:#B7791F; --st-done:#4F7A52; --pri-hi:#B53333; --pri-no:#C96442; --pri-lo:#97948A; --pc0:#D97757;--pc1:#3E6E78;--pc2:#A6496B;--pc3:#4F7A52;--pc4:#B7791F;--pc5:#7A5FA6;--pc6:#3E6B8A;--pc7:#8C5A3C;
  --logo1:#1F1E1D; --logo2:#D97757; --oninv:#FAF9F5; --sh1:0 6px 18px rgba(60,40,20,.06); --sh2:0 24px 60px rgba(60,40,20,.12); --sh3:0 12px 30px rgba(60,40,20,.10); --ovlc:rgba(31,30,29,.28);
  --app-bg:#F5F4ED; --hero-bg:linear-gradient(135deg,#FFFFFF 0%,#FBF3EC 55%,#F4E2D6 100%); --hero-glow:radial-gradient(circle at 78% 22%,rgba(217,119,87,.22),transparent 36%),radial-gradient(circle at 24% 76%,rgba(201,100,66,.08),transparent 42%);
  --tc-a1:rgba(217,119,87,.20); --tc-a2:rgba(232,180,140,.22); --tc-a3:rgba(201,100,66,.10); color-scheme:light;
}
:root[data-theme="ember"]{
  --bg:#262624; --panel:#30302E; --panel2:#383835; --panel3:#3E3E3A; --side:#1F1E1D; --line:rgba(250,249,245,.08); --line2:rgba(250,249,245,.15); --photowash:none;
  --txt:#FAF9F5; --mut:#C2C0B6; --dim:#85837B; --acc:#D97757; --acc2:#E8916F; --accbg:rgba(217,119,87,.15);
  --red:#F08A80; --redbg:rgba(240,138,128,.14); --amber:#E7B75F; --amberbg:rgba(231,183,95,.14); --green:#8BC79A; --greenbg:rgba(139,199,154,.14);
  --teal:#7FB8C8; --tealbg:rgba(127,184,200,.14); --pink:#E59AB8;
  --st-todo:#E59AB8; --st-wip:#D97757; --st-rev:#E7B75F; --st-done:#8BC79A; --pri-hi:#F08A80; --pri-no:#D97757; --pri-lo:#85837B; --pc0:#D97757;--pc1:#7FB8C8;--pc2:#E59AB8;--pc3:#8BC79A;--pc4:#E7B75F;--pc5:#B4A6E8;--pc6:#9CC7E8;--pc7:#E8916F;
  --logo1:#3A3936; --logo2:#D97757; --oninv:#1F1E1D; --sh1:0 8px 20px rgba(0,0,0,.28); --sh2:0 25px 70px rgba(0,0,0,.48); --sh3:0 12px 32px rgba(0,0,0,.38); --ovlc:rgba(0,0,0,.6);
  --app-bg:#262624; --hero-bg:linear-gradient(135deg,#34332F 0%,#2B2A27 55%,#3A2C25 100%); --hero-glow:radial-gradient(circle at 78% 22%,rgba(217,119,87,.20),transparent 36%),radial-gradient(circle at 24% 76%,rgba(232,145,111,.07),transparent 42%);
  --tc-a1:rgba(217,119,87,.13); --tc-a2:rgba(232,145,111,.08); --tc-a3:rgba(160,80,50,.12); color-scheme:dark;
}
.theme-preview[data-theme-preview="terracotta"]{--preview-bg:linear-gradient(135deg,#FFFFFF,#F4E2D6);--preview-side:#EEECE2;--preview-card:#EDB89F}
.theme-preview[data-theme-preview="ember"]{--preview-bg:linear-gradient(135deg,#34332F,#262624);--preview-side:#1F1E1D;--preview-card:rgba(217,119,87,.45)}
:root[data-theme="terracotta"] .hero-banner::before{background:linear-gradient(135deg,rgba(255,255,255,.5),transparent 48%,rgba(0,0,0,.02))!important}
:is([data-theme="terracotta"],[data-theme="ember"]) #vtitle,:is([data-theme="terracotta"],[data-theme="ember"]) .hero-banner :is(h1,h2){font-family:ui-serif,"Iowan Old Style","Palatino Linotype",Georgia,serif;letter-spacing:-.01em;font-weight:600}
:is([data-theme="terracotta"],[data-theme="ember"]) .btn.pri{background:linear-gradient(110deg,var(--acc) 0%,var(--acc2) 45%,var(--acc) 100%);background-size:220% 100%;color:#fff;transition:background-position .6s cubic-bezier(.2,.75,.2,1),transform .15s ease,box-shadow .25s ease}
:is([data-theme="terracotta"],[data-theme="ember"]) .btn.pri:hover{background-position:100% 0;box-shadow:0 6px 18px color-mix(in srgb,var(--acc) 35%,transparent)}
:is([data-theme="terracotta"],[data-theme="ember"]) #app{position:relative;z-index:1;background:transparent!important}
#tcAura{position:fixed;inset:0;pointer-events:none;z-index:0;overflow:hidden;display:none;contain:strict}
:is([data-theme="terracotta"],[data-theme="ember"]) #tcAura{display:block}
#tcAura i{position:absolute;border-radius:50%;will-change:transform}
#tcAura i:nth-child(1){width:62vmax;height:62vmax;top:-22vmax;right:-18vmax;background:radial-gradient(circle,var(--tc-a1),transparent 64%)}
#tcAura i:nth-child(2){width:54vmax;height:54vmax;bottom:-24vmax;left:-14vmax;background:radial-gradient(circle,var(--tc-a2),transparent 64%)}
#tcAura i:nth-child(3){width:38vmax;height:38vmax;top:40%;left:42%;background:radial-gradient(circle,var(--tc-a3),transparent 66%)}
#tcBar{position:fixed;top:0;left:0;right:0;height:2px;z-index:9999;pointer-events:none;opacity:0;transition:opacity .25s ease;background:linear-gradient(90deg,transparent,var(--acc2),var(--acc),transparent);background-size:40% 100%;background-repeat:no-repeat}
#tcBar.on{opacity:1;animation:tcBar 1.1s cubic-bezier(.4,0,.2,1) infinite}
@keyframes tcBar{from{background-position:-40% 0}to{background-position:140% 0}}
@keyframes tcDrift1{to{transform:translate3d(-18vw,14vh,0) scale(1.12)}}
@keyframes tcDrift2{to{transform:translate3d(20vw,-12vh,0) scale(1.08)}}
@keyframes tcDrift3{0%{transform:translate3d(0,0,0) scale(1)}50%{transform:translate3d(-12vw,-10vh,0) scale(1.18)}100%{transform:translate3d(10vw,8vh,0) scale(.92)}}
@keyframes tcViewIn{from{opacity:0;transform:translate3d(0,12px,0) scale(.992)}to{opacity:1;transform:none}}
@keyframes tcRipple{to{transform:translate(-50%,-50%) scale(1);opacity:0}}
@keyframes tcReveal{from{clip-path:circle(0 at var(--vtx,50%) var(--vty,50%))}to{clip-path:circle(150vmax at var(--vtx,50%) var(--vty,50%))}}
@keyframes tcNavIn{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@media (prefers-reduced-motion:no-preference){
  #tcAura i:nth-child(1){animation:tcDrift1 46s ease-in-out infinite alternate}
  #tcAura i:nth-child(2){animation:tcDrift2 58s ease-in-out infinite alternate}
  #tcAura i:nth-child(3){animation:tcDrift3 72s ease-in-out infinite alternate}
  :is([data-theme="terracotta"],[data-theme="ember"]) #view.viewenter{animation:tcViewIn .42s cubic-bezier(.2,.75,.2,1)}
  :is([data-theme="terracotta"],[data-theme="ember"]) :is(.card,.kpi,.panel){background-image:radial-gradient(380px circle at var(--mx,-600px) var(--my,-600px),color-mix(in srgb,var(--acc2) 9%,transparent),transparent 62%);transition:border-color .25s ease,box-shadow .3s ease,transform .3s cubic-bezier(.2,.75,.2,1)}
  :is([data-theme="terracotta"],[data-theme="ember"]) :is(.card,.panel):hover{border-color:color-mix(in srgb,var(--acc2) 34%,var(--line))}
  :is([data-theme="terracotta"],[data-theme="ember"]) .kpi:hover{transform:translateY(-3px);box-shadow:var(--sh3);border-color:color-mix(in srgb,var(--acc2) 40%,var(--line))}
  :is([data-theme="terracotta"],[data-theme="ember"]) .nav{position:relative}
  :is([data-theme="terracotta"],[data-theme="ember"]) .nav.on::before{content:'';position:absolute;left:0;top:18%;bottom:18%;width:3px;border-radius:3px;background:linear-gradient(180deg,var(--acc2),var(--acc));transform-origin:center;animation:tcNavIn .35s cubic-bezier(.2,.75,.2,1)}
  :is([data-theme="terracotta"],[data-theme="ember"]) .btn{position:relative;overflow:hidden}
  .tc-ripple{position:absolute;border-radius:50%;pointer-events:none;width:240px;height:240px;transform:translate(-50%,-50%) scale(0);background:radial-gradient(circle,rgba(255,255,255,.45),transparent 60%);animation:tcRipple .6s cubic-bezier(.2,.75,.2,1) forwards}
  ::view-transition-new(root){animation:tcReveal .7s cubic-bezier(.2,.75,.2,1)}
  ::view-transition-old(root){animation:none}
}
</style>
</head>
<body>
<div id="tcAura" aria-hidden="true"><i></i><i></i><i></i></div><div id="tcBar" aria-hidden="true"></div>
<div id="seasonAura" aria-hidden="true"><i></i><i></i></div><canvas id="seasonFx" aria-hidden="true"></canvas>
<div id="app">
  <div id="side">
    <div id="logo">
      <div class="mark"><svg viewBox="0 0 64 64" aria-hidden="true"><path fill="rgba(255,255,255,.95)" d="M8 45 22 20l8 13 6-8 20 20H8z"/><path fill="rgba(255,255,255,.72)" d="M13 45 26 24l6 10 4-5 15 16H13z"/></svg></div>
      <div><b>VanoAtlas</b></div>
    </div>
    <button class="nav" data-v="dash"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>Dashboard<span class="k">1</span></button>
    <button class="nav" data-v="board"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="10" y="4" width="5" height="10" rx="1.5"/><rect x="17" y="4" width="5" height="13" rx="1.5"/></svg>Tasks<span class="k">2</span></button>
    <button class="nav" data-v="projects"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>Projects<span class="k">3</span></button>
    <button class="nav" data-v="agenda"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>Meetings<span class="k">4</span></button>
    <button class="nav" data-v="daily"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/></svg>Daily Tasks<span class="k">5</span></button>
    <button class="nav" data-v="planner"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 2h6"/></svg>Day Planner<span class="v6new">NEW</span></button>
    <button class="nav" data-v="timeline"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h10M7 12h12M5 18h8"/><circle cx="16" cy="6" r="1.6"/><circle cx="4" cy="12" r="1.6"/><circle cx="16" cy="18" r="1.6"/></svg>Timeline<span class="v6new">NEW</span></button>
    <div class="nav-group">Research</div>
    <button class="nav" data-v="vanolib"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/></svg>VanoLib<span class="v6new">NEW</span></button>
    <div class="nav-group">Apps</div>
    <button class="nav" data-v="vanonote"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>VanoNote</button>
    <button class="nav" data-v="vanoleaf"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.5 19 2c1 2 2 4.2 2 8 0 5.5-4.8 10-10 10Z"/><path d="M2 21c0-3 1.9-5.4 5.1-6"/></svg>VanoLeaf</button>
    <div class="nav-group">More</div>
    <button class="nav" data-v="notes"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>Notes<span class="k">6</span></button>
    <div class="grow"></div>
    <button id="focusbtn" onclick="openFocusMode()" title="Focus session"><span style="font-size:15px">◎</span><span>Focus Session</span></button>
    <button id="healthbtn" onclick="openDataHealth()" title="Data health"><span style="font-size:14px">◇</span><span>Data Health</span></button>
    <button id="theme" onclick="toggleTheme()" title="Night mode"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg><span id="themetxt">Night mode</span></button>
    <button id="sync" onclick="syncGcal()" title="Sync Google Calendar"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-2.6-6.3M21 3v6h-6"/></svg><span id="synctxt">Sync Google Calendar</span></button>
    <div style="display:flex;gap:4px;align-items:stretch">
      <button id="icloudsync" style="flex:1" onclick="syncOrConnectIcloud()" title="iCloud Calendar"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 18H7a4 4 0 0 1-1-7.9 5 5 0 0 1 9.8-1.7A4.5 4.5 0 0 1 18 18z"/></svg><span id="icloudtxt">Connect iCloud Calendar</span></button>
      <button id="icloudcfg" title="iCloud settings" onclick="openIcloudSettings()" style="flex:none;padding:0 10px"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg></button>
    </div>
    <button id="drivebk" onclick="backupDrive()" title="Backup to Drive"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg><span id="drivetxt">Backup to Drive</span></button>
  </div>
  <div id="main">
    <div id="top">
      <span id="vtitle">Dashboard</span>
      <span id="net" class="chip" style="display:none;background:var(--amberbg);color:var(--amber);cursor:pointer" onclick="tryReconnect()" title="Tap to retry sync"></span>
      <span class="sp"></span>
      <input id="search" placeholder="Search anything...">
      <div id="topactions">
        <button class="topicon" id="langbtn" title="Passer en français" onclick="vaLangToggle()" style="width:auto;min-width:40px;padding:0 10px;font-weight:700;font-size:12px;letter-spacing:.04em">FR</button>
        <button class="topicon" title="Notifications" onclick="openNotifSettings()"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg></button>
        <button class="topicon" title="Command Center" onclick="openCommandCenter()"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v12H4z"/><path d="m22 6-10 7L2 6"/></svg></button>
        <div id="profilepill" title="Your name" style="cursor:pointer" onclick="vaSetName()">◆</div>
      </div>
      <button class="btn pri" id="newbtn" onclick="quickNew()">+ New</button>
    </div>
    <div id="view"></div>
  </div>
</div>
<div id="focusovl" class="focus-overlay"><div id="focuspanel" class="focus-panel"></div></div>
<div id="ovl"><div id="modal"></div></div>
<div id="graphovl" class="hwovl" style="position:fixed;overflow:auto;padding:40px 20px"></div>
<div id="hwovl" class="hwovl">
  <div class="hwpanel">
    <div class="hwpage-wrap"><div id="hwPage" class="hw-page"></div></div>
    <div class="hwside">
      <h3>Handwrite note</h3>
      <div class="field"><label>Writing style</label>
        <select id="hwFontSel" onchange="hwUpdateSettings()"></select>
      </div>
      <div class="field"><label>Paper</label>
        <select id="hwPaper" onchange="hwUpdateSettings()">
          <option value="grid">Grid paper</option>
          <option value="lines">Lined paper</option>
          <option value="dots">Dotted paper</option>
          <option value="cream">Aged cream paper</option>
        </select>
      </div>
      <div class="field"><label>Ink</label>
        <select id="hwInk" onchange="hwUpdateSettings()">
          <option value="blue">Fountain pen blue</option>
          <option value="black">Black</option>
          <option value="sepia">Sepia</option>
        </select>
      </div>
      <div class="field"><label>Irregularity <span id="hwIrregVal"></span></label>
        <input type="range" id="hwIrreg" min="0" max="100" value="40" oninput="hwUpdateSettings()">
      </div>
      <button class="btn ghost" onclick="hwReshuffle()">🔀 Shuffle variation</button>
      <div class="hwside-acts">
        <button class="btn ghost" onclick="hwExportImage()">🖼 Export image</button>
        <button class="btn ghost" onclick="hwExportPdf()">⤓ Export PDF</button>
        <button class="btn pri" onclick="closeHandwrite()">Close</button>
      </div>
      <div id="hwStatus" class="hwstatus"></div>
    </div>
  </div>
</div>
<div id="toast"></div>
<script>
let S = {};
let VIEW = localStorage.getItem('m2view') || 'dash';
if(!['dash','board','projects','agenda','daily','notes','wins','planner','timeline','vanolib','vanonote','vanoleaf','inbox','radar','biblio','results','review','analytics','matrix','books','finance','ts'].includes(VIEW)) VIEW='dash';
let Q = '';
let PROJFILTER = 0;
let AGDATE = new Date();
let AGMODE = localStorage.getItem('m2agmode') || 'month';
let AGSEL = iso(new Date());
let NOTESEL = 0, FINTAB = 'overview', DAILYDATE = iso(new Date());
let TSTAB = localStorage.getItem('m2_tstab') || 'goals';
const NOTE_CATS = ['New ideas','Projects','Research','Teaching','Personal','Archive'];
let NOTECATDEFAULT = localStorage.getItem('m2_notecat_default') || 'New ideas';
let NOTECATS_COLLAPSED = (()=>{ try{ return JSON.parse(localStorage.getItem('m2_notecats_collapsed')||'[]'); }catch(e){ return []; } })();
let _compileTimer = null, _compileGen = 0;

const STATUSES  = ['To Do','In Progress','In Review','Done'];
const STATCOL   = {'To Do':'var(--st-todo)','In Progress':'var(--st-wip)','In Review':'var(--st-rev)','Done':'var(--st-done)'};
const PRIORITIES= ['High','Normal','Low'];
const PRICOL    = {High:'var(--pri-hi)',Normal:'var(--pri-no)',Low:'var(--pri-lo)'};
const TASK_TYPES= ['Writing','Computation','Reading','Seminar','Code','Admin','Other'];
const MTG_TYPES = ['Advisory Meeting','Class','Seminar','Conference','Thesis Defense','Team Meeting','Workshop','Office Hours','Other'];
const WIN_TYPES = ['Proof','Publication','Conference','Award','Chapter','Seminar Talk','Teaching','Collaboration','Milestone','Other'];
const WIN_ICONS = {Proof:'∎',Publication:'📄',Conference:'🎤',Award:'🏆',Chapter:'📖','Seminar Talk':'🎓',Teaching:'✏️',Collaboration:'🤝',Milestone:'⬦',Other:'✦'};
const DAILY_CATS= ['Research','Personal','Health','Sport','Shopping','Administrative','Social','Other'];
const FIN_CATS  = ['Housing','Food','Transport','Health','Entertainment','Subscriptions','Savings','Education','Clothing','Other'];
const GENRES    = ['Mathematics','Algebra','Analysis','Number Theory','Topology','Geometry','Probability & Statistics','Mathematical Logic','Combinatorics','Applied Mathematics','Physics','Computer Science','Philosophy','History','Biography','Fiction','Other'];
const PROJCOLS  = ['var(--pc0)','var(--pc1)','var(--pc2)','var(--pc3)','var(--pc4)','var(--pc5)','var(--pc6)','var(--pc7)'];

function iso(d){ return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
function esc(s){ return String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmtd(s){ if(!s) return ''; const d=new Date(s+'T00:00:00'); return isNaN(d)?s:d.toLocaleDateString('en-US',{month:'short',day:'numeric'}); }
function fmtdy(s){ if(!s) return ''; const d=new Date(s+'T00:00:00'); return isNaN(d)?s:d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}); }
function daysTo(s){ const t=new Date(); t.setHours(0,0,0,0); return Math.round((new Date(s+'T00:00:00')-t)/864e5); }
function projName(id){ const p=(S.projects||[]).find(p=>p.id==id); return p?p.title:''; }
function projColor(id){ return id?PROJCOLS[id%PROJCOLS.length]:'var(--dim)'; }

function fmtDur(min){
  min=Math.round(min||0);
  const h=Math.floor(min/60), m=min%60;
  return h?(h+'h'+(m?m+'m':'')):(m+'m');
}
async function toggleTimer(taskId){
  const open=(S.time_entries||[]).find(e=>e.task_id==taskId && !e.ended_at);
  const now=new Date().toISOString();
  if(open){
    const dur=Math.max(1,Math.round((Date.now()-new Date(open.started_at).getTime())/60000));
    await mutate('PUT','time_entries',open.id,{ended_at:now,duration_min:dur});
  }else{
    const other=(S.time_entries||[]).find(e=>!e.ended_at);
    if(other){
      const d=Math.max(1,Math.round((Date.now()-new Date(other.started_at).getTime())/60000));
      await mutate('PUT','time_entries',other.id,{ended_at:now,duration_min:d});
    }
    await mutate('POST','time_entries',null,{task_id:taskId,started_at:now,ended_at:'',duration_min:0});
  }
  await load();
}
function toast(m){ const t=document.getElementById('toast'); t.textContent=m; t.classList.add('show'); clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('show'),2400); }

const API_INFLIGHT=new Map(), API_MICRO=new Map();
const API_MICRO_OK=['/api/vanolib/summary','/api/settings','/api/icloud/status'];
let API_BUSY=0, API_BAR_T=0;
function apiBar(d){ API_BUSY=Math.max(0,API_BUSY+d); const b=document.getElementById('tcBar'); if(!b) return;
  if(API_BUSY>0){ if(!API_BAR_T) API_BAR_T=setTimeout(()=>{ if(API_BUSY>0) b.classList.add('on'); },220); }
  else { clearTimeout(API_BAR_T); API_BAR_T=0; b.classList.remove('on'); } }
async function apiRaw(method,path,body){
  apiBar(1);
  try{ const r = await fetch(path,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined}); return await r.json(); }
  finally{ apiBar(-1); }
}
function api(method,path,body){
  if(method!=='GET'){ API_MICRO.clear(); return apiRaw(method,path,body); }
  const base=path.split('?')[0];
  if(API_MICRO_OK.includes(base)){ const m=API_MICRO.get(path); if(m&&Date.now()-m.t<1500) return Promise.resolve(m.v); }
  if(API_INFLIGHT.has(path)) return API_INFLIGHT.get(path);
  const pr=apiRaw('GET',path).then(v=>{ if(API_MICRO_OK.includes(base)) API_MICRO.set(path,{t:Date.now(),v}); return v; }).finally(()=>API_INFLIGHT.delete(path));
  API_INFLIGHT.set(path,pr); return pr;
}

let OFFLINE=false;
let QUEUE=JSON.parse(localStorage.getItem('m2queue')||'[]');
let TMP=+(localStorage.getItem('m2tmp')||-1);
function tmpId(){ TMP-=1; localStorage.setItem('m2tmp',TMP); return TMP; }
function qSave(){ localStorage.setItem('m2queue',JSON.stringify(QUEUE)); netBadge(); }
function cSave(){ try{ localStorage.setItem('m2cache',JSON.stringify(S)); }catch(e){} }
function setOffline(v){ if(OFFLINE!==v){ OFFLINE=v; if(!v&&QUEUE.length===0) toast('Back online'); } netBadge(); }
function netBadge(){
  const n=document.getElementById('net'); if(!n) return;
  if(OFFLINE){ n.style.display='inline-flex'; n.textContent='● offline'+(QUEUE.length?' · '+QUEUE.length+' pending':''); }
  else if(QUEUE.length){ n.style.display='inline-flex'; n.textContent='↻ '+QUEUE.length+' to sync'; }
  else n.style.display='none';
}
function applyLocal(method,table,id,body){
  if(method==='POST'){ const r=Object.assign({id:id,created_at:new Date().toISOString().slice(0,19)},body); (S[table]=S[table]||[]).push(r); }
  else if(method==='PUT'){ const r=(S[table]||[]).find(x=>x.id==id); if(r) Object.assign(r,body); }
  else if(method==='DELETE'){ S[table]=(S[table]||[]).filter(x=>x.id!=id); }
  cSave();
}
async function mutate(method,table,id,body){
  if(!OFFLINE){
    try{
      const path='/api/'+table+(method==='POST'?'':'/'+id);
      return await api(method,path,body);
    }catch(e){ setOffline(true); }
  }
  if(method==='POST'){ id=tmpId(); QUEUE.push({method,table,tmpId:id,body}); }
  else QUEUE.push({method,table,id,body});
  applyLocal(method,table,id,body); qSave();
  return {ok:true,queued:true,id:id};
}
async function flushQueue(){
  const map={};
  while(QUEUE.length){
    const e=QUEUE[0];
    let id=e.id; if(id<0&&map[id]!==undefined) id=map[id];
    const body=Object.assign({},e.body);
    if(body.project_id<0&&map[body.project_id]!==undefined) body.project_id=map[body.project_id];
    if(e.method==='POST'){ const r=await api('POST','/api/'+e.table,body); map[e.tmpId]=r.id; }
    else if(id>0){ await api(e.method,'/api/'+e.table+'/'+id,body); }
    QUEUE.shift(); qSave();
  }
}
async function load(){
  try{
    await flushQueue();
    S=await api('GET','/api/all');
    setOffline(false); cSave();
  }catch(e){
    setOffline(true);
    S=JSON.parse(localStorage.getItem('m2cache')||'{}');
  }
  render(); netBadge();
}
async function tryReconnect(){
  try{ await api('GET','/api/ping'); const had=QUEUE.length; setOffline(false); await load();
       if(had) toast('Synced '+had+' change'+(had>1?'s':'')+' to your Mac'); }
  catch(e){ toast('Still offline — changes are saved on this device'); }
}
window.addEventListener('online',()=>tryReconnect());
setInterval(()=>{ if(OFFLINE||QUEUE.length) tryReconnect(); },15000);
setInterval(()=>{
  const ae=document.activeElement;
  const busy = ae && (['INPUT','TEXTAREA','SELECT'].includes(ae.tagName) || ae.isContentEditable);
  const modalOpen = document.getElementById('ovl').classList.contains('show');
  if(!busy && !modalOpen && !OFFLINE) load();
}, 120000);
if('serviceWorker' in navigator){ try{ navigator.serviceWorker.register('/sw.js',{updateViaCache:'none'}).then(r=>r.update()); }catch(e){} }

async function save(t,obj,id){ await mutate(id?'PUT':'POST',t,id,obj); await load(); }
async function del(t,id){ await mutate('DELETE',t,id); await load(); }

function modal(title, fields, initial, onSave, onDelete){
  const ovl=document.getElementById('ovl'), m=document.getElementById('modal');
  let h='<h3>'+esc(title)+'</h3>';
  let i=0, pending=[];
  while(i<fields.length){
    const f=fields[i], nx=fields[i+1];
    const half=f.half&&nx&&nx.half;
    const one=f=>{
      const v=initial[f.k]??f.def??'';
      let inp;
      if(f.type==='textarea') inp='<textarea data-k="'+f.k+'" rows="'+(f.rows||3)+'">'+esc(v)+'</textarea>';
      else if(f.type==='select'){
        inp='<select data-k="'+f.k+'">'+f.options.map(o=>{
          const val=Array.isArray(o)?o[0]:o, lab=Array.isArray(o)?o[1]:o;
          return '<option value="'+esc(val)+'"'+(String(val)==String(v)?' selected':'')+'>'+esc(lab)+'</option>';
        }).join('')+'</select>';
      }
      else inp='<input data-k="'+f.k+'" type="'+(f.type||'text')+'" value="'+esc(v)+'">';
      return '<div class="field"><label>'+esc(f.label)+'</label>'+inp+'</div>';
    };
    if(half){ h+='<div class="frow">'+one(f)+one(nx)+'</div>'; i+=2; }
    else { h+=one(f); i+=1; }
  }
  h+='<div class="acts">'+(onDelete?'<button class="btn danger" id="mdel">Delete</button>':'')+
     '<span class="sp"></span><button class="btn ghost" id="mcancel">Cancel</button>'+
     '<button class="btn pri" id="mok">Save</button></div>';
  m.innerHTML=h; ovl.classList.add('show');
  const close=()=>ovl.classList.remove('show');
  m.querySelector('#mcancel').onclick=close;
  ovl.onclick=e=>{ if(e.target===ovl) close(); };
  if(onDelete) m.querySelector('#mdel').onclick=()=>{ close(); onDelete(); };
  m.querySelector('#mok').onclick=()=>{
    const out={};
    m.querySelectorAll('[data-k]').forEach(el=>{ out[el.dataset.k]=el.type==='number'?parseFloat(el.value||0):el.value; });
    if(fields.some(f=>f.k==='title') && !String(out.title||'').trim()){ toast('Title is required'); return; }
    close(); onSave(out);
  };
  const first=m.querySelector('input,textarea,select'); if(first) first.focus();
}
function projOptions(){ return [[ '', '— none —' ]].concat((S.projects||[]).map(p=>[p.id,p.title])); }

let NOTIF_SETTINGS=null;
async function loadNotifSettings(){
  try{ NOTIF_SETTINGS=await api('GET','/api/settings'); }
  catch(e){ NOTIF_SETTINGS={notif_enabled:'1',notif_tasks:'1',notif_meetings:'1',notif_daily_time:'08:00',notif_lead_minutes:'15'}; }
  return NOTIF_SETTINGS;
}
async function openNotifSettings(){
  const s=await loadNotifSettings();
  modal('Notifications',[
    {k:'notif_enabled',label:'Notifications',type:'select',options:[['1','On'],['0','Off']],def:s.notif_enabled,half:true},
    {k:'notif_lead_minutes',label:'Remind before meetings',type:'select',options:[['5','5 min'],['10','10 min'],['15','15 min'],['30','30 min'],['60','1 hour']],def:s.notif_lead_minutes,half:true},
    {k:'notif_meetings',label:'Meeting reminders',type:'select',options:[['1','On'],['0','Off']],def:s.notif_meetings,half:true},
    {k:'notif_tasks',label:'Daily task/meeting summary',type:'select',options:[['1','On'],['0','Off']],def:s.notif_tasks,half:true},
    {k:'notif_daily_time',label:'Daily summary time',type:'time',def:s.notif_daily_time,half:true},
  ],s,async o=>{
    const wasOff=s.notif_enabled!=='1';
    await api('POST','/api/settings',o);
    toast('Saved');
    if(wasOff&&o.notif_enabled==='1'){ try{ await api('POST','/api/notify/test',{}); toast('Test notification sent'); }catch(e){} }
  },null);
}

function subtasksToText(raw){
  let arr=[]; try{ arr=JSON.parse(raw||'[]'); }catch(e){}
  if(!Array.isArray(arr)) arr=[];
  return arr.map(s=>'['+(s.done?'x':' ')+'] '+(s.text||'')).join('\n');
}
function textToSubtasks(text){
  return JSON.stringify(String(text||'').split('\n').map(l=>l.trim()).filter(Boolean).map(l=>{
    const m=l.match(/^\[( |x|X)\]\s*(.*)$/);
    return m?{done:m[1].toLowerCase()==='x',text:m[2]}:{done:false,text:l};
  }));
}
function subtaskProgress(raw){
  let arr=[]; try{ arr=JSON.parse(raw||'[]'); }catch(e){}
  if(!Array.isArray(arr)||!arr.length) return null;
  return {done:arr.filter(s=>s.done).length, total:arr.length};
}
function taskTags(t){ return String(t.tags||'').split(',').map(s=>s.trim()).filter(Boolean); }

function editTask(t){
  t=t||{};
  const initial=Object.assign({},t,{subtasks_text:subtasksToText(t.subtasks)});
  modal(t.id?'Edit task':'New task',[
    {k:'title',label:'Title'},
    {k:'description',label:'Description',type:'textarea'},
    {k:'project_id',label:'Project',type:'select',options:projOptions(),half:true},
    {k:'task_type',label:'Type',type:'select',options:TASK_TYPES,def:'Other',half:true},
    {k:'status',label:'Status',type:'select',options:STATUSES,def:t.status||'To Do',half:true},
    {k:'priority',label:'Priority',type:'select',options:PRIORITIES,def:'Normal',half:true},
    {k:'deadline',label:'Deadline',type:'date',half:true},
    {k:'planned_date',label:'Planned work day',type:'date',half:true},
    {k:'estimate_min',label:'Estimated time (minutes)',type:'number',half:true},
    {k:'energy',label:'Energy',type:'select',options:['Low','Medium','High'],def:t.energy||'Medium',half:true},
    {k:'tags',label:'Tags (comma-separated)'},
    {k:'subtasks_text',label:'Subtasks — one per line, "[x] done" / "[ ] todo"',type:'textarea',rows:4},
  ],initial,o=>{
    o.subtasks=textToSubtasks(o.subtasks_text); delete o.subtasks_text;
    return save('tasks',o,t.id).then(()=>toast(t.id?'Task updated':'Task created'));
  },
  t.id?()=>del('tasks',t.id).then(()=>toast('Task deleted')):null);
}
function editProject(p){
  p=p||{};
  modal(p.id?'Edit project':'New project',[
    {k:'title',label:'Title'},
    {k:'description',label:'Description',type:'textarea'},
    {k:'domain',label:'Domain (e.g. Algebraic geometry)',half:true},
    {k:'status',label:'Status',type:'select',options:['In Progress','Planned','On Hold','Done'],def:'In Progress',half:true},
    {k:'deadline',label:'Deadline',type:'date'},
  ],p,o=>save('projects',o,p.id).then(()=>toast('Saved')),
  p.id?()=>del('projects',p.id).then(()=>toast('Project deleted')):null);
}
function editMeeting(m,date){
  m=m||{meeting_date:date||iso(new Date())};
  modal(m.id?'Edit event':'New event',[
    {k:'title',label:'Title'},
    {k:'meeting_date',label:'Date',type:'date',half:true},
    {k:'meeting_type',label:'Type',type:'select',options:MTG_TYPES,def:'Other',half:true},
    {k:'start_time',label:'Start',type:'time',half:true},
    {k:'end_time',label:'End',type:'time',half:true},
    {k:'recur_freq',label:'Repeats',type:'select',options:[['','Does not repeat'],['weekly','Weekly (e.g. a course)']],def:'',half:true},
    {k:'recur_until',label:'Repeat until',type:'date',half:true},
    {k:'location',label:'Location',half:true},
    {k:'project_id',label:'Project',type:'select',options:projOptions(),half:true},
    {k:'attendees',label:'Attendees'},
    {k:'notes',label:'Notes',type:'textarea'},
  ],m,o=>save('meetings',o,m.id).then(()=>toast(m.id?'Saved':(o.recur_freq==='weekly'?'Saved — repeats weekly':'Saved'))),
  m.id?()=>del('meetings',m.id).then(()=>toast('Event deleted')):null);
}
function editMilestone(ms,date){
  ms=ms||{milestone_date:date||iso(new Date())};
  modal(ms.id?'Edit milestone':'New milestone',[
    {k:'title',label:'Title'},
    {k:'description',label:'Description',type:'textarea'},
    {k:'milestone_date',label:'Date',type:'date',half:true},
    {k:'project_id',label:'Project',type:'select',options:projOptions(),half:true},
    {k:'done',label:'Done',type:'select',options:[[0,'No'],[1,'Yes']],def:0},
  ],ms,o=>save('milestones',o,ms.id).then(()=>toast('Saved')),
  ms.id?()=>del('milestones',ms.id).then(()=>toast('Milestone deleted')):null);
}
function editWin(w){
  w=w||{win_date:iso(new Date())};
  modal(w.id?'Edit win':'New win',[
    {k:'title',label:'Title'},
    {k:'win_type',label:'Type',type:'select',options:WIN_TYPES,def:'Milestone',half:true},
    {k:'win_date',label:'Date',type:'date',half:true},
    {k:'project_id',label:'Project',type:'select',options:projOptions()},
    {k:'description',label:'Description',type:'textarea'},
  ],w,o=>save('achievements',o,w.id).then(()=>toast('Saved')),
  w.id?()=>del('achievements',w.id).then(()=>toast('Deleted')):null);
}
function editBook(b){
  b=b||{};
  modal(b.id?'Edit book':'Add book',[
    {k:'title',label:'Title'},
    {k:'author',label:'Author',half:true},
    {k:'year',label:'Year',half:true},
    {k:'genre',label:'Genre',type:'select',options:GENRES,def:'Mathematics',half:true},
    {k:'rating',label:'Rating',type:'select',options:[[0,'—'],[1,'★'],[2,'★★'],[3,'★★★'],[4,'★★★★'],[5,'★★★★★']],def:0,half:true},
    {k:'read_date',label:'Read date',type:'date'},
    {k:'notes',label:'Notes',type:'textarea'},
  ],b,o=>save('books',o,b.id).then(()=>toast('Saved')),
  b.id?()=>del('books',b.id).then(()=>toast('Deleted')):null);
}
function editExpense(x){
  x=x||{exp_date:iso(new Date())};
  modal(x.id?'Edit expense':'New expense',[
    {k:'title',label:'Title'},
    {k:'amount',label:'Amount ($)',type:'number',half:true},
    {k:'category',label:'Category',type:'select',options:FIN_CATS,def:'Other',half:true},
    {k:'exp_date',label:'Date',type:'date'},
    {k:'note',label:'Note'},
  ],x,o=>save('expenses',o,x.id).then(()=>toast('Saved')),
  x.id?()=>del('expenses',x.id).then(()=>toast('Deleted')):null);
}
function editBudget(b){
  b=b||{month:DAILYDATE.slice(0,7)};
  modal(b.id?'Edit budget line':'New budget line',[
    {k:'category',label:'Category',type:'select',options:FIN_CATS,def:'Housing'},
    {k:'budgeted',label:'Budgeted ($)',type:'number',half:true},
    {k:'actual',label:'Actual ($)',type:'number',half:true},
    {k:'month',label:'Month (YYYY-MM)'},
    {k:'note',label:'Note'},
  ],b,o=>save('budget_items',o,b.id).then(()=>toast('Saved')),
  b.id?()=>del('budget_items',b.id).then(()=>toast('Deleted')):null);
}
function editDebt(d){
  d=d||{};
  modal(d.id?'Edit debt':'New debt',[
    {k:'title',label:'Title'},
    {k:'amount',label:'Amount ($)',type:'number',half:true},
    {k:'creditor',label:'Creditor',half:true},
    {k:'due_date',label:'Due date',type:'date',half:true},
    {k:'paid',label:'Paid',type:'select',options:[[0,'No'],[1,'Yes']],def:0,half:true},
    {k:'note',label:'Note'},
  ],d,o=>save('debts',o,d.id).then(()=>toast('Saved')),
  d.id?()=>del('debts',d.id).then(()=>toast('Deleted')):null);
}
function editNoteMeta(n){
  n=n||{};
  modal(n.id?'Note details':'New note',[
    {k:'title',label:'Title'},
    {k:'category',label:'Category',type:'select',options:NOTE_CATS,def:NOTECATDEFAULT,half:true},
    {k:'note_date',label:'Date',type:'date',def:iso(new Date()),half:true},
    {k:'tags',label:'Tags (comma separated)',half:true},
    {k:'project_id',label:'Project',type:'select',options:projOptions(),half:true},
  ],n,async o=>{
    if(o.category) { NOTECATDEFAULT=o.category; localStorage.setItem('m2_notecat_default',o.category); }
    if(!n.id){
      o.content='';
      const r=await mutate('POST','notes',null,o);
      NOTESEL=r.id;
      await load();
    } else {
      await save('notes',o,n.id);
    }
    toast('Saved');
  },
  n.id?()=>{NOTESEL=0; del('notes',n.id).then(()=>toast('Deleted'));}:null);
}
function getNoteMode(id){ return localStorage.getItem('m2_notemode_'+id)||'latex'; }
function setNoteModeStored(id,mode){ localStorage.setItem('m2_notemode_'+id,mode); }
function toggleNoteMode(id){
  const latex=getNoteBodyLatex(id);
  const n=(S.notes||[]).find(x=>x.id==id);
  if(n) n.content=latex;
  setNoteModeStored(id, getNoteMode(id)==='word'?'latex':'word');
  mutate('PUT','notes',id,{content:latex});
  render();
  compileNotePdf(id,false);
}
function getNoteBodyLatex(id){
  const el=document.getElementById('notebody');
  if(!el){ const n=(S.notes||[]).find(x=>x.id==id); return n?(n.content||''):''; }
  if(el.tagName==='TEXTAREA') return el.value;
  return htmlToLatex(el);
}
function escLatexText(s){
  const map={'\\':'\\textbackslash{}','%':'\\%','&':'\\&','#':'\\#','_':'\\_','{':'\\{','}':'\\}','~':'\\textasciitilde{}','^':'\\textasciicircum{}'};
  return String(s).replace(/[\\%&#_{}~^]/g, ch=>map[ch]);
}
function unescLatexPlain(s){
  return String(s)
    .replace(/\\textbackslash\{\}/g,'\\')
    .replace(/\\textasciitilde\{\}/g,'~')
    .replace(/\\textasciicircum\{\}/g,'^')
    .replace(/\\([%&#_{}])/g,'$1');
}
function escLatexInline(s){
  const plain=unescLatexPlain(String(s));
  return plain.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function cssColorToLatex(c){
  c=(c||'').trim();
  const named={'#c0392b':'red','red':'red','rgb(192, 57, 43)':'red','#f5e28a':'yellow','yellow':'yellow'};
  if(named[c.toLowerCase()]) return named[c.toLowerCase()];
  const m=c.match(/^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/);
  if(m){
    const hex='#'+[m[1],m[2],m[3]].map(v=>(+v).toString(16).padStart(2,'0')).join('');
    return named[hex]||hex.replace('#','');
  }
  return c.replace('#','').replace(/[^a-zA-Z0-9]/g,'')||'black';
}

function latexToHtml(src){
  src=src||'';
  const chips=[];
  const protect=(s)=>{ const i=chips.length; chips.push(s); return '\u0001CHIP'+i+'\u0001'; };
  src=src.replace(/\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\$[^$\n]+?\$|\\\([\s\S]+?\\\)/g, m=>protect(m));
  src=src.replace(/\\begin\{(equation\*?|align\*?|pmatrix|bmatrix|matrix)\}[\s\S]*?\\end\{\1\}/g, m=>protect(m));
  src=src.replace(/\\begin\{verbatim\}([\s\S]*?)\\end\{verbatim\}/g, (m,body)=>'\u0002PRE\u0002'+escHtml(body)+'\u0002/PRE\u0002');
  src=src.replace(/\\noindent\\rule\{\\linewidth\}\{[^}]*\}/g, ()=>'\u0002HR\u0002');
  src=src.replace(/\\newpage/g, ()=>'\u0002PB\u0002');
  src=src.replace(/\\footnote\{((?:[^{}]|\{[^{}]*\})*)\}/g, (m,body)=>'\u0002FN\u0002'+escHtmlAttr(unescLatexPlain(body))+'\u0002/FN\u0002');

  const inlineFmt=(text)=>{
    let t=text;
    for(let pass=0; pass<3; pass++){
      t=t.replace(/\\textbf\{((?:[^{}]|\{[^{}]*\})*)\}/g,'<b>$1</b>');
      t=t.replace(/\\textit\{((?:[^{}]|\{[^{}]*\})*)\}/g,'<i>$1</i>');
      t=t.replace(/\\underline\{((?:[^{}]|\{[^{}]*\})*)\}/g,'<u>$1</u>');
      t=t.replace(/\\sout\{((?:[^{}]|\{[^{}]*\})*)\}/g,'<s>$1</s>');
      t=t.replace(/\\textsuperscript\{((?:[^{}]|\{[^{}]*\})*)\}/g,'<sup>$1</sup>');
      t=t.replace(/\\textsubscript\{((?:[^{}]|\{[^{}]*\})*)\}/g,'<sub>$1</sub>');
      t=t.replace(/\\textcolor\{([a-zA-Z0-9#]+)\}\{((?:[^{}]|\{[^{}]*\})*)\}/g,'<span style="color:$1">$2</span>');
      t=t.replace(/\\colorbox\{([a-zA-Z0-9#]+)\}\{((?:[^{}]|\{[^{}]*\})*)\}/g,'<span style="background:$1">$2</span>');
      t=t.replace(/\\href\{([^}]*)\}\{((?:[^{}]|\{[^{}]*\})*)\}/g,'<a href="$1">$2</a>');
    }
    return t;
  };

  const lines=src.split('\n');
  let html=''; const stack=[]; let para=[];
  const flushPara=()=>{ if(para.length){ html+='<p>'+para.join('<br>')+'</p>'; para=[]; } };
  for(const raw of lines){
    const line=raw;
    let m;
    if(/^\s*\\begin\{itemize\}\s*$/.test(line)){ flushPara(); html+='<ul>'; stack.push('itemize'); continue; }
    if(/^\s*\\begin\{enumerate\}\s*$/.test(line)){ flushPara(); html+='<ol>'; stack.push('enumerate'); continue; }
    if(/^\s*\\end\{itemize\}\s*$/.test(line)){ html+='</ul>'; stack.pop(); continue; }
    if(/^\s*\\end\{enumerate\}\s*$/.test(line)){ html+='</ol>'; stack.pop(); continue; }
    if(/^\s*\\begin\{quote\}\s*$/.test(line)){ flushPara(); html+='<blockquote>'; stack.push('quote'); continue; }
    if(/^\s*\\end\{quote\}\s*$/.test(line)){ html+='</blockquote>'; stack.pop(); continue; }
    if(/^\s*\\begin\{center\}\s*$/.test(line)){ flushPara(); html+='<div style="text-align:center">'; stack.push('center'); continue; }
    if(/^\s*\\end\{center\}\s*$/.test(line)){ html+='</div>'; stack.pop(); continue; }
    if(/^\s*\\begin\{tabular\}\{[^}]*\}\s*$/.test(line)){ flushPara(); html+='<table class="notewordtable"><tbody>'; stack.push('tabular'); continue; }
    if(/^\s*\\end\{tabular\}\s*$/.test(line)){ html+='</tbody></table>'; stack.pop(); continue; }
    if(stack[stack.length-1]==='tabular'){
      if(/^\s*\\hline\s*$/.test(line)) continue;
      if(!line.trim()) continue;
      const cells=line.replace(/\\\\\s*$/,'').split('&').map(c=>c.trim());
      html+='<tr>'+cells.map(c=>'<td>'+inlineFmt(escLatexInline(c))+'</td>').join('')+'</tr>';
      continue;
    }
    if(stack[stack.length-1]==='itemize' || stack[stack.length-1]==='enumerate'){
      if((m=line.match(/^\s*\\item\s?(.*)$/))){ html+='<li>'+inlineFmt(escLatexInline(m[1]))+'</li>'; continue; }
      if(!line.trim()) continue;
      html+='<li>'+inlineFmt(escLatexInline(line.trim()))+'</li>'; continue;
    }
    if((m=line.match(/^\s*\\section\{((?:[^{}]|\{[^{}]*\})*)\}\s*$/))){ flushPara(); html+='<h1>'+inlineFmt(escLatexInline(m[1]))+'</h1>'; continue; }
    if((m=line.match(/^\s*\\subsection\{((?:[^{}]|\{[^{}]*\})*)\}\s*$/))){ flushPara(); html+='<h2>'+inlineFmt(escLatexInline(m[1]))+'</h2>'; continue; }
    if((m=line.match(/^\s*\\subsubsection\{((?:[^{}]|\{[^{}]*\})*)\}\s*$/))){ flushPara(); html+='<h3>'+inlineFmt(escLatexInline(m[1]))+'</h3>'; continue; }
    if(stack[stack.length-1]==='quote' || stack[stack.length-1]==='center'){
      if(!line.trim()) continue;
      html+=inlineFmt(escLatexInline(line))+'<br>'; continue;
    }
    if(!line.trim()){ flushPara(); continue; }
    para.push(inlineFmt(escLatexInline(line)));
  }
  flushPara();
  while(stack.length){
    const e=stack.pop();
    html+= e==='itemize'?'</ul>': e==='enumerate'?'</ol>': e==='quote'?'</blockquote>': e==='center'?'</div>': e==='tabular'?'</tbody></table>':'';
  }

  html=html.replace(/\u0001CHIP(\d+)\u0001/g, (m,i)=>'<span class="latexmath" contenteditable="false">'+escHtml(chips[+i])+'</span>');
  html=html.replace(/\u0002PRE\u0002([\s\S]*?)\u0002\/PRE\u0002/g, (m,body)=>'<pre>'+body+'</pre>');
  html=html.replace(/\u0002HR\u0002/g, '<hr>');
  html=html.replace(/\u0002PB\u0002/g, '<div class="notewordbreak" contenteditable="false">····· page break ·····</div>');
  html=html.replace(/\u0002FN\u0002([\s\S]*?)\u0002\/FN\u0002/g, (m,body)=>'<sup class="notewordfn" contenteditable="false" title="'+body+'">[fn]</sup>');
  return html || '<p><br></p>';
}

function collectTag(el,tagName){
  let out=[];
  for(const c of el.childNodes){
    if(c.nodeType!==1) continue;
    if(c.tagName.toLowerCase()===tagName) out.push(c);
    else out=out.concat(collectTag(c,tagName));
  }
  return out;
}
function htmlToLatex(root){
  function walk(node){
    let out='';
    for(const child of node.childNodes){
      if(child.nodeType===3){ out+=escLatexText(child.textContent); continue; }
      if(child.nodeType!==1) continue;
      const tag=child.tagName.toLowerCase();
      const cls=(child.className||'')+'';
      if(cls.indexOf('latexmath')>=0){ out+=child.textContent; continue; }
      if(cls.indexOf('notewordbreak')>=0){ out+='\n\\newpage\n'; continue; }
      if(cls.indexOf('notewordfn')>=0){ out+='\\footnote{'+escLatexText(child.getAttribute('title')||'')+'}'; continue; }
      const inner=()=>walk(child);
      switch(tag){
        case 'b': case 'strong': out+='\\textbf{'+inner()+'}'; break;
        case 'i': case 'em': out+='\\textit{'+inner()+'}'; break;
        case 'u': out+='\\underline{'+inner()+'}'; break;
        case 's': case 'strike': case 'del': out+='\\sout{'+inner()+'}'; break;
        case 'sup': out+='\\textsuperscript{'+inner()+'}'; break;
        case 'sub': out+='\\textsubscript{'+inner()+'}'; break;
        case 'h1': out+='\\section{'+inner()+'}\n'; break;
        case 'h2': out+='\\subsection{'+inner()+'}\n'; break;
        case 'h3': out+='\\subsubsection{'+inner()+'}\n'; break;
        case 'blockquote': out+='\\begin{quote}\n'+inner()+'\n\\end{quote}\n'; break;
        case 'ul': out+='\\begin{itemize}\n'+listItems(child)+'\\end{itemize}\n'; break;
        case 'ol': out+='\\begin{enumerate}\n'+listItems(child)+'\\end{enumerate}\n'; break;
        case 'a': out+='\\href{'+(child.getAttribute('href')||'')+'}{'+inner()+'}'; break;
        case 'hr': out+='\\noindent\\rule{\\linewidth}{0.4pt}\n'; break;
        case 'br': out+='\n'; break;
        case 'pre': out+='\\begin{verbatim}\n'+child.textContent+'\n\\end{verbatim}\n'; break;
        case 'table': out+='\\begin{tabular}{'+tableCols(child)+'}\n\\hline\n'+tableRows(child)+'\\hline\n\\end{tabular}\n'; break;
        case 'div': case 'p':
          if(child.style && child.style.textAlign==='center') out+='\\begin{center}\n'+inner()+'\n\\end{center}\n';
          else { const t=inner(); if(t.trim()) out+=t+'\n\n'; }
          break;
        case 'span': {
          const color=(child.style&&child.style.color)||'';
          const bg=(child.style&&(child.style.background||child.style.backgroundColor))||'';
          if(color) out+='\\textcolor{'+cssColorToLatex(color)+'}{'+inner()+'}';
          else if(bg) out+='\\colorbox{'+cssColorToLatex(bg)+'}{'+inner()+'}';
          else out+=inner();
          break;
        }
        default: out+=inner();
      }
    }
    return out;
  }
  function listItems(listEl){
    let out='';
    for(const li of listEl.childNodes){
      if(li.nodeType===1 && li.tagName.toLowerCase()==='li') out+='  \\item '+walk(li)+'\n';
    }
    return out;
  }
  function tableCols(tableEl){
    const rows=collectTag(tableEl,'tr');
    let n=1;
    if(rows.length) n=Array.prototype.filter.call(rows[0].childNodes,c=>c.nodeType===1).length||1;
    return Array(Math.max(n,1)).fill('l').join('|');
  }
  function tableRows(tableEl){
    let out='';
    collectTag(tableEl,'tr').forEach(tr=>{
      const cells=Array.prototype.filter.call(tr.childNodes,c=>c.nodeType===1).map(td=>walk(td).trim());
      out+=cells.join(' & ')+' \\\\\n';
    });
    return out;
  }
  return walk(root).replace(/\n{3,}/g,'\n\n').trim();
}

const NOTE_TOOLS=[
  {label:'B',title:'Bold',before:'\\textbf{',after:'}',placeholder:'bold text',cls:'tb-b',wys:{cmd:'bold'}},
  {label:'I',title:'Italic',before:'\\textit{',after:'}',placeholder:'italic text',cls:'tb-i',wys:{cmd:'italic'}},
  {label:'U',title:'Underline',before:'\\underline{',after:'}',placeholder:'underlined text',cls:'tb-u',wys:{cmd:'underline'}},
  {label:'S',title:'Strikethrough',before:'\\sout{',after:'}',placeholder:'struck text',cls:'tb-s',wys:{cmd:'strikeThrough'}},
  {label:'x²',title:'Superscript',before:'\\textsuperscript{',after:'}',placeholder:'sup',wys:{cmd:'superscript'}},
  {label:'x₂',title:'Subscript',before:'\\textsubscript{',after:'}',placeholder:'sub',wys:{cmd:'subscript'}},
  {label:'<span style="color:#c0392b">A</span>',title:'Text color',before:'\\textcolor{red}{',after:'}',placeholder:'colored text',wys:{cmd:'foreColor',val:'#c0392b'}},
  {label:'<span style="background:#f5e28a;padding:0 2px;border-radius:2px">H</span>',title:'Highlight',before:'\\colorbox{yellow}{',after:'}',placeholder:'highlighted text',wys:{cmd:'hiliteColor',val:'#f5e28a'}},
  {sep:true},
  {label:'H1',title:'Section heading',before:'\\section{',after:'}',placeholder:'Heading',line:true,wys:{cmd:'formatBlock',val:'H1'}},
  {label:'H2',title:'Subsection heading',before:'\\subsection{',after:'}',placeholder:'Subheading',line:true,wys:{cmd:'formatBlock',val:'H2'}},
  {label:'H3',title:'Subsubsection heading',before:'\\subsubsection{',after:'}',placeholder:'Subheading',line:true,wys:{cmd:'formatBlock',val:'H3'}},
  {label:'❝',title:'Quote',block:['\\begin{quote}','','\\end{quote}'],placeholder:'Quoted text',wys:{cmd:'formatBlock',val:'BLOCKQUOTE'}},
  {label:'Ctr',title:'Center align',block:['\\begin{center}','','\\end{center}'],placeholder:'Centered text',wys:{cmd:'justifyCenter'}},
  {sep:true},
  {label:'•',title:'Bullet list',block:['\\begin{itemize}','  \\item ','\\end{itemize}'],placeholder:'item',wys:{cmd:'insertUnorderedList'}},
  {label:'1.',title:'Numbered list',block:['\\begin{enumerate}','  \\item ','\\end{enumerate}'],placeholder:'item',wys:{cmd:'insertOrderedList'}},
  {sep:true},
  {label:'∑',title:'Equation',block:['\\begin{equation}','','\\end{equation}'],placeholder:'a^2+b^2=c^2',wys:{atomic:true}},
  {label:'Align',title:'Aligned equations',block:['\\begin{align}','','\\end{align}'],placeholder:'a &= b \\\\\nc &= d',wys:{atomic:true}},
  {label:'Mtx',title:'Matrix',before:'$\\begin{pmatrix} ',after:' \\end{pmatrix}$',placeholder:'a & b \\\\ c & d',wys:{atomic:true}},
  {sep:true},
  {label:'⊞',title:'Table',raw:'\\begin{tabular}{|l|l|}\n\\hline\nHeader 1 & Header 2 \\\\\n\\hline\nRow 1 & Row 1 \\\\\nRow 2 & Row 2 \\\\\n\\hline\n\\end{tabular}\n',wys:{table:true}},
  {label:'&lt;/&gt;',title:'Code block',block:['\\begin{verbatim}','','\\end{verbatim}'],placeholder:'code here',wys:{cmd:'formatBlock',val:'PRE'}},
  {label:'―',title:'Horizontal rule',raw:'\\noindent\\rule{\\linewidth}{0.4pt}\n',wys:{cmd:'insertHorizontalRule'}},
  {label:'PB',title:'Page break',raw:'\\newpage\n',wys:{pagebreak:true}},
  {label:'fn¹',title:'Footnote',before:'\\footnote{',after:'}',placeholder:'footnote text',wys:{footnote:true}},
  {label:'🔗',title:'Link',before:'\\href{https://}{',after:'}',placeholder:'link text',wys:{link:true}},
];
function escHtml(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escHtmlAttr(s){ return escHtml(s).replace(/"/g,'&quot;'); }
function insertAtomicChip(src){
  document.execCommand('insertHTML',false,
    '<span class="latexmath" contenteditable="false">'+escHtml(src)+'</span>&nbsp;');
}
function wysToolbarAction(id,el,t){
  el.focus();
  const w=t.wys;
  if(!w) return;
  if(w.cmd==='formatBlock'){
    document.execCommand('formatBlock',false,'<'+w.val+'>');
  } else if(w.cmd==='foreColor'||w.cmd==='hiliteColor'){
    try{ document.execCommand(w.cmd,false,w.val); }
    catch(err){ document.execCommand('backColor',false,w.val); }
  } else if(w.cmd){
    document.execCommand(w.cmd,false,null);
  } else if(w.atomic){
    const body=t.placeholder||'';
    const src=t.block?(t.block[0]+'\n'+t.block[1]+body+'\n'+t.block[2]):((t.before||'')+body+(t.after||''));
    insertAtomicChip(src);
  } else if(w.table){
    document.execCommand('insertHTML',false,
      '<table class="notewordtable"><tbody><tr><td>Header 1</td><td>Header 2</td></tr>'+
      '<tr><td>Row 1</td><td>Row 1</td></tr><tr><td>Row 2</td><td>Row 2</td></tr></tbody></table><p><br></p>');
  } else if(w.pagebreak){
    document.execCommand('insertHTML',false,
      '<div class="notewordbreak" contenteditable="false">····· page break ·····</div><p><br></p>');
  } else if(w.footnote){
    const txt=prompt('Footnote text:','');
    if(txt) document.execCommand('insertHTML',false,
      '<sup class="notewordfn" contenteditable="false" title="'+escHtmlAttr(txt)+'">[fn]</sup>');
  } else if(w.link){
    const url=prompt('Link URL:','https://');
    if(url){
      const sel=window.getSelection();
      if(sel && !sel.isCollapsed) document.execCommand('createLink',false,url);
      else document.execCommand('insertHTML',false,'<a href="'+escHtmlAttr(url)+'">'+escHtmlAttr(url)+'</a>');
    }
  }
  onNoteWordInput(id);
}
function noteToolbarClick(id,idx){
  const t=NOTE_TOOLS[idx];
  if(!t||t.sep) return;
  const el=document.getElementById('notebody');
  if(!el) return;
  if(getNoteMode(id)==='word'){ wysToolbarAction(id,el,t); return; }
  const ta=el;
  const s=ta.selectionStart, e=ta.selectionEnd;
  const val=ta.value;
  const sel=val.slice(s,e);
  let insert, selStart, selEnd;
  if(t.raw){
    insert=t.raw;
    selStart=selEnd=s+insert.length;
  } else if(t.block){
    const body=sel||t.placeholder;
    insert=t.block[0]+'\n'+t.block[1]+body+'\n'+t.block[2];
    selStart=s+t.block[0].length+1+t.block[1].length;
    selEnd=selStart+body.length;
  } else {
    const body=sel||t.placeholder;
    insert=t.before+body+t.after;
    selStart=s+t.before.length;
    selEnd=selStart+body.length;
  }
  ta.value=val.slice(0,s)+insert+val.slice(e);
  ta.focus();
  ta.setSelectionRange(selStart,selEnd);
  onNoteInput(id);
}
function quickNew(){
  if(VIEW==='board'||VIEW==='dash') editTask();
  else if(VIEW==='projects') editProject();
  else if(VIEW==='agenda') editMeeting(null,AGSEL);
  else if(VIEW==='daily') editDaily();
  else if(VIEW==='analytics'||VIEW==='matrix') editTask();
  else if(VIEW==='notes') editNoteMeta();
  else if(VIEW==='wins') editWin();
  else if(VIEW==='books') editBook();
  else if(VIEW==='finance'){ FINTAB==='budget'?editBudget():FINTAB==='debts'?editDebt():editExpense(); }
  else if(VIEW==='ts'){ TSTAB==='journal'?editJournalEntry():TSTAB==='mood'?editMoodEntry():editGoal(); }
  else if(VIEW==='inbox') openPalette('+ ');
  else if(VIEW==='planner'||VIEW==='review') editTask({planned_date:PLANDATE});
  else if(VIEW==='timeline') editMilestone(null,iso(new Date()));
  else if(VIEW==='radar') radarSettings();
  else if(VIEW==='biblio') editRef();
  else if(VIEW==='results') editResult({project_id:RESPROJ||''});
  else if(VIEW==='graph') editNoteMeta();
  else if(VIEW==='vanolib'){ VL_TAB='overview'; render(); setTimeout(()=>{const q=document.getElementById('vlQ'); if(q) q.focus();},50); }
}

const TITLES={dash:'Dashboard',board:'Tasks',projects:'Projects',agenda:'Meetings',daily:'Daily Tasks',analytics:'Productivity Analytics',matrix:'Priority Matrix',notes:'Notes',wins:'Achievements',books:'Library',finance:'Budget',ts:'Goals'};
function setView(v){ if(typeof stopDictation==='function') stopDictation(); VIEW=v; localStorage.setItem('m2view',v); render(); }
function render(){
  document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('on',b.dataset.v===VIEW));
  document.getElementById('vtitle').textContent=TITLES[VIEW];
  const el=document.getElementById('view');
  ({dash:rDash,board:rBoard,projects:rProjects,agenda:rAgenda,daily:rDaily,analytics:rAnalytics,matrix:rMatrix,
    notes:rNotes,wins:rWins,books:rBooks,finance:rFinance,ts:rTS,
    inbox:rInbox,planner:rPlanner,timeline:rTimeline,radar:rRadar,biblio:rBiblio,results:rResults,review:rReview,vanolib:rVanolib,vanonote:rVanonote,vanoleaf:rVanoleaf})[VIEW](el);
  if(VIEW==='daily'){ try{ el.insertAdjacentHTML('afterbegin',plCard()); }catch(e){ console.error(e); } }
  if(VIEW==='dash'){ try{ const hb=el.querySelector('.hero-banner'); const strip=v6DashStrip(); if(hb) hb.insertAdjacentHTML('afterend',strip); else el.insertAdjacentHTML('afterbegin',strip); }catch(e){ console.error(e); } }
  if(window._vaLastView!==VIEW){ window._vaLastView=VIEW; el.classList.remove('viewenter','va-stagger'); void el.offsetWidth; el.classList.add('viewenter','va-stagger'); }
}
function match(s){ return !Q || String(s||'').toLowerCase().includes(Q); }


function vaSetName(){ const n=prompt('Your first name (shown on the dashboard):', APP_USER||''); if(n===null) return; try{ localStorage.setItem('va_name', n.trim()); }catch(e){} location.reload(); }
const APP_USER=(()=>{ try{ return localStorage.getItem('va_name')||''; }catch(e){ return ''; } })();
const DEMO_THEMES={
  terracotta:{label:'Terracotta', mood:'Warm · Clear · Focused', quote:'Think clearly, write simply.', accent:'◆'},
  ember:{label:'Ember', mood:'Dark · Warm · Quiet', quote:'Slow fire, deep work.', accent:'◆'},
  aurora:{label:'Aurora', mood:'Modern · Elegant · Inspiring', quote:'Discipline today, freedom tomorrow.', accent:'✦'},
  autumn:{label:'Autumn', mood:'Warm · Natural · Focused', quote:'Consistency turns small steps into real progress.', accent:'🍂'},
  cyber:{label:'Cyber / Matrix', mood:'High-tech · Futuristic · Focused', quote:'Turn ideas into reality.', accent:'⌁'},
  ivory:{label:'Ivory', mood:'Calm · Editorial · Clear', quote:'Clarity is a form of kindness.', accent:'✧'},
  midnight:{label:'Midnight', mood:'Dark · Refined · Quiet', quote:'Deep work, quiet mind.', accent:'☾'},
  ocean:{label:'Ocean', mood:'Deep · Fluid · Serene', quote:'Still water runs deep.', accent:'≋'},
  sakura:{label:'Sakura', mood:'Soft · Light · Gentle', quote:'Small blossoms, every day.', accent:'✿'},
  nord:{label:'Nord', mood:'Cool · Clean · Precise', quote:'Simple is hard; that is the point.', accent:'❄'},
  forest:{label:'Forest', mood:'Grounded · Natural · Calm', quote:'Grow slowly, root deeply.', accent:'❦'},
  printemps:{label:'Spring', mood:'Petals · Soft · Bright', quote:'A calm space to focus.', accent:'✿'},
  ete:{label:'Summer', mood:'Fireflies · Warm · Golden', quote:'Light comes slowly.', accent:'☀'},
  automne:{label:'Fall', mood:'Leaves · Warm · Calm', quote:'Let go of what no longer serves.', accent:'❧'},
  hiver:{label:'Winter', mood:'Snow · Night blue · Silent', quote:'Silence helps thinking.', accent:'❄'}
};
function activeTheme(){ let t=localStorage.getItem('m2theme')||'aurora'; if(t==='minimal') t='autumn'; return DEMO_THEMES[t]?t:'aurora'; }
function partOfDay(){ const h=new Date().getHours(); return h<12?'morning':h<18?'afternoon':'evening'; }
function themeSwitch(next){ const go=()=>{ const r=document.documentElement.style; r.removeProperty('--acc'); r.removeProperty('--acc2'); try{ localStorage.removeItem('m2accent'); }catch(e){} applyTheme(next); render(); }; if(document.startViewTransition&&!matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(go); else go(); }
let DASH_TASK_FILTER='All';
function setDashTaskFilter(v){DASH_TASK_FILTER=v;render();}
function setAccentColor(c){document.documentElement.style.setProperty('--acc',c);document.documentElement.style.setProperty('--acc2',c);localStorage.setItem('m2accent',c);}
const _savedAccent=localStorage.getItem('m2accent');if(_savedAccent)setAccentColor(_savedAccent);

const DASH_BLOCKS=['weekly','deadlines','today','projects'];
const DASH_LABELS={weekly:'Weekly effort',deadlines:'Deadlines',today:'Today',projects:'Projects'};
function dashHidden(){ try{ return JSON.parse(localStorage.getItem('m2_dash_hidden')||'[]'); }catch(e){ return []; } }
function dashVisible(id){ return !dashHidden().includes(id); }
function toggleDashBlock(id){
  let h=dashHidden();
  h=h.includes(id)?h.filter(x=>x!==id):h.concat(id);
  localStorage.setItem('m2_dash_hidden',JSON.stringify(h));
  render();
}
let DASH_CUSTOMIZE=false;
function toggleDashCustomize(){ DASH_CUSTOMIZE=!DASH_CUSTOMIZE; render(); }
async function quickCapture(){
  const sel=document.getElementById('captureType'), input=document.getElementById('captureInput');
  const type=sel?sel.value:'task', v=input?input.value.trim():'';
  if(!v) return;
  input.value='';
  if(type==='task') await save('tasks',{title:v,status:'To Do',priority:'Normal',task_type:'Other'});
  else if(type==='note') await save('notes',{title:v,content:'',category:NOTECATDEFAULT});
  else if(type==='win') await save('achievements',{title:v,win_type:'Milestone',win_date:iso(new Date())});
  toast('Added');
  input.focus();
}

function rDash(el){
  const T=S.tasks||[], M=S.meetings||[], N=S.notes||[], P=S.projects||[], W=S.achievements||[];
  const today=iso(new Date());
  const theme=activeTheme(), meta=DEMO_THEMES[theme];
  const activeTasks=T.filter(t=>t.status!=='Done');
  const todayPool=T.filter(t=>((t.status!=='Done')&&((t.planned_date||'')===today||(t.deadline&&daysTo(t.deadline)<=0)||(!t.planned_date&&!t.deadline))) || (t.status==='Done'&&String(t.completed_at||'').slice(0,10)===today));
  const tasksToday=todayPool.filter(t=>DASH_TASK_FILTER==='All'?(t.status!=='Done'||String(t.completed_at||'').slice(0,10)===today):t.status===DASH_TASK_FILTER).slice(0,5);
  const meetings=agendaItems().filter(x=>x.kind==='meeting' && x.date>=today)
      .sort((a,b)=>(a.date+a.time).localeCompare(b.date+b.time)).slice(0,4);
  const notes=(N.slice().sort((a,b)=>(b.updated_at||b.created_at||'').localeCompare(a.updated_at||a.created_at||''))).slice(0,4);
  const done=T.filter(t=>t.status==='Done').length;
  const projPct=P.length?Math.round(P.map(p=>{const pt=T.filter(t=>t.project_id==p.id); return pt.length?100*pt.filter(t=>t.status==='Done').length/pt.length:0;}).reduce((a,b)=>a+b,0)/P.length):0;
  const daily=(S.daily_tasks||[]).filter(d=>d.task_date===today);
  const dailyDone=daily.filter(d=>d.done).length;
  const streak = dailyDone || activeTasks.length ? Math.max(1, Math.min(7, dailyDone + Math.min(5, done?1:0) + (W.length?1:0))) : 0;
  const prgTasks=T.length?Math.round(100*done/Math.max(1,T.length)):0;
  const pad = s=>esc(String(s||''));
  const meetHtml = meetings.length ? meetings.map(m=>`<div class="meetline" style="cursor:pointer" onclick="editMeeting(S.meetings.find(x=>x.id==${m.src.id}))"><div style="width:42px;font-size:13px;color:var(--txt);padding-top:2px">${pad(m.time||'09:00')}</div><div><div>${pad(m.title.replace(/^↻\s*/,''))}</div><div class="meta">${pad(m.date===today?'Today':fmtd(m.date))}${m.loc?' · '+pad(m.loc):''}</div></div><div class="soft-badge" style="background:var(--tealbg);color:var(--teal)">Event</div></div>`).join('') : '<div class="empty">No upcoming meetings</div>';
  const tasksHtml = tasksToday.length ? tasksToday.map(t=>{
      const p=String(t.priority||'Normal');
      const tone=p==='High'?['var(--redbg)','var(--red)']:p==='Low'?['var(--greenbg)','var(--green)']:['var(--amberbg)','var(--amber)'];
      return `<div class="taskline" style="cursor:pointer" onclick="editTask(S.tasks.find(x=>x.id==${t.id}))"><div class="ck"></div><div><div>${pad(t.title)}</div><div class="meta">${pad(projName(t.project_id)||t.task_type||'General')}${t.deadline?' · '+pad(fmtd(t.deadline)):''}${t.estimate_min?' · ~'+fmtDur(t.estimate_min):''}</div></div><div class="priority-badge" style="background:${tone[0]};color:${tone[1]}">${pad(p)}</div></div>`;
    }).join('') : '<div class="empty">No tasks yet</div>';
  const notesHtml = notes.length ? notes.map(n=>`<div class="noteline" style="cursor:pointer" onclick="NOTESEL=${n.id};setView('notes')"><div style="width:14px;height:14px;border-radius:4px;background:var(--accbg);margin-top:3px"></div><div><div>${pad(n.title||'Untitled')}</div><div class="meta">${pad((n.tags||'').split(',').filter(Boolean).slice(0,2).map(x=>'#'+x.trim()).join(' '))||pad(n.category||'Note')}</div></div><div class="meta">${pad(fmtd((n.note_date||n.updated_at||n.created_at||'').slice(0,10)))}</div></div>`).join('') : '<div class="empty">No notes yet</div>';
  const themePreview = key => `<div class="theme-preview ${theme===key?'on':''}" data-theme-preview="${key}" onclick="themeSwitch('${key}')"><div class="theme-art"></div><div class="lab"><div style="font-weight:700">${pad(DEMO_THEMES[key].label)}</div><div class="demo-note">${pad(DEMO_THEMES[key].mood)}</div></div></div>`;
  const DQ=dashQuotes();
  el.innerHTML = `
    <div class="ydash">
      <section class="hero-banner">
        <div class="hero-copy">
          <h1>Good ${partOfDay()}${APP_USER?', '+esc(APP_USER):''} ${meta.accent}</h1>
          <p>“${pad(DQ[0].text)}” <span class="vq-a">— ${pad(DQ[0].author)}</span>${qActs(DQ[0],0)}</p>
          <div class="hero-line"></div>
        </div>
        <div class="hero-date">
          <div class="d1">${new Date().toLocaleDateString('en-US',{weekday:'long'})}</div>
          <div class="d2">${new Date().toLocaleDateString('en-US',{month:'long', day:'numeric', year:'numeric'})}</div>
          <div class="q">“${pad(DQ[1].text)}” <span class="vq-a">— ${pad(DQ[1].author)}</span>${qActs(DQ[1],1)}</div>
        </div>
      </section>

      <div class="kpi-grid">
        <div class="show-kpi"><div class="ico">📁</div><div><b>${P.length}</b><span>Active Projects</span></div></div>
        <div class="show-kpi"><div class="ico">☑️</div><div><b>${tasksToday.length}</b><span>Tasks Today</span></div></div>
        <div class="show-kpi"><div class="ico">📅</div><div><b>${meetings.length}</b><span>Upcoming Meetings</span></div></div>
        <div class="show-kpi"><div class="ico">🏆</div><div><b>${W.length}</b><span>Recent Wins</span></div></div>
      </div>

      <div class="dashboard-grid">
        <div class="dashboard-stack">
          <section class="show-card">
            <div class="head"><h3>Today's Tasks</h3><button class="theme-mini on" onclick="setView('board')">Open Tasks</button></div>
            <div class="filter-pills" style="margin-bottom:12px">${['All','To Do','In Progress','Done'].map(f=>`<button class="${DASH_TASK_FILTER===f?'on':''}" onclick="setDashTaskFilter('${f}')">${f}</button>`).join('')}</div>
            ${tasksHtml}
          </section>
          <section class="show-card">
            <div class="head"><h3>Upcoming</h3><button class="theme-mini" onclick="setView('agenda')">View all</button></div>
            ${meetHtml}
          </section>
        </div>

        <div class="right-rail">
          <section class="show-card quote-card">
            <h3>Focus</h3>
            <div class="quote">“${pad(DQ[2].text)}”<div class="vq-a" style="margin-top:6px">— ${pad(DQ[2].author)}</div></div>${qActs(DQ[2],2)}
          </section>
          <section class="show-card">
            <div class="head"><h3>Quick Notes</h3><button class="theme-mini" onclick="setView('notes')">Open</button></div>
            ${notesHtml}
          </section>
          <section class="show-card">
            <div class="head"><h3>Progress Overview</h3><div class="demo-note">This week</div></div>
            <div class="progress-bars">
              <div class="progress-row"><div class="label"><span>Tasks Completed</span><span>${done}/${T.length}</span></div><div class="pbar"><i style="width:${prgTasks}%"></i></div></div>
              <div class="progress-row"><div class="label"><span>Project Progress</span><span>${projPct}%</span></div><div class="pbar"><i style="width:${projPct}%;background:linear-gradient(90deg,var(--acc),var(--pink))"></i></div></div>
              <div class="progress-row"><div class="label"><span>Daily Streak</span><span>${streak}/7</span></div><div class="pbar"><i style="width:${Math.round((streak/7)*100)}%;background:linear-gradient(90deg,var(--amber),var(--pink))"></i></div></div>
              <div class="demo-note">Routine done today: ${dailyDone}/${daily.length || 0}</div>
            </div>
          </section>
        </div>
      </div>

      <div class="theme-strip">
        <section class="tstrip-card" style="grid-column:span 2">
          <h4>Themes</h4>
          <div class="theme-previews">${THEMES.map(themePreview).join('')}</div>
        </section>
        <section class="tstrip-card">
          <h4>Customizable Colors</h4>
          <div class="swatches">
            <button class="swatch" onclick="setAccentColor('#a855f7')" title="Use #a855f7" style="background:#a855f7"></button><button class="swatch" onclick="setAccentColor('#22d3ee')" title="Use #22d3ee" style="background:#22d3ee"></button><button class="swatch" onclick="setAccentColor('#f472b6')" title="Use #f472b6" style="background:#f472b6"></button><button class="swatch" onclick="setAccentColor('#facc15')" title="Use #facc15" style="background:#facc15"></button><button class="swatch" onclick="setAccentColor('#34d399')" title="Use #34d399" style="background:#34d399"></button><button class="swatch" onclick="setAccentColor('#60a5fa')" title="Use #60a5fa" style="background:#60a5fa"></button>
          </div>
          <div class="demo-note" style="margin-top:10px">Primary color adapts automatically to the active theme.</div>
        </section>
        <section class="tstrip-card">
          <h4>Zen Mode</h4>
          <div class="demo-note">Hide distractions and stay focused.</div>
          <button class="zenbtn" onclick="openFocusMode()">Activate Focus / Zen Mode</button>
        </section>
        <section class="tstrip-card">
          <h4>Fluid Animations</h4>
          <div class="demo-note">Smooth transitions · hover effects · micro-animations</div>
          <div class="demo-note" style="margin-top:10px">Wallpaper follows the selected theme.</div>
        </section>
        <section class="tstrip-card">
          <h4>Quick Themes</h4>
          <div class="filter-pills">${THEMES.map(k=>`<button class="${theme===k?'on':''}" onclick="themeSwitch('${k}')">${k==='autumn'?'Automne':pad((DEMO_THEMES[k]||{label:k}).label.split(' /')[0])}</button>`).join('')}</div>
        </section>
        <section class="tstrip-card">
          <h4>Shortcuts & Productivity</h4>
          <div class="shortcut-list"><div class="shortcut"><kbd>⌘K</kbd> Everything</div><div class="shortcut"><kbd>/</kbd> Search</div><div class="shortcut"><kbd>N</kbd> New task</div><div class="shortcut"><kbd>T</kbd> Theme</div><div class="shortcut"><kbd>1</kbd> Dashboard</div></div>
        </section>
      </div>
    </div>`;
}

function kpi(n,label,color){ return '<div class="kpi"><b style="color:'+color+'">'+n+'</b><span>'+label+'</span></div>'; }

function isoMonday(d){
  const x=new Date(d);
  const day=(x.getDay()+6)%7;
  x.setDate(x.getDate()-day); x.setHours(0,0,0,0);
  return x;
}
function weeklyEffortMinutes(){
  const mon=isoMonday(new Date());
  const days=[0,1,2,3,4,5,6].map(i=>{ const d=new Date(mon); d.setDate(mon.getDate()+i); return d; });
  const mins=[0,0,0,0,0,0,0];
  (S.time_entries||[]).forEach(e=>{
    if(!e.ended_at || !e.started_at) return;
    const sd=new Date(e.started_at);
    for(let i=0;i<7;i++){
      const d=days[i];
      if(sd.getFullYear()===d.getFullYear() && sd.getMonth()===d.getMonth() && sd.getDate()===d.getDate()){
        mins[i]+=(e.duration_min||0);
        break;
      }
    }
  });
  return {days,mins};
}
function svgWeeklyBarChart(mins){
  const labels=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const barW=28,gap=10,H=100,padBottom=22;
  const maxV=Math.max(60,...mins);
  const todayIdx=(new Date().getDay()+6)%7;
  let bars='';
  mins.forEach((m,i)=>{
    const h=Math.max(2, Math.round((m/maxV)*(H-padBottom-6)));
    const x=i*(barW+gap)+8;
    const y=H-padBottom-h;
    bars+='<rect x="'+x+'" y="'+y+'" width="'+barW+'" height="'+h+'" rx="4" fill="'+(i===todayIdx?'var(--acc2)':'var(--panel3)')+'">'+
      '<title>'+labels[i]+': '+fmtDur(m)+'</title></rect>';
    bars+='<text x="'+(x+barW/2)+'" y="'+(H-6)+'" text-anchor="middle" font-size="10" fill="var(--mut)">'+labels[i]+'</text>';
  });
  const W=(barW+gap)*7+8;
  return '<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="'+H+'">'+bars+'</svg>';
}


let ANMONTH=localStorage.getItem('m2_anmonth')||iso(new Date()).slice(0,7);
function minsBetween(a,b){
  if(!a||!b) return 0;
  const [ah,am]=a.split(':').map(Number),[bh,bm]=b.split(':').map(Number);
  if([ah,am,bh,bm].some(Number.isNaN)) return 0;
  return Math.max(0,(bh*60+bm)-(ah*60+am));
}
function monthStats(month){
  const [yy,mm]=month.split('-').map(Number), days=new Date(yy,mm,0).getDate();
  const rows=Array.from({length:days},(_,i)=>({day:i+1,date:`${month}-${String(i+1).padStart(2,'0')}`,focus:0,planned:0,meet:0,done:0,routine:0}));
  const byDate=Object.fromEntries(rows.map(r=>[r.date,r]));
  (S.time_entries||[]).forEach(e=>{ const d=String(e.started_at||'').slice(0,10); if(byDate[d]) byDate[d].focus+=Number(e.duration_min||0); });
  (S.tasks||[]).forEach(t=>{
    const pd=t.planned_date||t.deadline||''; if(byDate[pd]) byDate[pd].planned+=Number(t.estimate_min||0);
    const cd=String(t.completed_at||'').slice(0,10); if(byDate[cd]) byDate[cd].done+=1;
  });
  (S.meetings||[]).forEach(m=>meetingOccurrences(m).forEach(d=>{ if(byDate[d]) byDate[d].meet+=minsBetween(m.start_time||m.meeting_time,m.end_time); }));
  (S.daily_tasks||[]).forEach(x=>{ if(x.done&&byDate[x.task_date]) byDate[x.task_date].routine+=1; });
  return rows;
}
function sumRows(rows,k){ return rows.reduce((s,r)=>s+Number(r[k]||0),0); }
function fmtHours(min){ return (min/60).toFixed(min>=600?0:1)+'h'; }
function monthlySvg(rows){
  const W=980,H=330,L=42,R=16,T=20,B=38,innerW=W-L-R,innerH=H-T-B;
  const max=Math.max(60,...rows.map(r=>Math.max(r.focus,r.planned,r.meet)));
  const x=i=>L+(i+.5)*innerW/rows.length, y=v=>T+innerH-(v/max)*innerH;
  const bw=Math.max(3,innerW/rows.length*.34);
  let s=`<svg class="month-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly productivity chart">`;
  [0,.25,.5,.75,1].forEach(p=>{const yy=T+innerH*(1-p),v=Math.round(max*p);s+=`<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" stroke="var(--line)"/><text x="${L-7}" y="${yy+4}" text-anchor="end" font-size="10" fill="var(--dim)">${v}</text>`});
  rows.forEach((r,i)=>{const xx=x(i); const hf=Math.max(0,T+innerH-y(r.focus)); const hm=Math.max(0,T+innerH-y(r.meet));
    s+=`<rect x="${xx-bw-.5}" y="${y(r.focus)}" width="${bw}" height="${hf}" rx="2" fill="var(--acc)"><title>Day ${r.day}: ${r.focus} focus min</title></rect>`;
    s+=`<rect x="${xx+.5}" y="${y(r.meet)}" width="${bw}" height="${hm}" rx="2" fill="var(--teal)" opacity=".75"><title>Day ${r.day}: ${r.meet} meeting min</title></rect>`;
    if(i%Math.ceil(rows.length/10)===0||i===rows.length-1) s+=`<text x="${xx}" y="${H-12}" text-anchor="middle" font-size="10" fill="var(--dim)">${r.day}</text>`;
  });
  const pts=rows.map((r,i)=>`${x(i)},${y(r.planned)}`).join(' ');
  s+=`<polyline points="${pts}" fill="none" stroke="var(--amber)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
  rows.forEach((r,i)=>{if(r.planned>0)s+=`<circle cx="${x(i)}" cy="${y(r.planned)}" r="2.5" fill="var(--amber)"><title>Day ${r.day}: ${r.planned} planned min</title></circle>`});
  return s+'</svg>';
}
function activitySvg(rows){
  const W=980,H=220,L=38,R=12,T=15,B=34,innerW=W-L-R,innerH=H-T-B;
  const max=Math.max(1,...rows.map(r=>r.done+r.routine)); const step=innerW/rows.length,bw=Math.max(3,step*.54);
  let s=`<svg class="month-chart" style="min-height:200px" viewBox="0 0 ${W} ${H}">`;
  rows.forEach((r,i)=>{const xx=L+i*step+(step-bw)/2;const vd=r.done,vr=r.routine;const hd=vd/max*innerH,hr=vr/max*innerH;
    s+=`<rect x="${xx}" y="${T+innerH-hd}" width="${bw}" height="${hd}" rx="2" fill="var(--green)"><title>Day ${r.day}: ${vd} completed tasks</title></rect>`;
    s+=`<rect x="${xx}" y="${T+innerH-hd-hr}" width="${bw}" height="${hr}" rx="2" fill="var(--pink)" opacity=".72"><title>Day ${r.day}: ${vr} completed routines</title></rect>`;
    if(i%Math.ceil(rows.length/10)===0||i===rows.length-1)s+=`<text x="${xx+bw/2}" y="${H-10}" text-anchor="middle" font-size="10" fill="var(--dim)">${r.day}</text>`;
  }); return s+'</svg>';
}
function setAnalyticsMonth(v){ANMONTH=v;localStorage.setItem('m2_anmonth',v);render();}
function rAnalytics(el){
  const rows=monthStats(ANMONTH),focus=sumRows(rows,'focus'),planned=sumRows(rows,'planned'),meet=sumRows(rows,'meet'),done=sumRows(rows,'done'),routine=sumRows(rows,'routine');
  const tracked=focus+meet, variance=planned?Math.round((focus-planned)/planned*100):0;
  const best=rows.slice().sort((a,b)=>(b.focus+b.done*30+b.routine*15)-(a.focus+a.done*30+a.routine*15))[0];
  const avg=rows.length?Math.round(focus/rows.length):0;
  el.innerHTML=`<div class="chart-head" style="margin-bottom:16px"><div><h2 style="font-size:19px">Monthly Productivity</h2><div class="demo-note">Planned time, actual focus, meetings, completions and routines. Completion history is recorded precisely from v5 onward.</div></div><input type="month" value="${ANMONTH}" onchange="setAnalyticsMonth(this.value)" style="width:170px"></div>
  <div class="analytics-grid">
    <div class="metric-card"><div class="big">${fmtHours(focus)}</div><div class="sub">Focused work tracked</div></div>
    <div class="metric-card"><div class="big">${fmtHours(planned)}</div><div class="sub">Estimated / planned work</div></div>
    <div class="metric-card"><div class="big">${fmtHours(meet)}</div><div class="sub">Meeting time</div></div>
    <div class="metric-card"><div class="big">${done}</div><div class="sub">Tasks completed this month</div></div>
  </div>
  <div class="analytics-layout">
    <div style="display:grid;gap:16px">
      <section class="chart-card"><div class="chart-head"><h3>Time: plan vs reality</h3><div class="chart-legend"><span><i class="legend-dot" style="background:var(--acc)"></i>Focus</span><span><i class="legend-dot" style="background:var(--teal)"></i>Meetings</span><span><i class="legend-dot" style="background:var(--amber)"></i>Estimated</span></div></div>${monthlySvg(rows)}</section>
      <section class="chart-card"><div class="chart-head"><h3>Daily activity</h3><div class="chart-legend"><span><i class="legend-dot" style="background:var(--green)"></i>Completed tasks</span><span><i class="legend-dot" style="background:var(--pink)"></i>Routines</span></div></div>${activitySvg(rows)}</section>
    </div>
    <div style="display:grid;gap:16px">
      <section class="chart-card"><h3 style="font-size:14px;margin-bottom:12px">Month insights</h3><div class="insight-list">
        <div class="insight"><b>Tracked workload</b><span>${fmtHours(tracked)} across focused work + meetings.</span></div>
        <div class="insight"><b>Estimate variance</b><span>${planned?`${variance>0?'+':''}${variance}% actual focus vs planned estimates.`:'Add estimated minutes to tasks to compare plan vs reality.'}</span></div>
        <div class="insight"><b>Best activity day</b><span>${best&&best.focus+best.done+best.routine?`${fmtdy(best.date)} · ${best.focus} focus min · ${best.done} tasks done.`:'Not enough tracked activity yet.'}</span></div>
        <div class="insight"><b>Daily focus average</b><span>${avg} minutes/day across this calendar month.</span></div>
        <div class="insight"><b>Routine completions</b><span>${routine} completed daily-routine items.</span></div>
      </div></section>
      <section class="chart-card"><h3 style="font-size:14px;margin-bottom:12px">Workload planner</h3>${workloadPlannerHtml()}</section>
    </div>
  </div>`;
}
function workloadPlannerHtml(){
  const today=iso(new Date()), tasks=(S.tasks||[]).filter(t=>t.status!=='Done'&&(t.planned_date===today||(!t.planned_date&&t.deadline===today)));
  const planned=tasks.reduce((s,t)=>s+Number(t.estimate_min||0),0), cap=480, pct=Math.min(100,Math.round(planned/cap*100));
  return `<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:8px"><span>Today planned</span><b>${fmtDur(planned)} / 8h</b></div><div class="planner-bar"><i style="width:${pct}%"></i></div><div class="demo-note" style="margin-top:10px">${planned>cap?'Over capacity. Consider moving lower-priority work.':planned?`${cap-planned} minutes remain in an 8-hour capacity.`:'Schedule tasks with a planned day + estimate to use workload planning.'}</div>`;
}

function matrixCard(title,help,tasks,accent){
  return `<section class="matrix-q"><div style="height:3px;border-radius:99px;background:${accent};margin-bottom:12px"></div><h3>${title}</h3><div class="qhelp">${help}</div>${tasks.length?tasks.slice(0,14).map(t=>`<div class="matrix-task" onclick="editTask(S.tasks.find(x=>x.id==${t.id}))"><div style="font-size:12px;font-weight:650">${esc(t.title)}</div><div class="demo-note">${esc(projName(t.project_id)||t.task_type||'Task')}${t.deadline?' · '+fmtd(t.deadline):''}${t.estimate_min?' · ~'+fmtDur(t.estimate_min):''}</div></div>`).join(''):'<div class="empty" style="padding:24px 8px">Nothing here</div>'}</section>`;
}
function rMatrix(el){
  const tasks=(S.tasks||[]).filter(t=>t.status!=='Done'), urgent=t=>t.deadline&&daysTo(t.deadline)<=3, important=t=>t.priority==='High';
  const q1=tasks.filter(t=>urgent(t)&&important(t)),q2=tasks.filter(t=>!urgent(t)&&important(t)),q3=tasks.filter(t=>urgent(t)&&!important(t)),q4=tasks.filter(t=>!urgent(t)&&!important(t));
  el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">Priority Matrix</h2><div class="demo-note">Urgent = due within 3 days. Important = High priority. Click a task to edit it.</div></div><button class="btn pri" onclick="editTask()">+ New task</button></div><div class="matrix">${matrixCard('Do now','Urgent + important',q1,'var(--red)')}${matrixCard('Schedule','Important, not urgent',q2,'var(--acc)')}${matrixCard('Quick / delegate','Urgent, lower priority',q3,'var(--amber)')}${matrixCard('Backlog','Not urgent + lower priority',q4,'var(--dim)')}</div>`;
}

let FOCUS={timer:null,entryId:null,taskId:null,total:0,left:0,started:0,running:false};
function focusTaskOptions(){return (S.tasks||[]).filter(t=>t.status!=='Done').map(t=>`<option value="${t.id}">${esc(t.title)}</option>`).join('');}
function openFocusMode(){
  const ov=document.getElementById('focusovl'), p=document.getElementById('focuspanel');
  const opts=focusTaskOptions();
  p.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center"><div style="text-align:left"><div style="font-size:18px;font-weight:750">Focus / Zen</div><div class="demo-note">One task. One timer. No clutter.</div></div><button class="btn ghost" onclick="closeFocusMode()">Close</button></div>
    <div style="display:grid;grid-template-columns:1fr auto;gap:10px;margin:22px 0 10px"><select id="focusTaskSel">${opts||'<option value="">Create a task first</option>'}</select><select id="focusMinutes"><option value="25">25 min</option><option value="50">50 min</option><option value="90">90 min</option></select></div>
    <div class="focus-ring" id="focusRing" style="--focus-pct:0%"><div class="focus-time" id="focusTime">25:00</div></div>
    <div class="focus-task" id="focusTaskName">Ready to focus</div><div class="focus-sub" id="focusSub">Your session is logged as actual task time.</div>
    <div class="focus-actions"><button class="btn pri" id="focusStart" onclick="startFocusSession()">Start focus</button><button class="btn ghost" onclick="toggleZenShell()">Toggle clean screen</button><button class="btn ghost" id="focusStop" onclick="stopFocusSession(false)" style="display:none">Finish session</button></div>`;
  ov.classList.add('show');
  const mins=document.getElementById('focusMinutes'); mins.onchange=()=>{if(!FOCUS.running) document.getElementById('focusTime').textContent=String(mins.value).padStart(2,'0')+':00';};
}
function closeFocusMode(){ if(FOCUS.running){toast('Finish the focus session before closing');return;} document.getElementById('focusovl').classList.remove('show'); document.body.classList.remove('zen-shell'); }
function toggleZenShell(){ document.body.classList.toggle('zen-shell'); toast(document.body.classList.contains('zen-shell')?'Clean screen on · Esc to exit':'Clean screen off'); }
async function startFocusSession(){
  if(FOCUS.running)return; const sel=document.getElementById('focusTaskSel'),taskId=Number(sel.value),mins=Number(document.getElementById('focusMinutes').value||25); if(!taskId){toast('Choose a task first');return;}
  const old=(S.time_entries||[]).find(e=>!e.ended_at); if(old){const dur=Math.max(1,Math.round((Date.now()-new Date(old.started_at).getTime())/60000)); await api('PUT','/api/time_entries/'+old.id,{ended_at:new Date().toISOString(),duration_min:dur});}
  const started=new Date().toISOString(), r=await api('POST','/api/time_entries',{task_id:taskId,started_at:started,ended_at:'',duration_min:0});
  FOCUS={timer:null,entryId:r.id,taskId,total:mins*60,left:mins*60,started:Date.now(),running:true}; document.body.classList.add('zen-shell');
  document.getElementById('focusStart').style.display='none';document.getElementById('focusStop').style.display='inline-flex'; document.getElementById('focusTaskName').textContent=((S.tasks||[]).find(t=>t.id==taskId)||{}).title||'Focus session';
  FOCUS.timer=setInterval(updateFocusClock,250); updateFocusClock();
}
function updateFocusClock(){
  if(!FOCUS.running)return; const elapsed=Math.floor((Date.now()-FOCUS.started)/1000); FOCUS.left=Math.max(0,FOCUS.total-elapsed); const m=Math.floor(FOCUS.left/60),s=FOCUS.left%60; const t=document.getElementById('focusTime'),r=document.getElementById('focusRing'); if(t)t.textContent=String(m).padStart(2,'0')+':'+String(s).padStart(2,'0'); if(r)r.style.setProperty('--focus-pct',Math.min(100,elapsed/FOCUS.total*100)+'%'); if(FOCUS.left<=0) stopFocusSession(true);
}
async function stopFocusSession(completed){
  if(!FOCUS.running)return; clearInterval(FOCUS.timer); const dur=Math.max(1,Math.round((Date.now()-FOCUS.started)/60000)); await api('PUT','/api/time_entries/'+FOCUS.entryId,{ended_at:new Date().toISOString(),duration_min:dur}); FOCUS.running=false; document.body.classList.remove('zen-shell'); document.getElementById('focusStop').style.display='none';document.getElementById('focusStart').style.display='inline-flex'; document.getElementById('focusSub').textContent=completed?'Session complete — saved to your analytics.':'Session finished — time saved.'; toast('Focus time saved'); await load();
}

function closeRawModal(){document.getElementById('ovl').classList.remove('show');}
function commandGo(fn){closeRawModal();setTimeout(fn,0);}
function openCommandCenter(){
  const ov=document.getElementById('ovl'),m=document.getElementById('modal');
  m.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center"><h3>Command Center</h3><button class="btn ghost" onclick="closeRawModal()">Esc</button></div><div class="cmd-grid">
  <button class="cmd-action" onclick="commandGo(()=>editTask())"><b>New task</b><span>Create + estimate work</span></button>
  <button class="cmd-action" onclick="commandGo(()=>editNoteMeta())"><b>New note</b><span>Capture an idea</span></button>
  <button class="cmd-action" onclick="commandGo(()=>editMeeting(null,iso(new Date())))"><b>New meeting</b><span>Add an event</span></button>
  <button class="cmd-action" onclick="commandGo(()=>openFocusMode())"><b>Focus / Zen</b><span>Start a timed session</span></button>
  <button class="cmd-action" onclick="commandGo(()=>openDataHealth())"><b>Data health</b><span>Audit + safe cleanup</span></button>
  <button class="cmd-action" onclick="commandGo(()=>toggleTheme())"><b>Switch theme</b><span>Aurora · Automne · Cyber</span></button></div>`; ov.classList.add('show');
}

async function openDataHealth(){
  let h; try{h=await api('GET','/api/data/health');}catch(e){toast('Could not read data health');return;}
  const ov=document.getElementById('ovl'),m=document.getElementById('modal'); const blanks=Object.values(h.blank_titles||{}).reduce((a,b)=>a+b,0);
  m.innerHTML=`<h3>Data Health</h3><div class="demo-note">Safe cleanup creates a database backup first. Duplicate-looking tasks are reported, not auto-deleted.</div><div class="health-grid">
    <div class="health-cell"><b>${h.orphan_time_entries||0}</b><span>orphan timer rows</span></div><div class="health-cell"><b>${h.duplicate_task_groups||0}</b><span>possible duplicate task groups</span></div><div class="health-cell"><b>${(h.duplicate_google_groups||0)+(h.duplicate_caldav_groups||0)}</b><span>calendar duplicate groups</span></div><div class="health-cell"><b>${blanks}</b><span>blank titles</span></div><div class="health-cell"><b>${h.stale_timers||0}</b><span>timers open &gt;24h</span></div><div class="health-cell"><b>${Object.values(h.counts||{}).reduce((a,b)=>a+b,0)}</b><span>rows across app tables</span></div></div>
    <div class="acts"><button class="btn ghost" onclick="closeRawModal()">Close</button><span class="sp"></span><button class="btn pri" onclick="runDataCleanup()">Backup + Safe Clean</button></div>`; ov.classList.add('show');
}
async function runDataCleanup(){
  const r=await api('POST','/api/data/cleanup',{}); if(!r.ok){toast(r.error||'Cleanup failed');return;} closeRawModal(); toast(`Cleaned safely · backup created`); await load();
}


let TASKQUICK='all';
function setTaskQuick(v){ TASKQUICK=v; render(); }
function taskQuickMatch(t){
  if(TASKQUICK==='all') return true;
  if(TASKQUICK==='done') return t.status==='Done';
  if(!t.deadline) return false;
  const d=daysTo(t.deadline);
  if(TASKQUICK==='today') return d===0 && t.status!=='Done';
  if(TASKQUICK==='week') return d>=0&&d<=7 && t.status!=='Done';
  if(TASKQUICK==='overdue') return d<0 && t.status!=='Done';
  return true;
}
function rBoard(el){
  const T=(S.tasks||[]).filter(t=>match(t.title+' '+t.description+' '+(t.tags||'')) &&
    (!PROJFILTER||t.project_id==PROJFILTER) && taskQuickMatch(t));
  let filter='';
  if(PROJFILTER){
    filter='<div style="margin-bottom:12px"><span class="chip" style="background:var(--accbg);color:var(--acc2)">'+
      'Project: '+esc(projName(PROJFILTER))+' <button onclick="PROJFILTER=0;render()" style="color:inherit;font-weight:700">✕</button></span></div>';
  }
  const quickLabels={all:'All',today:'Today',week:'This week',overdue:'Overdue',done:'Done'};
  const quickBtns='<div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">'+
    Object.keys(quickLabels).map(k=>'<button class="btn ghost'+(TASKQUICK===k?' on':'')+
      '" style="padding:5px 12px;font-size:12px" onclick="setTaskQuick(\''+k+'\')">'+quickLabels[k]+'</button>').join('')+'</div>';
  let h=quickBtns+filter+'<div id="board">';
  for(const st of STATUSES){
    const list=T.filter(t=>t.status===st).sort((a,b)=>{
      const pr={High:0,Normal:1,Low:2};
      if(pr[a.priority]!==pr[b.priority]) return pr[a.priority]-pr[b.priority];
      return (a.deadline||'9999')<(b.deadline||'9999')?-1:1;
    });
    h+='<div class="col" data-st="'+st+'"><div class="colhead">'+
       '<span class="dot" style="background:'+STATCOL[st]+'"></span>'+st+
       '<span class="n">'+list.length+'</span>'+
       '<button class="add" onclick="editTask({status:\''+st+'\'})">+</button></div>'+
       '<div class="collist" data-st="'+st+'">';
    for(const t of list){
      const d=t.deadline?daysTo(t.deadline):null;
      const dchip=t.deadline?'<span class="chip" style="background:'+
        (d<0?'var(--redbg);color:var(--red)':d<=3?'var(--amberbg);color:var(--amber)':'var(--panel3);color:var(--mut)')+
        '">⏱ '+fmtd(t.deadline)+(d<0?' · '+Math.abs(d)+'d late':'')+'</span>':'';
      const entries=(S.time_entries||[]).filter(e=>e.task_id==t.id);
      const open=entries.find(e=>!e.ended_at);
      const totalMin=entries.reduce((s,e)=>s+(e.ended_at?(e.duration_min||0):0),0);
      const sp=subtaskProgress(t.subtasks);
      const tags=taskTags(t);
      h+='<div class="task" draggable="true" data-id="'+t.id+'">'+
        '<div class="tt"><span class="dot" style="background:'+PRICOL[t.priority]+'"></span><span>'+esc(t.title)+'</span></div>'+
        (t.description?'<div class="desc">'+esc(t.description)+'</div>':'')+
        '<div class="meta">'+
        (t.project_id?'<span class="chip" style="background:color-mix(in srgb,'+projColor(t.project_id)+' 14%,transparent);color:'+projColor(t.project_id)+'">'+esc(projName(t.project_id))+'</span>':'')+
        '<span class="chip" style="background:var(--panel3);color:var(--mut)">'+esc(t.task_type)+'</span>'+
        (t.estimate_min?'<span class="chip" style="background:var(--accbg);color:var(--acc2)">~ '+fmtDur(t.estimate_min)+'</span>':'')+
        dchip+
        tags.map(tg=>'<span class="chip" style="background:var(--accbg);color:var(--acc2)">#'+esc(tg)+'</span>').join('')+
        '</div>'+
        (sp?'<div style="display:flex;align-items:center;gap:6px;margin:4px 0 0"><div class="pbar" style="max-width:70px"><i style="width:'+
          Math.round(100*sp.done/sp.total)+'%"></i></div><span style="font-size:11px;color:var(--mut)">'+sp.done+'/'+sp.total+' subtasks</span></div>':'')+
        '<div class="tmeta">'+
        '<button class="tbtn'+(open?' on':'')+'" onclick="event.stopPropagation();toggleTimer('+t.id+')" title="'+(open?'Stop timer':'Start timer')+'">'+(open?'⏸':'▶')+'</button>'+
        '<span class="ttime">'+(totalMin?fmtDur(totalMin):'no time logged')+(open?' · running…':'')+'</span>'+
        '</div></div>';
    }
    h+='</div></div>';
  }
  h+='</div>';
  el.innerHTML=h;
  el.querySelectorAll('.task').forEach(c=>{
    c.addEventListener('dragstart',e=>{ c.classList.add('drag'); e.dataTransfer.setData('text/plain',c.dataset.id); });
    c.addEventListener('dragend',()=>c.classList.remove('drag'));
    c.addEventListener('click',()=>editTask(S.tasks.find(t=>t.id==c.dataset.id)));
  });
  el.querySelectorAll('.col').forEach(col=>{
    col.addEventListener('dragover',e=>{ e.preventDefault(); col.classList.add('over'); });
    col.addEventListener('dragleave',()=>col.classList.remove('over'));
    col.addEventListener('drop',async e=>{
      e.preventDefault(); col.classList.remove('over');
      const id=e.dataTransfer.getData('text/plain'), st=col.dataset.st;
      const t=S.tasks.find(t=>t.id==id);
      if(t&&t.status!==st){ t.status=st; render(); await mutate('PUT','tasks',id,{status:st}); load(); }
    });
  });
}

function rProjects(el){
  const P=(S.projects||[]).filter(p=>match(p.title+' '+p.domain));
  let h='<div class="projgrid">';
  for(const p of P){
    const pt=(S.tasks||[]).filter(t=>t.project_id==p.id), dn=pt.filter(t=>t.status==='Done').length;
    const pc=pt.length?Math.round(100*dn/pt.length):0;
    const d=p.deadline?daysTo(p.deadline):null;
    h+='<div class="card proj" onclick="editProject(S.projects.find(x=>x.id=='+p.id+'))">'+
      '<div style="display:flex;gap:9px;align-items:center"><span class="dot" style="background:'+projColor(p.id)+'"></span>'+
      '<h4 style="flex:1">'+esc(p.title)+'</h4>'+
      '<span class="chip" style="background:var(--panel3);color:var(--mut)">'+esc(p.status)+'</span></div>'+
      (p.domain?'<div class="dom" style="margin-top:4px">'+esc(p.domain)+'</div>':'')+
      (p.description?'<div class="desc" style="font-size:12px;color:var(--mut);margin-top:8px">'+esc(p.description)+'</div>':'')+
      '<div style="display:flex;align-items:center;gap:10px;margin-top:14px"><div class="pbar"><i style="width:'+pc+'%"></i></div><span style="font-size:12px;color:var(--mut)">'+pc+'%</span></div>'+
      '<div class="stats"><span>'+dn+'/'+pt.length+' tasks</span>'+
      (p.deadline?'<span style="color:'+(d<0?'var(--red)':d<=14?'var(--amber)':'var(--mut)')+'">⏱ '+fmtdy(p.deadline)+'</span>':'')+
      '<span style="flex:1"></span>'+
      '<button class="btn ghost" style="padding:4px 10px;font-size:11.5px" onclick="event.stopPropagation();PROJFILTER='+p.id+';setView(\'board\')">Board →</button></div></div>';
  }
  h+='</div>'+(P.length?'':'<div class="empty">No projects — create one with + New</div>');
  el.innerHTML=h;
}

function meetingOccursOn(m,ds){
  if(!m.meeting_date||ds<m.meeting_date) return false;
  if(m.recur_freq==='weekly'&&m.recur_until){
    if(ds>m.recur_until) return false;
    const d0=new Date(m.meeting_date+'T00:00:00'), d1=new Date(ds+'T00:00:00');
    return Math.round((d1-d0)/864e5)%7===0;
  }
  return ds===m.meeting_date;
}
function meetingOccurrences(m){
  const out=[];
  if(!m.meeting_date) return out;
  if(m.recur_freq==='weekly'&&m.recur_until){
    let d=new Date(m.meeting_date+'T00:00:00');
    const until=new Date(m.recur_until+'T00:00:00');
    let guard=0;
    while(d<=until&&guard<520){ out.push(iso(d)); d.setDate(d.getDate()+7); guard++; }
    if(!out.length) out.push(m.meeting_date);
  } else out.push(m.meeting_date);
  return out;
}
function agendaItems(){
  const items=[];
  (S.meetings||[]).forEach(m=>{ if(!m.meeting_date) return;
    meetingOccurrences(m).forEach(ds=>{
      items.push({date:ds,time:m.start_time||m.meeting_time||'',end:m.end_time||'',
        title:(ds!==m.meeting_date?'↻ ':'')+m.title,kind:'meeting',loc:m.location,src:m});
    });
  });
  (S.tasks||[]).forEach(t=>{ if(t.deadline&&t.status!=='Done') items.push({date:t.deadline,time:'',title:t.title,kind:'deadline',src:t}); });
  (S.milestones||[]).forEach(ms=>{ if(ms.milestone_date&&!ms.done) items.push({date:ms.milestone_date,time:'',title:ms.title,kind:'milestone',src:ms}); });
  (S.projects||[]).forEach(p=>{ if(p.deadline&&p.status!=='Done') items.push({date:p.deadline,time:'',title:p.title,kind:'project',src:p}); });
  return items.sort((a,b)=>a.date===b.date?(a.time<b.time?-1:1):(a.date<b.date?-1:1));
}
const KINDSTYLE={meeting:'background:var(--accbg);color:var(--acc2)',deadline:'background:var(--redbg);color:var(--red)',
  milestone:'background:var(--amberbg);color:var(--amber)',project:'background:var(--tealbg);color:var(--teal)'};
const KINDDOT={meeting:'var(--acc2)',deadline:'var(--red)',milestone:'var(--amber)',project:'var(--teal)'};
function openItem(it){ if(it.kind==='meeting') editMeeting(it.src); else if(it.kind==='deadline') editTask(it.src); else if(it.kind==='milestone') editMilestone(it.src); else editProject(it.src); }
let AGITEMS=[];
function rAgenda(el){
  AGITEMS=agendaItems();
  const byDate={}; AGITEMS.forEach((it,ix)=>{ (byDate[it.date]=byDate[it.date]||[]).push(ix); });
  const mn=AGDATE.toLocaleDateString('en-US',{month:'long',year:'numeric'});
  let h='<div id="aghead"><span id="agtitle">'+mn+'</span>'+
    '<button class="btn ghost" onclick="agNav(-1)">‹</button>'+
    '<button class="btn ghost" onclick="AGDATE=new Date();AGSEL=iso(new Date());render()">Today</button>'+
    '<button class="btn ghost" onclick="agNav(1)">›</button><span style="flex:1"></span>'+
    '<button class="fintab'+(AGMODE==='month'?' on':'')+'" onclick="agMode(\'month\')">Month</button>'+
    '<button class="fintab'+(AGMODE==='week'?' on':'')+'" onclick="agMode(\'week\')">Week</button>'+
    '<button class="btn ghost" onclick="editMilestone(null,AGSEL)">+ Milestone</button>'+
    '<button class="btn pri" onclick="editMeeting(null,AGSEL)">+ Event</button></div>';
  h+='<div style="display:flex;gap:14px;margin-bottom:12px;font-size:11.5px;color:var(--mut)">'+
    ['meeting','deadline','milestone','project'].map(k=>'<span class="chip" style="'+KINDSTYLE[k]+'">'+k+'</span>').join('')+'</div>';
  if(AGMODE==='month') h+=agMonth(byDate); else h+=agWeek(byDate);
  h+='<div style="margin-top:20px" class="panel"><h3 class="sec">'+fmtdy(AGSEL)+'</h3><div id="agday">'+agDayList(byDate[AGSEL]||[])+'</div></div>';
  el.innerHTML=h;
}
function agDayList(ixs){
  if(!ixs.length) return '<div class="empty" style="padding:16px">Nothing scheduled — click + Event to add</div>';
  return ixs.map(ix=>{ const it=AGITEMS[ix];
    return '<div class="row" onclick="openItem(AGITEMS['+ix+'])">'+
      '<span class="chip" style="'+KINDSTYLE[it.kind]+'">'+it.kind+'</span>'+
      '<span class="t">'+esc(it.title)+'</span>'+
      '<span class="d">'+esc(it.time?it.time+(it.end?'–'+it.end:''):'')+(it.loc?' · '+esc(it.loc):'')+'</span></div>';
  }).join('');
}
function agNav(dir){
  if(AGMODE==='month') AGDATE.setMonth(AGDATE.getMonth()+dir);
  else AGDATE.setDate(AGDATE.getDate()+7*dir);
  render();
}
function agMode(m){ AGMODE=m; localStorage.setItem('m2agmode',m); render(); }
function agMonth(byDate){
  const y=AGDATE.getFullYear(), m=AGDATE.getMonth(), today=iso(new Date());
  const first=new Date(y,m,1); let start=new Date(first); start.setDate(1-(first.getDay()+6)%7);
  let h='<div class="aggrid">'+['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d=>'<div class="agdow">'+d+'</div>').join('');
  const cur=new Date(start);
  for(let i=0;i<42;i++){
    const ds=iso(cur), ixs=byDate[ds]||[], out=cur.getMonth()!==m;
    h+='<div class="agcell'+(out?' out':'')+(ds===today?' today':'')+(ds===AGSEL?' sel':'')+'" onclick="AGSEL=\''+ds+'\';render()">'+
      '<div class="dn">'+cur.getDate()+'</div>'+
      ixs.slice(0,3).map(ix=>{ const it=AGITEMS[ix];
        return '<div class="agev" style="'+KINDSTYLE[it.kind]+'">'+(it.time?it.time+' ':'')+esc(it.title)+'</div>'; }).join('')+
      (ixs.length>3?'<div class="agmore">+'+(ixs.length-3)+' more</div>':'')+
      '<div class="agdots">'+ixs.slice(0,6).map(ix=>'<span class="dot" style="width:5px;height:5px;background:'+KINDDOT[AGITEMS[ix].kind]+'"></span>').join('')+'</div>'+
      '</div>';
    cur.setDate(cur.getDate()+1);
    if(i>=34 && cur.getMonth()!==m && cur.getDay()===1) break;
  }
  return h+'</div>';
}
function agWeek(byDate){
  const start=new Date(AGDATE); start.setDate(start.getDate()-(start.getDay()+6)%7);
  const today=iso(new Date());
  let h='<div class="agweek">';
  for(let i=0;i<7;i++){
    const d=new Date(start); d.setDate(d.getDate()+i); const ds=iso(d), ixs=byDate[ds]||[];
    h+='<div class="agwcol'+(ds===today?' today':'')+'" onclick="AGSEL=\''+ds+'\';render()">'+
      '<h5>'+d.toLocaleDateString('en-US',{weekday:'short',day:'numeric'})+'</h5>'+
      ixs.map(ix=>{ const it=AGITEMS[ix];
        return '<div class="agwev" style="'+KINDSTYLE[it.kind]+'" onclick="event.stopPropagation();openItem(AGITEMS['+ix+'])">'+
          (it.time?'<b>'+it.time+(it.end?'–'+it.end:'')+'</b>':'')+esc(it.title)+'</div>'; }).join('')+
      '</div>';
  }
  return h+'</div>';
}

function rDaily(el){
  const D=(S.daily_tasks||[]).filter(d=>d.task_date===DAILYDATE)
    .sort((a,b)=>(a.task_time||'99:99').localeCompare(b.task_time||'99:99'));
  const done=D.filter(d=>d.done).length;
  let h='<div class="dailywrap">'+
    '<div style="display:flex;align-items:center;gap:12px;margin-bottom:18px">'+
    '<input type="date" value="'+DAILYDATE+'" onchange="DAILYDATE=this.value;render()">'+
    '<div class="pbar"><i style="width:'+(D.length?Math.round(100*done/D.length):0)+'%"></i></div>'+
    '<span style="font-size:12.5px;color:var(--mut)">'+done+'/'+D.length+'</span>'+
    '<button class="btn pri" onclick="editDaily()">+ Add</button></div>';
  for(const cat of DAILY_CATS){
    const list=D.filter(d=>d.category===cat);
    if(!list.length) continue;
    h+='<div class="dcat"><h3 class="sec">'+cat+'</h3>'+list.map(d=>
      '<div class="drow'+(d.done?' done':'')+'">'+
      '<button class="chk'+(d.done?' on':'')+'" onclick="toggleDaily('+d.id+','+(d.done?0:1)+')">'+(d.done?'✓':'')+'</button>'+
      (d.task_time?'<span class="chip timerange">'+esc(d.task_time)+
        (d.task_time_end?'<i class="tarrow"></i>'+esc(d.task_time_end):'')+'</span>':'')+
      '<span class="t" style="flex:1;cursor:pointer" onclick="editDaily(S.daily_tasks.find(x=>x.id=='+d.id+'))">'+esc(d.title)+'</span>'+
      '<button style="color:var(--dim)" onclick="del(\'daily_tasks\','+d.id+')">✕</button></div>').join('')+'</div>';
  }
  if(!D.length) h+='<div class="empty">No items for this day — add your routine</div>';
  h+='</div>';
  el.innerHTML=h;
}
async function toggleDaily(id,v){ const d=(S.daily_tasks||[]).find(x=>x.id==id); if(d){d.done=v; render();} await mutate('PUT','daily_tasks',id,{done:v}); load(); }
function editDaily(d){
  d=d||{};
  modal(d.id?'Edit daily item':'New daily item',[
    {k:'title',label:'Title'},
    {k:'category',label:'Category',type:'select',options:DAILY_CATS,def:'Personal',half:true},
    {k:'task_date',label:'Date',type:'date',def:DAILYDATE,half:true},
    {k:'task_time',label:'Start time',type:'time',half:true},
    {k:'task_time_end',label:'End time',type:'time',half:true},
  ],d,o=>save('daily_tasks',o,d.id).then(()=>toast('Saved')),
  d.id?()=>del('daily_tasks',d.id).then(()=>toast('Deleted')):null);
}

function toggleNoteCat(cat){
  const i=NOTECATS_COLLAPSED.indexOf(cat);
  if(i>=0) NOTECATS_COLLAPSED.splice(i,1); else NOTECATS_COLLAPSED.push(cat);
  localStorage.setItem('m2_notecats_collapsed', JSON.stringify(NOTECATS_COLLAPSED));
  render();
}
async function quickAddToCategory(cat){
  NOTECATDEFAULT=cat; localStorage.setItem('m2_notecat_default',cat);
  const i=NOTECATS_COLLAPSED.indexOf(cat);
  if(i>=0){ NOTECATS_COLLAPSED.splice(i,1); localStorage.setItem('m2_notecats_collapsed', JSON.stringify(NOTECATS_COLLAPSED)); }
  const r=await mutate('POST','notes',null,{title:'Untitled',content:'',tags:'',project_id:'',category:cat});
  NOTESEL=r.id;
  await load();
}
async function moveNoteCategory(sel){
  const id=+sel.dataset.id, cat=sel.value;
  sel.value='';
  if(!cat) return;
  await mutate('PUT','notes',id,{category:cat});
  await load();
  toast('Moved to '+cat);
}
function extractWikiLinks(text){
  const re=/\[\[([^\]]+)\]\]/g; const out=[]; let m;
  while((m=re.exec(text||''))) out.push(m[1].trim());
  return [...new Set(out)].filter(Boolean);
}
function findNoteByTitle(title){
  return (S.notes||[]).find(n=>String(n.title||'').toLowerCase()===String(title||'').toLowerCase());
}
function noteBacklinks(id){
  const n=(S.notes||[]).find(x=>x.id==id); if(!n) return [];
  return (S.notes||[]).filter(o=>o.id!=id && extractWikiLinks(o.content).some(t=>t.toLowerCase()===String(n.title||'').toLowerCase()));
}
function renderNoteLinks(sel){
  const links=extractWikiLinks(sel.content), back=noteBacklinks(sel.id);
  if(!links.length && !back.length) return '';
  let h='<div style="display:flex;flex-wrap:wrap;gap:14px;padding:10px 2px;font-size:12px">';
  if(links.length){
    h+='<div><span style="color:var(--mut)">Links → </span>'+links.map(t=>{
      const target=findNoteByTitle(t);
      return target
        ? '<span class="chip" style="background:var(--accbg);color:var(--acc2);cursor:pointer" onclick="clearTimeout(_compileTimer);stopDictation();NOTESEL='+target.id+';render()">'+esc(t)+'</span>'
        : '<span class="chip" style="background:var(--panel3);color:var(--mut)" title="No note with this title yet">'+esc(t)+' ✧</span>';
    }).join(' ')+'</div>';
  }
  if(back.length){
    h+='<div><span style="color:var(--mut)">Linked from ← </span>'+back.map(n=>
      '<span class="chip" style="background:var(--panel3);color:var(--mut);cursor:pointer" onclick="clearTimeout(_compileTimer);stopDictation();NOTESEL='+n.id+';render()">'+esc(n.title)+'</span>'
    ).join(' ')+'</div>';
  }
  h+='</div>';
  return h;
}
function openNoteGraph(){
  const N=S.notes||[];
  const n=N.length;
  const cx=340,cy=300,r=Math.min(240,60+n*8);
  const pos=N.map((note,i)=>({note,x:cx+r*Math.cos(2*Math.PI*i/Math.max(1,n)),y:cy+r*Math.sin(2*Math.PI*i/Math.max(1,n))}));
  const byId={}; pos.forEach(p=>byId[p.note.id]=p);
  let edges='';
  N.forEach(note=>{
    extractWikiLinks(note.content).forEach(t=>{
      const target=findNoteByTitle(t);
      if(target && byId[note.id] && byId[target.id] && note.id!=target.id){
        const a=byId[note.id], b=byId[target.id];
        edges+='<line x1="'+a.x.toFixed(1)+'" y1="'+a.y.toFixed(1)+'" x2="'+b.x.toFixed(1)+'" y2="'+b.y.toFixed(1)+'" stroke="var(--line2)" stroke-width="1.4"/>';
      }
    });
  });
  const nodes=pos.map(p=>
    '<g class="gnode" style="cursor:pointer" onclick="closeNoteGraph();clearTimeout(_compileTimer);stopDictation();NOTESEL='+p.note.id+';render()">'+
      '<circle cx="'+p.x.toFixed(1)+'" cy="'+p.y.toFixed(1)+'" r="8" fill="var(--acc)"/>'+
      '<text x="'+p.x.toFixed(1)+'" y="'+(p.y-13).toFixed(1)+'" text-anchor="middle" font-size="11" fill="var(--txt)">'+esc(p.note.title)+'</text>'+
    '</g>').join('');
  const W=cx*2, H=cy*2;
  const svg='<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="620" style="max-width:900px">'+edges+nodes+'</svg>';
  const el=document.getElementById('graphovl');
  el.innerHTML='<div class="card" style="max-width:940px;margin:0 auto;padding:20px">'+
    '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px"><h3 style="flex:1">Note graph</h3>'+
    '<button class="btn ghost" onclick="closeNoteGraph()">Close</button></div>'+
    (n?svg:'<div class="empty">No notes yet</div>')+'</div>';
  el.classList.add('show');
}
function closeNoteGraph(){ document.getElementById('graphovl').classList.remove('show'); }

function rNotes(el){
  const N=(S.notes||[]).filter(n=>match(n.title+' '+n.tags+' '+n.content))
    .sort((a,b)=>(b.updated_at||'')<(a.updated_at||'')?-1:1);
  if(NOTESEL && !N.find(n=>n.id==NOTESEL)) NOTESEL=0;
  const sel=N.find(n=>n.id==NOTESEL);
  if(sel) NOTECATDEFAULT=NOTE_CATS.includes(sel.category)?sel.category:NOTECATDEFAULT;

  const searching=!!Q;
  const byCat={}; NOTE_CATS.forEach(c=>byCat[c]=[]);
  N.forEach(n=>{ const c=NOTE_CATS.includes(n.category)?n.category:'New ideas'; byCat[c].push(n); });

  let listHtml=NOTE_CATS.map(cat=>{
    const notes=byCat[cat];
    const collapsed=!searching && NOTECATS_COLLAPSED.includes(cat);
    const others=NOTE_CATS.filter(c=>c!==cat);
    return '<div class="notecat'+(collapsed?' collapsed':'')+'">'+
      '<div class="notecathead" onclick="toggleNoteCat(\''+cat+'\')">'+
        '<span class="chev">▾</span><span class="cname">'+esc(cat)+'</span>'+
        '<span class="ccount">'+notes.length+'</span>'+
        '<span class="cadd" title="New note in '+esc(cat)+'" onclick="event.stopPropagation();quickAddToCategory(\''+cat+'\')">+</span>'+
      '</div>'+
      '<div class="notecat-notes">'+
        (notes.length?notes.map(n=>
          '<div class="noteitem'+(n.id==NOTESEL?' on':'')+'" onclick="clearTimeout(_compileTimer);stopDictation();NOTESEL='+n.id+';render()">'+
            '<span class="nt">'+esc(n.title)+'</span>'+
            '<select class="notemove" data-id="'+n.id+'" title="Move to…" onclick="event.stopPropagation()" onchange="moveNoteCategory(this)">'+
              '<option value="" selected>⋯</option>'+
              others.map(c=>'<option value="'+esc(c)+'">'+esc(c)+'</option>').join('')+
            '</select>'+
          '</div>').join('')
          :'<div class="notecat-empty">No notes</div>')+
      '</div></div>';
  }).join('');

  let h='<div id="noteswrap"><div class="notesidebar">'+
    '<div style="padding:4px 4px 8px"><button class="btn ghost" style="width:100%" onclick="openNoteGraph()">🕸 Graph</button></div>'+
    '<div id="notelist">'+listHtml+'</div></div>';
  if(sel){
    const noteMode=getNoteMode(sel.id);
    h+='<div id="noteedit" class="card" style="padding:16px">'+
      '<div style="display:flex;gap:10px;align-items:center">'+
      '<h4 style="flex:1;font-size:15px">'+esc(sel.title)+'</h4>'+
      (sel.tags?'<span class="chip" style="background:var(--accbg);color:var(--acc2)">'+esc(sel.tags)+'</span>':'')+
      '<span class="chip" style="background:var(--panel3);color:var(--mut)">'+esc(sel.category||'New ideas')+'</span>'+
      (sel.note_date?'<span class="chip" style="background:var(--panel3);color:var(--mut)">📅 '+fmtdy(sel.note_date)+'</span>':'')+
      (noteMode==='latex'?('<label style="font-size:11.5px;color:var(--mut);display:flex;align-items:center;gap:5px;cursor:pointer;white-space:nowrap">'+
      '<input type="checkbox" id="gcToggle" onchange="localStorage.setItem(\'m2_gc_enabled\',this.checked?\'1\':\'0\')" '+
      (localStorage.getItem('m2_gc_enabled')!=='0'?'checked':'')+'> Auto-correct</label>'):'')+
      '<button class="btn ghost notemodebtn" title="Switch between LaTeX source and a Word-style rich text editor" onclick="toggleNoteMode('+sel.id+')">'+
      (noteMode==='word'?'{ } LaTeX mode':'📝 Word mode')+'</button>'+
      '<button class="btn ghost" onclick="editNoteMeta(S.notes.find(n=>n.id=='+sel.id+'))">Rename / tags</button></div>'+
      '<div class="notetoolbar">'+NOTE_TOOLS.map((t,i)=>
        t.sep?'<i class="tbsep"></i>':
        '<button type="button" class="'+(t.cls||'')+'" title="'+esc(t.title)+'" onclick="noteToolbarClick('+sel.id+','+i+')">'+t.label+'</button>').join('')+
        '<span style="flex:1"></span>'+
        '<select id="dictLang" class="dictlang" title="Dictation language" onchange="localStorage.setItem(\'m2_dict_lang\',this.value)">'+
        DICT_LANGS.map(l=>'<option value="'+l[0]+'"'+(dictLang()===l[0]?' selected':'')+'>'+esc(l[1])+'</option>').join('')+
        '</select>'+
        '<button type="button" id="micBtn" class="micbtn" title="Voice dictation" onclick="toggleDictation('+sel.id+')">🎤 Dictate</button>'+
        '</div>'+
      '<div class="notesplit" id="notesplit">'+
      (noteMode==='word'
        ? '<div id="notebody" contenteditable="true" dir="auto" class="notebody-word" oninput="onNoteWordInput('+sel.id+')">'+latexToHtml(sel.content||'')+'</div>'
        : '<textarea id="notebody" dir="auto" placeholder="Write in Markdown or LaTeX…" oninput="onNoteInput('+sel.id+')">'+esc(sel.content||'')+'</textarea>')+
      '<div class="splith" id="splith" title="Drag to resize" onmousedown="startSplitDrag(event)"></div>'+
      '<div id="pdfpane" class="pdfpane"><div class="empty">Compiling live as you type…</div></div>'+
      '</div>'+
      renderNoteLinks(sel)+
      '<div style="display:flex;gap:8px;align-items:center"><span id="noteupdated" style="font-size:11.5px;color:var(--dim)">Updated '+esc((sel.updated_at||'').slice(0,16).replace('T',' '))+'</span>'+
      '<span id="notestatus" style="font-size:11.5px;color:var(--dim)"></span>'+
      '<span style="flex:1"></span>'+
      '<button class="btn ghost" onclick="openHandwrite('+sel.id+')">🖋 Handwrite note</button>'+
      '<button class="btn ghost" onclick="downloadNotePdf(\''+esc(sel.title)+'\')">⤓ Download PDF</button>'+
      '<button class="btn ghost" onclick="compileNotePdf('+sel.id+',true)">📄 Compile now</button>'+
      '<button class="btn pri" onclick="saveNote('+sel.id+')">Save note</button></div></div>';
  } else h+='<div class="card empty" style="align-self:start;justify-self:stretch;padding:40px 10px">Select a note on the left, or create one with a category\'s + button (or + New above)</div>';
  h+='</div>';
  el.innerHTML=h;
  if(sel){
    compileNotePdf(sel.id,true);
    const ta=document.getElementById('notebody');
    const savedW=parseInt(localStorage.getItem('m2_split_w')||'0',10);
    if(ta && ta.tagName==='TEXTAREA' && savedW>100) ta.style.flex='0 0 '+savedW+'px';
  }
}

let _splitDragging=false;
function startSplitDrag(e){
  _splitDragging=true;
  document.body.style.userSelect='none';
  const h=document.getElementById('splith'); if(h) h.classList.add('active');
  e.preventDefault();
}
window.addEventListener('mousemove',e=>{
  if(!_splitDragging) return;
  const wrap=document.getElementById('notesplit'), ta=document.getElementById('notebody');
  if(!wrap||!ta||ta.tagName!=='TEXTAREA') return;
  const rect=wrap.getBoundingClientRect();
  let w=e.clientX-rect.left;
  w=Math.max(220,Math.min(w,rect.width-260));
  ta.style.flex='0 0 '+w+'px';
});
window.addEventListener('mouseup',()=>{
  if(!_splitDragging) return;
  _splitDragging=false;
  document.body.style.userSelect='';
  const h=document.getElementById('splith'); if(h) h.classList.remove('active');
  const ta=document.getElementById('notebody');
  if(ta && ta.tagName==='TEXTAREA' && ta.style.flex){
    const w=parseInt(ta.style.flex.replace('0 0 ',''),10);
    if(w) localStorage.setItem('m2_split_w', String(w));
  }
});

function autoCloseBegin(ta){
  const pos=ta.selectionStart;
  if(pos!==ta.selectionEnd) return;
  const before=ta.value.slice(0,pos);
  const m=before.match(/\\begin\{([a-zA-Z*]+)\}$/);
  if(!m) return;
  const env=m[1];
  const after=ta.value.slice(pos);
  if(/^\s*\n\s*\\end\{/.test(after)) return;
  const insertion='\n\n\\end{'+env+'}';
  ta.value=before+insertion+after;
  const newPos=before.length+1;
  ta.selectionStart=ta.selectionEnd=newPos;
}

async function saveNote(id){
  const v=getNoteBodyLatex(id);
  await mutate('PUT','notes',id,{content:v});
  toast('Note saved');
  await load();
}

const GC_TYPOS = {
  teh:'the', adn:'and', wich:'which', alot:'a lot', recieve:'receive', seperate:'separate',
  occured:'occurred', definately:'definitely', goverment:'government', untill:'until',
  begining:'beginning', neccessary:'necessary', accomodate:'accommodate', wether:'whether',
  becuase:'because', thier:'their', freind:'friend', arguement:'argument', enviroment:'environment',
  concious:'conscious', tommorow:'tomorrow', existance:'existence', independant:'independent',
  meme:'même', deja:'déjà', tres:'très', apres:'après'
};
function gcFixSegment(text){
  let t=text;
  t=t.replace(/[ \t]{2,}/g,' ');
  t=t.replace(/ +([,.])/g,'$1');
  t=t.replace(/([,.;:!?])(?=[A-Za-zÀ-ÿ])/g,'$1 ');
  t=t.replace(/([!?,;:])\1+/g,'$1');
  t=t.replace(/\.{4,}/g,'...');
  t=t.replace(/\b(\w+)([ \t]+\1)+\b/gi,'$1');
  t=t.replace(/\b[a-zA-Zà-ÿ]+\b/g, w=>{
    const fix=GC_TYPOS[w.toLowerCase()];
    if(!fix) return w;
    return (w[0]===w[0].toUpperCase()) ? fix[0].toUpperCase()+fix.slice(1) : fix;
  });
  return t;
}
function gcTokenize(text){
  const re=/\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\$[^$\n]+?\$|\\\([\s\S]+?\\\)/g;
  const out=[]; let last=0, m;
  while((m=re.exec(text))){
    if(m.index>last) out.push({type:'text',raw:text.slice(last,m.index)});
    out.push({type:'math',raw:m[0]});
    last=re.lastIndex;
  }
  if(last<text.length) out.push({type:'text',raw:text.slice(last)});
  return out;
}
function gcFixLine(line){
  const tokens=gcTokenize(line);
  let atStart=true;
  const parts=tokens.map(t=>{
    if(t.type==='math') return t.raw;
    const seg=gcFixSegment(t.raw);
    let result='', i=0;
    while(i<seg.length){
      if(atStart){
        let j=i;
        while(j<seg.length && /\s/.test(seg[j])) j++;
        result+=seg.slice(i,j);
        if(j<seg.length){
          const ch=seg[j];
          result += /[a-zà-ÿ]/.test(ch) ? ch.toUpperCase() : ch;
          i=j+1; atStart=false;
        } else i=j;
        continue;
      }
      const ch=seg[i];
      result+=ch;
      if('.!?'.includes(ch)) atStart=true;
      else if(!/\s/.test(ch)) atStart=false;
      i++;
    }
    return result;
  });
  return parts.join('').replace(/[ \t]+$/,'');
}
function gcFixText(full){
  return full.split('\n').map(line=>/^\s*\\/.test(line) ? line : gcFixLine(line)).join('\n');
}
function gcApply(ta){
  const before=ta.value, after=gcFixText(before);
  if(after===before) return false;
  const pos=ta.selectionStart;
  let prefix=0; const maxP=Math.min(before.length, after.length);
  while(prefix<maxP && before[prefix].toLowerCase()===after[prefix].toLowerCase()) prefix++;
  let suffix=0; const maxS=Math.min(before.length-prefix, after.length-prefix);
  while(suffix<maxS && before[before.length-1-suffix].toLowerCase()===after[after.length-1-suffix].toLowerCase()) suffix++;
  let newPos;
  if(pos<=prefix) newPos=pos;
  else if(before.length-pos<=suffix) newPos=after.length-(before.length-pos);
  else newPos=prefix;
  ta.value=after;
  ta.selectionStart=ta.selectionEnd=Math.max(0,Math.min(newPos, after.length));
  return true;
}
let _gcTimer=null;
const DICT_LANGS=[
  ['fr-FR','Français'],['en-US','English (US)'],['en-GB','English (UK)'],
  ['ar-SA','العربية'],['zh-CN','中文（简体）'],['zh-TW','中文（繁體）'],
  ['es-ES','Español'],['de-DE','Deutsch'],['it-IT','Italiano'],
  ['pt-PT','Português'],['ru-RU','Русский'],['ja-JP','日本語'],
  ['ko-KR','한국어'],['hi-IN','हिन्दी'],['nl-NL','Nederlands'],
  ['tr-TR','Türkçe'],
];
function dictLang(){ return localStorage.getItem('m2_dict_lang')||'fr-FR'; }
let DICTATION=null;
function insertDictatedText(ta,text){
  if(ta.tagName!=='TEXTAREA'){
    ta.focus();
    document.execCommand('insertText',false,text);
    return ta.innerHTML;
  }
  const s=ta.selectionStart, e=ta.selectionEnd;
  const val=ta.value;
  const needsSpace = s>0 && !!val[s-1] && !/\s/.test(val[s-1]);
  const insert=(needsSpace?' ':'')+text;
  ta.value=val.slice(0,s)+insert+val.slice(e);
  const pos=s+insert.length;
  ta.setSelectionRange(pos,pos);
  return ta.value;
}
function stopDictation(){
  if(DICTATION){
    DICTATION.active=false;
    try{ DICTATION.recognition.stop(); }catch(err){}
    DICTATION=null;
  }
  const btn=document.getElementById('micBtn');
  if(btn){ btn.classList.remove('rec'); btn.innerHTML='🎤 Dictate'; }
}
function toggleDictation(id){
  if(DICTATION && DICTATION.active){ stopDictation(); return; }
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!SR){ toast('Voice dictation is not supported in this browser'); return; }
  const ta=document.getElementById('notebody');
  const btn=document.getElementById('micBtn');
  if(!ta) return;
  const rec=new SR();
  rec.lang=dictLang();
  rec.continuous=true;
  rec.interimResults=true;
  rec.onresult=(e)=>{
    for(let i=e.resultIndex;i<e.results.length;i++){
      const res=e.results[i];
      if(!res.isFinal) continue;
      let txt=(res[0].transcript||'').trim();
      if(!txt) continue;
      txt=txt.charAt(0).toUpperCase()+txt.slice(1);
      insertDictatedText(ta,txt+' ');
      if(ta.tagName==='TEXTAREA') onNoteInput(id); else onNoteWordInput(id);
    }
  };
  rec.onerror=(e)=>{
    if(e.error==='no-speech'||e.error==='aborted') return;
    toast('Dictation error: '+e.error);
    stopDictation();
  };
  rec.onend=()=>{
    if(DICTATION && DICTATION.active && DICTATION.id===id){
      try{ DICTATION.recognition.start(); }catch(err){ stopDictation(); }
    }
  };
  DICTATION={recognition:rec,id,active:true};
  try{ rec.start(); }catch(err){ toast('Could not start dictation'); DICTATION=null; return; }
  if(btn){ btn.classList.add('rec'); btn.innerHTML='⏺ Listening…'; }
}
function onNoteInput(id){
  const ta=document.getElementById('notebody');
  if(ta && ta.tagName==='TEXTAREA') autoCloseBegin(ta);
  const s=document.getElementById('notestatus');
  if(s) s.textContent='Unsaved changes…';
  clearTimeout(_compileTimer);
  _compileTimer=setTimeout(()=>compileNotePdf(id,false), 1500);
  if(localStorage.getItem('m2_gc_enabled')!=='0'){
    clearTimeout(_gcTimer);
    _gcTimer=setTimeout(()=>{
      const ta2=document.getElementById('notebody');
      if(ta2 && ta2.tagName==='TEXTAREA' && document.activeElement===ta2 && gcApply(ta2)){
        silentSaveNote(id);
        if(s) s.textContent='Auto-corrected · saved';
      }
    }, 2500);
  }
}
function onNoteWordInput(id){
  const s=document.getElementById('notestatus');
  if(s) s.textContent='Unsaved changes…';
  clearTimeout(_compileTimer);
  _compileTimer=setTimeout(()=>compileNotePdf(id,false), 1500);
}

async function silentSaveNote(id){
  const ta=document.getElementById('notebody');
  if(!ta) return null;
  const v=getNoteBodyLatex(id);
  await mutate('PUT','notes',id,{content:v});
  const n=(S.notes||[]).find(x=>x.id==id);
  if(n){ n.content=v; n.updated_at=new Date().toISOString(); }
  return v;
}

async function compileNotePdf(id, immediate){
  clearTimeout(_compileTimer);
  if(NOTESEL!=id) return;
  const myGen=++_compileGen;
  await silentSaveNote(id);
  if(myGen!==_compileGen || NOTESEL!=id) return;
  const pane=document.getElementById('pdfpane');
  const status=document.getElementById('notestatus');
  if(!pane) return;
  const hadPdf=!!pane.querySelector('iframe');
  if(!hadPdf) pane.innerHTML='<div class="empty">Compiling…</div>';
  try{
    const res=await fetch('/api/notes/'+id+'/pdf');
    if(myGen!==_compileGen || NOTESEL!=id) return;
    if(!res.ok){
      let msg='Compilation failed';
      try{ msg=(await res.json()).error||msg; }catch(e){}
      if(!hadPdf) pane.innerHTML='<pre class="pdferr">'+esc(msg)+'</pre>';
      if(status) status.textContent='Compile error';
      return;
    }
    const blob=await res.blob();
    if(myGen!==_compileGen || NOTESEL!=id){ return; }
    if(pane.dataset.blob) URL.revokeObjectURL(pane.dataset.blob);
    const url=URL.createObjectURL(blob);
    pane.dataset.blob=url;
    pane.innerHTML='<iframe src="'+url+'#view=FitH" title="PDF preview"></iframe>';
    if(status) status.textContent='Saved';
  }catch(e){
    if(myGen!==_compileGen || NOTESEL!=id) return;
    if(!hadPdf) pane.innerHTML='<div class="empty">Error: '+esc(e.message)+'</div>';
    if(status) status.textContent='';
  }
}

function downloadNotePdf(title){
  const pane=document.getElementById('pdfpane');
  if(!pane || !pane.dataset.blob){ toast('Wait for the compile to finish first'); return; }
  const a=document.createElement('a');
  a.href=pane.dataset.blob;
  a.download=(title||'note').replace(/[^a-z0-9-_]+/gi,'_')+'.pdf';
  document.body.appendChild(a); a.click(); a.remove();
}

const HW_FONTS = {
  delafield: {label:'Elegant antique',   family:"'Mrs Saint Delafield', cursive", google:'Mrs+Saint+Delafield',   size:1,   lh:2},
  petit:     {label:'Fine calligraphy',  family:"'Petit Formal Script', cursive", google:'Petit+Formal+Script',  size:1.15,lh:1.9},
  caveat:    {label:'Natural cursive',   family:"'Caveat', cursive",              google:'Caveat:wght@500;700',  size:0.95,lh:1.7},
  apple:     {label:'Casual handwriting',family:"'Homemade Apple', cursive",      google:'Homemade+Apple',       size:0.62,lh:2.1},
};
const HW_FONT_CSS_URL  = 'https://fonts.googleapis.com/css2?family='+
  Object.values(HW_FONTS).map(f=>f.google).join('&family=')+'&display=swap';
const HW_KATEX_BASE    = 'https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/';
const HW_KATEX_CSS_URL = HW_KATEX_BASE+'katex.min.css';
const HW_KATEX_JS_URL  = HW_KATEX_BASE+'katex.min.js';
let HW_DEPS_READY=null, HW_KATEX_CSS_TEXT='', HW_NOTE=null;
let HW_SETTINGS=Object.assign({paper:'grid',ink:'blue',irregular:40,seed:1,font:'delafield'},
  (()=>{ try{ return JSON.parse(localStorage.getItem('m2_hw_settings')||'{}'); }catch(e){ return {}; } })());
if(!HW_FONTS[HW_SETTINGS.font]) HW_SETTINGS.font='delafield';
function hwSaveSettings(){ localStorage.setItem('m2_hw_settings', JSON.stringify(HW_SETTINGS)); }

function hwLoadDeps(){
  if(HW_DEPS_READY) return HW_DEPS_READY;
  if(!document.getElementById('hwFontLink')){
    const l=document.createElement('link'); l.id='hwFontLink'; l.rel='stylesheet'; l.href=HW_FONT_CSS_URL;
    document.head.appendChild(l);
  }
  if(!document.getElementById('hwKatexCss')){
    const l=document.createElement('link'); l.id='hwKatexCss'; l.rel='stylesheet'; l.href=HW_KATEX_CSS_URL;
    document.head.appendChild(l);
  }
  const katexJsReady = window.katex ? Promise.resolve() : new Promise((resolve,reject)=>{
    const existing=document.getElementById('hwKatexJs');
    if(existing){ existing.addEventListener('load',resolve); existing.addEventListener('error',()=>reject(new Error('katex'))); return; }
    const s=document.createElement('script'); s.id='hwKatexJs'; s.src=HW_KATEX_JS_URL;
    s.onload=resolve; s.onerror=()=>reject(new Error('katex'));
    document.head.appendChild(s);
  });
  const katexCssTextReady = HW_KATEX_CSS_TEXT ? Promise.resolve() :
    fetch(HW_KATEX_CSS_URL).then(r=>r.text()).then(t=>{
      HW_KATEX_CSS_TEXT = t.replace(/url\((?!['"]?(?:https?:|data:))/g, 'url('+HW_KATEX_BASE);
    }).catch(()=>{});
  const fontReady = (document.fonts && document.fonts.load)
    ? Promise.all(Object.values(HW_FONTS).map(f=>document.fonts.load('40px '+f.family).catch(()=>{})))
    : Promise.resolve();
  HW_DEPS_READY = Promise.all([katexJsReady, katexCssTextReady, fontReady]);
  return HW_DEPS_READY;
}

function hwMulberry32(seed){
  return function(){
    seed|=0; seed=seed+0x6D2B79F5|0;
    let t=Math.imul(seed^seed>>>15,1|seed);
    t=t+Math.imul(t^t>>>7,61|t)^t;
    return ((t^t>>>14)>>>0)/4294967296;
  };
}
function hwParseBlocks(text){
  const lines=(text||'').replace(/\r\n/g,'\n').split('\n');
  const blocks=[]; let para=[];
  const flush=()=>{ if(para.length){ blocks.push({type:'p',text:para.join(' ')}); para=[]; } };
  for(const line of lines){
    if(!line.trim()){ flush(); continue; }
    const h=line.match(/^(#{1,3})\s+(.*)$/);
    if(h){ flush(); blocks.push({type:'h'+h[1].length,text:h[2]}); continue; }
    const li=line.match(/^\s*(?:[-*+]|\d+[.)])\s+(.*)$/);
    if(li){ flush(); blocks.push({type:'li',text:li[1]}); continue; }
    para.push(line.trim());
  }
  flush();
  return blocks;
}
function hwTokenizeMath(text){
  const re=/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\$([^$\n]+?)\$|\\\(([\s\S]+?)\\\)/g;
  const out=[]; let last=0, m;
  while((m=re.exec(text))){
    if(m.index>last) out.push({type:'text',value:text.slice(last,m.index)});
    if(m[1]!==undefined) out.push({type:'math',value:m[1],display:true});
    else if(m[2]!==undefined) out.push({type:'math',value:m[2],display:true});
    else if(m[3]!==undefined) out.push({type:'math',value:m[3],display:false});
    else out.push({type:'math',value:m[4],display:false});
    last=re.lastIndex;
  }
  if(last<text.length) out.push({type:'text',value:text.slice(last)});
  return out;
}
function hwWordSpan(word, rng, level){
  const rot=(rng()-0.5)*2*(0.6+level*0.05);
  const dy=(rng()-0.5)*2*(0.5+level*0.06);
  const dx=(rng()-0.5)*1*(level*0.03);
  const sc=1+(rng()-0.5)*0.02*(1+level*0.03);
  return '<span class="hw-word" style="display:inline-block;transform:translate('+dx.toFixed(2)+'px,'+dy.toFixed(2)+'px) rotate('+rot.toFixed(2)+'deg) scale('+sc.toFixed(3)+')">'+esc(word)+'</span>';
}
function hwRenderTextTokenHtml(text, rng, level){
  return text.split(/(\s+)/).map(part=> !part.trim() ? part.replace(/ /g,'&nbsp;') : hwWordSpan(part, rng, level)).join('');
}
function hwRenderMathHtml(value, display){
  if(!window.katex) return '<span class="hw-math-fallback">'+esc((display?'$$':'$')+value+(display?'$$':'$'))+'</span>';
  try{ return katex.renderToString(value, {throwOnError:false, displayMode:!!display}); }
  catch(e){ return '<span class="hw-math-fallback">'+esc(value)+'</span>'; }
}
function hwRenderBlockHtml(block, rng, level){
  const tokens=hwTokenizeMath(block.text);
  const inner=tokens.map(t=> t.type==='math'
    ? '<span class="hw-math">'+hwRenderMathHtml(t.value,t.display)+'</span>'
    : hwRenderTextTokenHtml(t.value, rng, level)
  ).join('');
  if(block.type==='h1'||block.type==='h2'||block.type==='h3') return '<div class="hw-h hw-'+block.type+'">'+inner+'</div>';
  if(block.type==='li') return '<div class="hw-li"><span class="hw-bullet">—</span><span>'+inner+'</span></div>';
  return '<p class="hw-p">'+inner+'</p>';
}
function hwPaperBg(paper){
  const map={
    grid:  {bg:'#fbfaf5', img:'linear-gradient(#d9d3c0 1px,transparent 1px),linear-gradient(90deg,#d9d3c0 1px,transparent 1px)', size:'26px 26px,26px 26px'},
    lines: {bg:'#fbfaf5', img:'linear-gradient(#c9c2ab 1px,transparent 1px),linear-gradient(90deg,transparent 53px,rgba(190,90,90,.5) 53px,rgba(190,90,90,.5) 55px,transparent 55px)', size:'100% 34px,100% 100%'},
    dots:  {bg:'#fbfaf5', img:'radial-gradient(#bdb59a 1.1px,transparent 1.1px)', size:'22px 22px'},
    cream: {bg:'#f2e8d0', img:'radial-gradient(ellipse at 30% 15%,rgba(255,255,255,.3),transparent 60%)', size:'100% 100%'},
  };
  return map[paper]||map.grid;
}
function hwInkColor(ink){ return {blue:'#1e3f7a',black:'#20201d',sepia:'#6b4423'}[ink]||'#1e3f7a'; }

function hwRenderPage(){
  const page=document.getElementById('hwPage');
  if(!page || !HW_NOTE) return;
  const rng=hwMulberry32((HW_SETTINGS.seed||1)*1000003+(HW_NOTE.id||0));
  const level=+HW_SETTINGS.irregular||0;
  const blocks=hwParseBlocks(HW_NOTE.content||'');
  const titleHtml='<div class="hw-h hw-h1">'+hwRenderTextTokenHtml(HW_NOTE.title||'(untitled)', rng, level)+'</div>';
  const body=blocks.length? blocks.map(b=>hwRenderBlockHtml(b,rng,level)).join('') : '<p class="hw-p" style="opacity:.6">(empty note)</p>';
  const bg=hwPaperBg(HW_SETTINGS.paper), ink=hwInkColor(HW_SETTINGS.ink);
  const font=HW_FONTS[HW_SETTINGS.font]||HW_FONTS.delafield;
  page.style.backgroundColor=bg.bg;
  page.style.backgroundImage=bg.img;
  page.style.backgroundSize=bg.size;
  page.style.color=ink;
  page.style.fontFamily=font.family;
  page.style.fontSize=(22*font.size).toFixed(1)+'px';
  page.style.lineHeight=String(font.lh);
  page.innerHTML=titleHtml+body;
}
function hwUpdateSettings(){
  HW_SETTINGS.font=document.getElementById('hwFontSel').value;
  HW_SETTINGS.paper=document.getElementById('hwPaper').value;
  HW_SETTINGS.ink=document.getElementById('hwInk').value;
  HW_SETTINGS.irregular=+document.getElementById('hwIrreg').value;
  document.getElementById('hwIrregVal').textContent=HW_SETTINGS.irregular+'%';
  hwSaveSettings();
  hwRenderPage();
}
function hwReshuffle(){ HW_SETTINGS.seed=(HW_SETTINGS.seed||1)+1; hwSaveSettings(); hwRenderPage(); }

async function openHandwrite(id){
  const n=(S.notes||[]).find(x=>x.id==id);
  if(!n){ toast('Note not found'); return; }
  HW_NOTE=n;
  document.getElementById('hwovl').classList.add('show');
  const fsel=document.getElementById('hwFontSel');
  if(!fsel.options.length){
    fsel.innerHTML=Object.entries(HW_FONTS).map(([k,f])=>'<option value="'+k+'">'+esc(f.label)+'</option>').join('');
  }
  fsel.value=HW_SETTINGS.font;
  document.getElementById('hwPaper').value=HW_SETTINGS.paper;
  document.getElementById('hwInk').value=HW_SETTINGS.ink;
  document.getElementById('hwIrreg').value=HW_SETTINGS.irregular;
  document.getElementById('hwIrregVal').textContent=HW_SETTINGS.irregular+'%';
  const status=document.getElementById('hwStatus');
  status.textContent='Loading font and math renderer…';
  hwRenderPage();
  try{ await hwLoadDeps(); hwRenderPage(); status.textContent=''; }
  catch(e){ status.textContent='Font / math renderer unavailable offline — simplified preview.'; }
}
function closeHandwrite(){ document.getElementById('hwovl').classList.remove('show'); }

function hwDrawPaperBgCanvas(ctx,w,h,paper){
  const bgColors={grid:'#fbfaf5',lines:'#fbfaf5',dots:'#fbfaf5',cream:'#f2e8d0'};
  ctx.fillStyle=bgColors[paper]||bgColors.grid;
  ctx.fillRect(0,0,w,h);
  if(paper==='grid'){
    ctx.strokeStyle='#d9d3c0'; ctx.lineWidth=1;
    for(let x=0;x<=w;x+=26){ ctx.beginPath();ctx.moveTo(x+0.5,0);ctx.lineTo(x+0.5,h);ctx.stroke(); }
    for(let y=0;y<=h;y+=26){ ctx.beginPath();ctx.moveTo(0,y+0.5);ctx.lineTo(w,y+0.5);ctx.stroke(); }
  } else if(paper==='lines'){
    ctx.strokeStyle='#c9c2ab'; ctx.lineWidth=1;
    for(let y=34;y<=h;y+=34){ ctx.beginPath();ctx.moveTo(0,y+0.5);ctx.lineTo(w,y+0.5);ctx.stroke(); }
    ctx.strokeStyle='rgba(190,90,90,.5)'; ctx.lineWidth=2;
    ctx.beginPath();ctx.moveTo(54,0);ctx.lineTo(54,h);ctx.stroke();
  } else if(paper==='dots'){
    ctx.fillStyle='#bdb59a';
    for(let x=11;x<w;x+=22) for(let y=11;y<h;y+=22){ ctx.beginPath();ctx.arc(x,y,1.1,0,Math.PI*2);ctx.fill(); }
  } else if(paper==='cream'){
    const g=ctx.createRadialGradient(w*0.3,h*0.15,10,w*0.3,h*0.15,Math.max(w,h)*0.6);
    g.addColorStop(0,'rgba(255,255,255,.3)'); g.addColorStop(1,'rgba(255,255,255,0)');
    ctx.fillStyle=g; ctx.fillRect(0,0,w,h);
  }
}
function hwFlattenBlockWords(block){
  const tokens=hwTokenizeMath(block.text);
  const words=[];
  tokens.forEach(t=>{
    if(t.type==='math') words.push({math:true,value:t.value});
    else t.value.split(/\s+/).filter(Boolean).forEach(w=>words.push({math:false,value:w}));
  });
  return words;
}
function hwCanvasWordJitter(ctx, word, x, y, rng, level, draw){
  const rot=(rng()-0.5)*2*(0.6+level*0.05)*Math.PI/180;
  const dy=(rng()-0.5)*2*(0.5+level*0.06);
  const dx=(rng()-0.5)*1*(level*0.03);
  const sc=1+(rng()-0.5)*0.02*(1+level*0.03);
  const w=ctx.measureText(word).width;
  if(draw){
    ctx.save();
    ctx.translate(x+dx,y+dy);
    ctx.rotate(rot);
    ctx.scale(sc,sc);
    ctx.fillText(word,0,0);
    ctx.restore();
  }
  return w*sc;
}
function hwRunLayout(ctx, note, settings, font, baseSize, pageWidth, margin, marginTop, draw){
  const contentWidth=pageWidth-margin*2;
  const startX=margin;
  let y=marginTop+baseSize*1.2;
  const level=+settings.irregular||0;
  const rng=hwMulberry32((settings.seed||1)*1000003+(note.id||0));

  function layoutWords(words, size, indent, extraGapAfter){
    const lh=size*font.lh;
    ctx.font=size.toFixed(1)+'px '+font.family;
    const spaceW=ctx.measureText(' ').width||size*0.28;
    let x=startX+indent;
    words.forEach(w=>{
      if(w.math){
        ctx.save();
        ctx.font='italic '+Math.round(size*0.92)+'px Georgia, serif';
        const mw=ctx.measureText(w.value).width;
        if(x>startX+indent && x+mw>startX+contentWidth){ x=startX+indent; y+=lh; }
        if(draw) ctx.fillText(w.value, x, y);
        ctx.restore();
        x+=mw+spaceW;
      } else {
        ctx.font=size.toFixed(1)+'px '+font.family;
        const plainW=ctx.measureText(w.value).width;
        if(x>startX+indent && x+plainW>startX+contentWidth){ x=startX+indent; y+=lh; }
        const usedW=hwCanvasWordJitter(ctx, w.value, x, y, rng, level, draw);
        x+=Math.max(usedW,plainW)+spaceW;
      }
    });
    y+=lh+(extraGapAfter||0);
  }

  layoutWords(hwFlattenBlockWords({text:note.title||'(untitled)'}), baseSize*1.7, 0, baseSize*0.5);
  const blocks=hwParseBlocks(note.content||'');
  if(!blocks.length) layoutWords([{math:false,value:'(empty note)'}], baseSize, 0, 0);
  blocks.forEach(b=>{
    const words=hwFlattenBlockWords(b);
    if(b.type==='h1') layoutWords(words, baseSize*1.7, 0, baseSize*0.3);
    else if(b.type==='h2') layoutWords(words, baseSize*1.35, 0, baseSize*0.25);
    else if(b.type==='h3') layoutWords(words, baseSize*1.15, 0, baseSize*0.2);
    else if(b.type==='li'){
      if(draw){ ctx.font=baseSize.toFixed(1)+'px '+font.family; ctx.fillText('—', startX, y); }
      layoutWords(words, baseSize, 22, baseSize*0.3);
    }
    else layoutWords(words, baseSize, 0, baseSize*0.5);
  });
  return y;
}
async function hwExportImage(){
  const status=document.getElementById('hwStatus');
  status.textContent='Generating image…';
  try{
    const note=HW_NOTE;
    if(!note) throw new Error('no note');
    const settings=HW_SETTINGS;
    const font=HW_FONTS[settings.font]||HW_FONTS.delafield;
    const baseSize=22*font.size;
    const pageWidthCss=640, marginCss=48, marginTopCss=56;

    const measureCanvas=document.createElement('canvas');
    measureCanvas.width=pageWidthCss; measureCanvas.height=4000;
    const mctx=measureCanvas.getContext('2d');
    const totalHeight=hwRunLayout(mctx, note, settings, font, baseSize, pageWidthCss, marginCss, marginTopCss, false);

    const scale=3;
    const finalH=Math.ceil(totalHeight)+40;
    const canvas=document.createElement('canvas');
    canvas.width=pageWidthCss*scale; canvas.height=finalH*scale;
    const ctx=canvas.getContext('2d');
    ctx.scale(scale,scale);
    hwDrawPaperBgCanvas(ctx, pageWidthCss, finalH, settings.paper);
    ctx.fillStyle=hwInkColor(settings.ink);
    ctx.textBaseline='alphabetic';
    hwRunLayout(ctx, note, settings, font, baseSize, pageWidthCss, marginCss, marginTopCss, true);

    canvas.toBlob(blob=>{
      if(!blob){ status.textContent='Image export failed.'; return; }
      const a=document.createElement('a');
      a.href=URL.createObjectURL(blob);
      a.download=(note.title||'note').replace(/[^a-z0-9-_]+/gi,'_')+'_handwritten.png';
      document.body.appendChild(a); a.click(); a.remove();
      status.textContent='Image exported ✓';
    },'image/png');
  }catch(e){
    status.textContent='Image export unavailable — try "Export PDF" instead.';
  }
}
function hwExportPdf(){ window.print(); }

function rWins(el){
  const W=(S.achievements||[]).filter(w=>match(w.title)).sort((a,b)=>(b.win_date||'')<(a.win_date||'')?-1:1);
  el.innerHTML=(W.length?W.map(w=>
    '<div class="card winrow" style="cursor:pointer" onclick="editWin(S.achievements.find(x=>x.id=='+w.id+'))">'+
    '<span class="ic">'+(WIN_ICONS[w.win_type]||'✦')+'</span>'+
    '<div style="flex:1"><h5>'+esc(w.title)+'</h5>'+
    (w.description?'<p>'+esc(w.description)+'</p>':'')+
    '<p style="margin-top:3px">'+esc(w.win_type)+(w.project_id?' · '+esc(projName(w.project_id)):'')+'</p></div>'+
    '<span class="d" style="font-size:12px;color:var(--mut);white-space:nowrap">'+fmtdy(w.win_date)+'</span></div>').join('')
    :'<div class="empty">Every proof counts — log your wins 🏆</div>');
}

function rBooks(el){
  const B=(S.books||[]).filter(b=>match(b.title+' '+b.author+' '+b.genre));
  el.innerHTML=(B.length?B.map(b=>
    '<div class="card winrow" style="cursor:pointer" onclick="editBook(S.books.find(x=>x.id=='+b.id+'))">'+
    '<span class="ic">📕</span><div style="flex:1"><h5>'+esc(b.title)+'</h5>'+
    '<p>'+esc(b.author)+(b.year?' · '+esc(b.year):'')+' · '+esc(b.genre)+'</p>'+
    (b.notes?'<p style="margin-top:3px">'+esc(b.notes)+'</p>':'')+'</div>'+
    '<div style="text-align:right"><div class="stars">'+'★'.repeat(b.rating||0)+'</div>'+
    '<div style="font-size:11.5px;color:var(--dim)">'+fmtdy(b.read_date)+'</div></div></div>').join('')
    :'<div class="empty">Your reading list is empty</div>');
}

function svgDonutChart(catTotals, size){
  const entries=Object.entries(catTotals).filter(([,v])=>v>0).sort((a,b)=>b[1]-a[1]);
  const total=entries.reduce((s,[,v])=>s+v,0);
  const r=size/2-14, cx=size/2, cy=size/2, circ=2*Math.PI*r;
  let offset=0;
  const circles=entries.map(([cat,v],i)=>{
    const frac=total?v/total:0, seg=frac*circ;
    const c='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="'+PROJCOLS[i%PROJCOLS.length]+
      '" stroke-width="20" stroke-dasharray="'+seg.toFixed(2)+' '+(circ-seg).toFixed(2)+
      '" stroke-dashoffset="'+(-offset).toFixed(2)+'" transform="rotate(-90 '+cx+' '+cy+')"/>';
    offset+=seg;
    return c;
  }).join('');
  const svg=total? '<svg width="'+size+'" height="'+size+'" viewBox="0 0 '+size+' '+size+'">'+circles+'</svg>'
    : '<svg width="'+size+'" height="'+size+'" viewBox="0 0 '+size+' '+size+'"><circle cx="'+cx+'" cy="'+cy+'" r="'+r+
      '" fill="none" stroke="var(--line)" stroke-width="20"/></svg>';
  return {svg, entries, total};
}
function rFinOverview(month){
  const X=(S.expenses||[]).filter(x=>(x.exp_date||'').startsWith(month));
  const totalSpent=X.reduce((s,x)=>s+(+x.amount||0),0);
  const catTotals={}; X.forEach(x=>{catTotals[x.category]=(catTotals[x.category]||0)+(+x.amount||0);});
  const B=(S.budget_items||[]).filter(b=>(b.month||'')===month);
  const totalBudgeted=B.reduce((s,b)=>s+(+b.budgeted||0),0);
  const remaining=totalBudgeted-totalSpent;
  const overCount=B.filter(b=>(catTotals[b.category]??(+b.actual||0))>+b.budgeted).length;
  const {svg,entries,total}=svgDonutChart(catTotals,170);
  const legend=entries.length? entries.map(([cat,v],i)=>
    '<div style="display:flex;align-items:center;gap:8px;font-size:12.5px;margin-bottom:6px">'+
    '<span style="width:10px;height:10px;border-radius:3px;background:'+PROJCOLS[i%PROJCOLS.length]+';flex:none"></span>'+
    '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(cat)+'</span>'+
    '<b>$'+v.toFixed(0)+'</b><span style="color:var(--dim);width:38px;text-align:right">'+(total?Math.round(100*v/total):0)+'%</span></div>'
  ).join('') : '<div class="empty" style="padding:10px 0">No expenses this month</div>';
  return '<div class="dashgrid" style="grid-template-columns:1fr 1fr;margin-bottom:16px">'+
    '<div class="card" style="padding:16px;display:flex;gap:20px;align-items:center">'+
      '<div style="flex:none">'+svg+'</div><div style="flex:1;min-width:0">'+legend+'</div>'+
    '</div>'+
    '<div class="panel">'+
      '<h3 class="sec">This month</h3>'+
      '<div class="kpis" style="grid-template-columns:repeat(2,1fr);gap:10px">'+
        kpi('$'+totalSpent.toFixed(0),'Spent','var(--red)')+
        kpi('$'+totalBudgeted.toFixed(0),'Budgeted','var(--acc2)')+
        kpi('$'+remaining.toFixed(0),'Remaining',remaining<0?'var(--red)':'var(--green)')+
        kpi(overCount,'Over budget','var(--amber)')+
      '</div></div>'+
  '</div>';
}
function rFinance(el){
  const month=DAILYDATE.slice(0,7);
  let h='<div class="fintabs">'+['overview','expenses','budget','debts'].map(t=>
    '<button class="fintab'+(FINTAB===t?' on':'')+'" onclick="FINTAB=\''+t+'\';render()">'+t[0].toUpperCase()+t.slice(1)+'</button>').join('')+'</div>';
  if(FINTAB==='overview'){
    h+=rFinOverview(month);
  } else if(FINTAB==='expenses'){
    const X=(S.expenses||[]).filter(x=>match(x.title)&&(x.exp_date||'').startsWith(month)).sort((a,b)=>b.exp_date<a.exp_date?-1:1);
    const tot=X.reduce((s,x)=>s+(+x.amount||0),0);
    h+='<div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">'+
      '<input type="month" value="'+month+'" onchange="DAILYDATE=this.value+\'-01\';render()">'+
      '<span class="chip" style="background:var(--accbg);color:var(--acc2);font-size:13px">Total: $'+tot.toFixed(2)+'</span></div>';
    h+='<div class="card" style="overflow:hidden"><table class="tbl"><tr><th>Date</th><th>Title</th><th>Category</th><th style="text-align:right">Amount</th></tr>'+
      X.map(x=>'<tr style="cursor:pointer" onclick="editExpense(S.expenses.find(e=>e.id=='+x.id+'))"><td>'+fmtd(x.exp_date)+'</td><td>'+esc(x.title)+'</td>'+
      '<td><span class="chip" style="background:var(--panel3);color:var(--mut)">'+esc(x.category)+'</span></td>'+
      '<td style="text-align:right;font-weight:600">$'+(+x.amount).toFixed(2)+'</td></tr>').join('')+'</table>'+
      (X.length?'':'<div class="empty">No expenses this month</div>')+'</div>';
  } else if(FINTAB==='budget'){
    const B=(S.budget_items||[]).filter(b=>(b.month||'')===month);
    const spent={};(S.expenses||[]).filter(x=>(x.exp_date||'').startsWith(month)).forEach(x=>{spent[x.category]=(spent[x.category]||0)+(+x.amount||0);});
    h+='<div style="margin-bottom:14px"><input type="month" value="'+month+'" onchange="DAILYDATE=this.value+\'-01\';render()"></div>';
    if(!B.length){
      h+='<div class="empty">No budget for '+month+' — add lines with + New</div>';
    } else {
      let sumBudgeted=0, sumActual=0;
      const rows=B.map(b=>{
        const budgeted=+b.budgeted||0, act=spent[b.category]??(+b.actual||0);
        const variance=act-budgeted, pc=budgeted?Math.round(100*act/budgeted):(act>0?100:0);
        sumBudgeted+=budgeted; sumActual+=act;
        const over=variance>0;
        return '<tr style="cursor:pointer" onclick="editBudget(S.budget_items.find(x=>x.id=='+b.id+'))">'+
          '<td>'+esc(b.category)+'</td>'+
          '<td style="text-align:right">$'+budgeted.toFixed(2)+'</td>'+
          '<td style="text-align:right">$'+act.toFixed(2)+'</td>'+
          '<td style="text-align:right;font-weight:600;color:'+(over?'var(--red)':'var(--green)')+'">'+(over?'+':'')+'$'+variance.toFixed(2)+'</td>'+
          '<td style="text-align:right;white-space:nowrap"><span style="color:'+(over?'var(--red)':'var(--mut)')+'">'+pc+'%</span> '+
            '<span class="pbar" style="display:inline-block;width:44px;vertical-align:middle;margin-left:6px">'+
            '<i style="width:'+Math.min(100,pc)+'%;background:'+(over?'var(--red)':'linear-gradient(90deg,var(--acc),var(--acc2))')+'"></i></span></td>'+
          '</tr>';
      }).join('');
      const totVariance=sumActual-sumBudgeted, totOver=totVariance>0;
      const totPc=sumBudgeted?Math.round(100*sumActual/sumBudgeted):0;
      h+='<div class="card" style="overflow:hidden"><table class="tbl">'+
        '<tr><th>Category</th><th style="text-align:right">Budgeted</th><th style="text-align:right">Actual</th>'+
        '<th style="text-align:right">Variance</th><th style="text-align:right">% used</th></tr>'+
        rows+
        '<tr style="font-weight:700;border-top:2px solid var(--line2)">'+
          '<td>Total</td>'+
          '<td style="text-align:right">$'+sumBudgeted.toFixed(2)+'</td>'+
          '<td style="text-align:right">$'+sumActual.toFixed(2)+'</td>'+
          '<td style="text-align:right;color:'+(totOver?'var(--red)':'var(--green)')+'">'+(totOver?'+':'')+'$'+totVariance.toFixed(2)+'</td>'+
          '<td style="text-align:right">'+totPc+'%</td>'+
        '</tr>'+
        '</table></div>';
    }
  } else {
    const D=(S.debts||[]);
    const open=D.filter(d=>!d.paid).reduce((s,d)=>s+(+d.amount||0),0);
    h+='<div style="margin-bottom:14px"><span class="chip" style="background:var(--redbg);color:var(--red);font-size:13px">Outstanding: $'+open.toFixed(2)+'</span></div>';
    h+=D.length?D.map(d=>'<div class="card winrow" style="cursor:pointer" onclick="editDebt(S.debts.find(x=>x.id=='+d.id+'))">'+
      '<span class="ic">'+(d.paid?'✅':'💳')+'</span><div style="flex:1"><h5'+(d.paid?' style="text-decoration:line-through;color:var(--dim)"':'')+'>'+esc(d.title)+'</h5>'+
      '<p>'+esc(d.creditor)+(d.due_date?' · due '+fmtdy(d.due_date):'')+'</p></div>'+
      '<b style="font-size:14px">$'+(+d.amount).toFixed(2)+'</b></div>').join(''):'<div class="empty">No debts 🎉</div>';
  }
  el.innerHTML=h;
}

const TS_TABS=['goals','journal','mood'];
const TS_TAB_LABELS={goals:'Goals',journal:'Journal',mood:'Mental state'};
const MOOD_SCALE=[[1,'😞','Difficult'],[2,'😕','Low'],[3,'😐','Neutral'],[4,'🙂','Good'],[5,'😄','Great']];
function moodEmoji(score){ const m=MOOD_SCALE.find(x=>x[0]==+score); return m?m[1]:'😐'; }
function moodLabel(score){ const m=MOOD_SCALE.find(x=>x[0]==+score); return m?m[2]:'Neutral'; }
function setTSTab(t){ TSTAB=t; localStorage.setItem('m2_tstab',t); render(); }
function rTS(el){
  let h='<div class="tsintro">A private space for your goals, personal growth notes, and how you\'ve been doing — just for you.</div>';
  h+='<div class="fintabs">'+TS_TABS.map(t=>
    '<button class="fintab'+(TSTAB===t?' on':'')+'" onclick="setTSTab(\''+t+'\')">'+TS_TAB_LABELS[t]+'</button>').join('')+'</div>';
  if(TSTAB==='goals') h+=rTSGoals();
  else if(TSTAB==='journal') h+=rTSJournal();
  else h+=rTSMood();
  el.innerHTML=h;
}

function rTSGoals(){
  const order={active:0,paused:1,done:2};
  const G=(S.ts_entries||[]).filter(t=>t.kind==='goal' && match(t.title+' '+t.content))
    .sort((a,b)=>(order[a.status]??0)-(order[b.status]??0) || String(a.target_date||'9999').localeCompare(String(b.target_date||'9999')));
  if(!G.length) return '<div class="empty">No goals yet — add one with + New</div>';
  return G.map(g=>{
    const ic=g.status==='done'?'✅':g.status==='paused'?'⏸':'🎯';
    return '<div class="card winrow" style="cursor:pointer" onclick="editGoal(S.ts_entries.find(x=>x.id=='+g.id+'))">'+
      '<span class="ic">'+ic+'</span><div style="flex:1">'+
      '<h5'+(g.status==='done'?' style="text-decoration:line-through;color:var(--dim)"':'')+'>'+esc(g.title)+'</h5>'+
      (g.content?'<p>'+esc(g.content)+'</p>':'')+
      '</div>'+
      (g.target_date?'<span class="chip" style="background:var(--panel3);color:var(--mut)">'+fmtd(g.target_date)+'</span>':'')+
      '</div>';
  }).join('');
}
function editGoal(g){
  g=g||{kind:'goal',status:'active'};
  modal(g.id?'Edit goal':'New goal',[
    {k:'title',label:'Title'},
    {k:'content',label:'Notes',type:'textarea'},
    {k:'status',label:'Status',type:'select',options:[['active','Active'],['paused','Paused'],['done','Done']],def:'active',half:true},
    {k:'target_date',label:'Target date',type:'date',half:true},
  ],g,o=>{ o.kind='goal'; return save('ts_entries',o,g.id).then(()=>toast('Saved')); },
  g.id?()=>del('ts_entries',g.id).then(()=>toast('Deleted')):null);
}

function rTSJournal(){
  const J=(S.ts_entries||[]).filter(t=>t.kind==='journal' && match(t.title+' '+t.content))
    .sort((a,b)=>String(b.entry_date||b.created_at||'')<String(a.entry_date||a.created_at||'')?-1:1);
  if(!J.length) return '<div class="empty">No journal entries yet — write your first one with + New</div>';
  return J.map(j=>{
    const preview=(j.content||'').replace(/\s+/g,' ').trim().slice(0,160);
    return '<div class="card winrow" style="cursor:pointer;align-items:flex-start" onclick="editJournalEntry(S.ts_entries.find(x=>x.id=='+j.id+'))">'+
      '<span class="ic">📓</span><div style="flex:1">'+
      '<h5>'+esc(j.title||'Untitled entry')+'</h5>'+
      (preview?'<p>'+esc(preview)+((j.content||'').length>160?'…':'')+'</p>':'')+
      '</div>'+
      '<span class="chip" style="background:var(--panel3);color:var(--mut)">'+fmtd(j.entry_date||(j.created_at||'').slice(0,10))+'</span>'+
      '</div>';
  }).join('');
}
function editJournalEntry(j){
  j=j||{kind:'journal',entry_date:iso(new Date()),title:'Journal — '+fmtdy(iso(new Date()))};
  modal(j.id?'Edit journal entry':'New journal entry',[
    {k:'title',label:'Title'},
    {k:'entry_date',label:'Date',type:'date',def:iso(new Date())},
    {k:'content',label:"What's on your mind?",type:'textarea',rows:8},
  ],j,o=>{ o.kind='journal'; return save('ts_entries',o,j.id).then(()=>toast('Saved')); },
  j.id?()=>del('ts_entries',j.id).then(()=>toast('Deleted')):null);
}

function moodSparklinePoints(scores,width,height){
  const n=scores.length;
  if(!n) return [];
  return scores.map((s,i)=>{
    const x = n===1 ? width/2 : (i/(n-1))*width;
    const num = (s===null||s===undefined||s==='') ? NaN : Number(s);
    const clamped = Number.isFinite(num) ? Math.max(1,Math.min(5,num)) : 3;
    const y = height - ((clamped-1)/4)*height;
    return {x,y,score:clamped};
  });
}
function svgMoodSparkline(scores){
  const W=560,H=60;
  const pts=moodSparklinePoints(scores,W,H);
  if(pts.length<2) return '';
  const path=pts.map(p=>p.x.toFixed(1)+','+p.y.toFixed(1)).join(' ');
  const dots=pts.map(p=>'<circle cx="'+p.x.toFixed(1)+'" cy="'+p.y.toFixed(1)+'" r="3" fill="var(--acc2)"/>').join('');
  return '<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="'+H+'" preserveAspectRatio="none" style="overflow:visible">'+
    '<polyline points="'+path+'" fill="none" stroke="var(--acc2)" stroke-width="2"/>'+dots+'</svg>';
}
function rTSMood(){
  const M=(S.ts_entries||[]).filter(t=>t.kind==='mood')
    .sort((a,b)=>String(a.created_at||'')<String(b.created_at||'')?-1:1);
  let h='';
  if(M.length>=2){
    const recent=M.slice(-20);
    h+='<div class="card" style="padding:14px 18px;margin-bottom:14px"><h3 class="sec">Trend · last '+recent.length+' check-ins</h3>'+
      svgMoodSparkline(recent.map(m=>m.mood_score))+'</div>';
  }
  const list=[...M].reverse();
  if(!list.length) return h+'<div class="empty">No check-ins yet — log how you\'re doing with + New</div>';
  h+=list.map(m=>
    '<div class="card winrow" style="cursor:pointer" onclick="editMoodEntry(S.ts_entries.find(x=>x.id=='+m.id+'))">'+
    '<span class="ic">'+moodEmoji(m.mood_score)+'</span><div style="flex:1">'+
    '<h5>'+moodLabel(m.mood_score)+'</h5>'+
    (m.content?'<p>'+esc(m.content)+'</p>':'')+
    '</div><span class="chip" style="background:var(--panel3);color:var(--mut)">'+fmtdy((m.created_at||'').slice(0,10))+'</span>'+
    '</div>').join('');
  return h;
}
function editMoodEntry(m){
  m=m||{kind:'mood',mood_score:3};
  modal(m.id?'Edit check-in':'How are you doing?',[
    {k:'mood_score',label:'Mood',type:'select',options:MOOD_SCALE.map(x=>[x[0],x[1]+' '+x[2]]),def:3},
    {k:'content',label:'Anything on your mind? (optional)',type:'textarea',rows:4},
  ],m,o=>{ o.kind='mood'; o.entry_date=o.entry_date||iso(new Date()); return save('ts_entries',o,m.id).then(()=>toast('Logged')); },
  m.id?()=>del('ts_entries',m.id).then(()=>toast('Deleted')):null);
}

async function syncGcal(){
  const t=document.getElementById('synctxt'); t.textContent='Syncing…';
  const r=await api('POST','/api/gcal/sync');
  t.textContent='Sync Google Calendar';
  if(r.ok){ toast('Google Calendar: '+r.imported+' new, '+r.updated+' updated'); load(); }
  else alert(r.error);
}

async function backupDrive(){
  const t=document.getElementById('drivetxt'); t.textContent='Backing up…';
  const r=await api('POST','/api/drive/backup');
  t.textContent='Backup to Drive';
  if(r.ok) toast('Backed up to Drive at '+r.time);
  else alert(r.error);
}

let ICLOUD_STATUS={connected:false,email:''};
async function loadIcloudStatus(){
  try{ ICLOUD_STATUS=await api('GET','/api/icloud/status'); }catch(e){}
  updateIcloudButton();
}
function updateIcloudButton(){
  const t=document.getElementById('icloudtxt');
  if(t) t.textContent=ICLOUD_STATUS.connected?'Sync iCloud Calendar':'Connect iCloud Calendar';
}
async function syncOrConnectIcloud(){
  if(!ICLOUD_STATUS.connected){ openIcloudSettings(); return; }
  const t=document.getElementById('icloudtxt'); t.textContent='Syncing…';
  const r=await api('POST','/api/icloud/sync');
  updateIcloudButton();
  if(r.ok){ toast('iCloud Calendar: '+r.imported+' new, '+r.updated+' updated'); load(); }
  else alert(r.error);
}
function openIcloudSettings(){
  modal(ICLOUD_STATUS.connected?'iCloud Calendar (connected)':'Connect iCloud Calendar',[
    {k:'email',label:'Apple ID (email)',def:ICLOUD_STATUS.email||''},
    {k:'app_password',label:'App-specific password — from appleid.apple.com, not your real Apple ID password',type:'password'},
  ],{},async o=>{
    const r=await api('POST','/api/icloud/save_creds',o);
    if(!r.ok){ alert(r.error||'Could not save iCloud credentials'); return; }
    await loadIcloudStatus();
    toast('iCloud connected — syncing…');
    syncOrConnectIcloud();
  },
  ICLOUD_STATUS.connected?async()=>{
    await api('POST','/api/icloud/disconnect');
    await loadIcloudStatus();
    toast('iCloud Calendar disconnected');
  }:null);
}
loadIcloudStatus();


const THEMES=['terracotta','ember','aurora','autumn','cyber','ivory','midnight','ocean','sakura','nord','forest','printemps','ete','automne','hiver'];
const THEME_NEXT_LABEL={aurora:'Automne mode',autumn:'Cyber mode',cyber:'Aurora mode'};
function applyTheme(t){
  if(t==='minimal') t='autumn';
  if(!THEMES.includes(t)) t='aurora';
  document.documentElement.dataset.theme=t;
  localStorage.setItem('m2theme',t);
  const tc=document.querySelector('meta[name=theme-color]'); if(tc) tc.content=({autumn:'#1F4E37',cyber:'#00f0ff',ivory:'#8C6A2F',midnight:'#0A0A0D',ocean:'#06111D',sakura:'#B03A66',nord:'#3B5BA9',forest:'#0B1410',printemps:'#a02c5a',ete:'#8a4b00',automne:'#8f3410',hiver:'#0e1a2c',terracotta:'#D97757',ember:'#262624'})[t]||'#8a6dff';
  const b=document.getElementById('themetxt');
  if(b){ const n=THEMES[(THEMES.indexOf(t)+1)%THEMES.length]; b.textContent=(THEME_NEXT_LABEL[n]&&THEME_NEXT_LABEL[t])||((typeof DEMO_THEMES!=='undefined'&&DEMO_THEMES[n]?DEMO_THEMES[n].label:n)+' mode'); }
}
function toggleTheme(){
  let cur=localStorage.getItem('m2theme')||'aurora';
  if(cur==='minimal') cur='autumn';
  const next=THEMES[(THEMES.indexOf(cur)+1)%THEMES.length];
  themeSwitch(next);
}
try{ if(!localStorage.getItem('m2_tc1')){ localStorage.setItem('m2_tc1','1'); localStorage.setItem('m2theme','terracotta'); localStorage.removeItem('m2accent'); const _r=document.documentElement.style; _r.removeProperty('--acc'); _r.removeProperty('--acc2'); } }catch(e){}
applyTheme(localStorage.getItem('m2theme')||'terracotta');
(function(){
  const tc=()=>{ const t=document.documentElement.dataset.theme; return t==='terracotta'||t==='ember'; };
  const still=matchMedia('(prefers-reduced-motion: reduce)');
  let card=null, px=0, py=0, raf=0;
  document.addEventListener('pointermove',e=>{ if(!tc()||still.matches) return; px=e.clientX; py=e.clientY; const t=e.target.closest&&e.target.closest('.card,.kpi,.panel');
    if(card&&card!==t){ card.style.removeProperty('--mx'); card.style.removeProperty('--my'); } card=t;
    if(card&&!raf) raf=requestAnimationFrame(()=>{ raf=0; if(!card) return; const r=card.getBoundingClientRect(); card.style.setProperty('--mx',(px-r.left)+'px'); card.style.setProperty('--my',(py-r.top)+'px'); }); },{passive:true});
  document.addEventListener('pointerdown',e=>{ const d=document.documentElement.style; d.setProperty('--vtx',e.clientX+'px'); d.setProperty('--vty',e.clientY+'px');
    if(!tc()||still.matches) return; const b=e.target.closest&&e.target.closest('.btn'); if(!b) return; const r=b.getBoundingClientRect(), s=document.createElement('span');
    s.className='tc-ripple'; s.style.left=(e.clientX-r.left)+'px'; s.style.top=(e.clientY-r.top)+'px'; b.appendChild(s); setTimeout(()=>s.remove(),650); },{passive:true});
})();
if('requestIdleCallback' in window) requestIdleCallback(()=>{ try{ if(typeof rdKatex==='function') rdKatex(); }catch(e){} },{timeout:8000});


const V6_TITLES={inbox:'Inbox',planner:'Day Planner',timeline:'Timeline',radar:'arXiv Radar',biblio:'Bibliography',results:'Results Bank',review:'Weekly Review'};
Object.assign(TITLES,V6_TITLES);
function pad2(n){ return String(n).padStart(2,'0'); }
function hm2min(s){ const m=String(s||'').match(/^(\d{1,2}):(\d{2})/); return m?(+m[1])*60+(+m[2]):null; }
function min2hm(m){ m=Math.max(0,Math.round(m)); return pad2(Math.floor(m/60)%24)+':'+pad2(m%60); }
function addDays(ds,n){ const d=new Date(ds+'T00:00:00'); d.setDate(d.getDate()+n); return iso(d); }
function v6open(fn){ return 'onclick="'+fn+'"'; }
function byId(t,id){ return (S[t]||[]).find(x=>x.id==id); }
async function v6put(t,id,body){ const r=byId(t,id); if(r) Object.assign(r,body); return mutate('PUT',t,id,body); }

const V6_DAYS={sunday:0,sun:0,dimanche:0,dim:0,monday:1,mon:1,lundi:1,lun:1,tuesday:2,tue:2,mardi:2,mar:2,wednesday:3,wed:3,mercredi:3,mer:3,thursday:4,thu:4,jeudi:4,jeu:4,friday:5,fri:5,vendredi:5,ven:5,saturday:6,sat:6,samedi:6,sam:6};
function v6DayWord(w){
  w=w.toLowerCase(); const t=new Date(); t.setHours(0,0,0,0);
  if(['today','aujourd\'hui','auj','tdy'].includes(w)) return iso(t);
  if(['tomorrow','demain','tmr'].includes(w)){ t.setDate(t.getDate()+1); return iso(t); }
  if(w in V6_DAYS){ const k=V6_DAYS[w]; t.setDate(t.getDate()+(((k-t.getDay())+7)%7||7)); return iso(t); }
  if(/^\d{4}-\d{2}-\d{2}$/.test(w)) return w;
  return null;
}
function parseCapture(raw){
  let s=' '+String(raw||'')+' ';
  const o={tags:[],priority:'Normal',estimate_min:0,planned_date:'',planned_time:'',deadline:'',project_id:''};
  s=s.replace(/\s#([\p{L}\w\-]+)/gu,(m,t)=>{o.tags.push(t);return ' ';});
  s=s.replace(/\s!(high|urgent|important|haute|low|basse|normal)\b/gi,(m,p)=>{p=p.toLowerCase();o.priority=['low','basse'].includes(p)?'Low':p==='normal'?'Normal':'High';return ' ';});
  s=s.replace(/\s@([\p{L}\w\-]+)/gu,(m,p)=>{const pl=p.toLowerCase();const P=(S.projects||[]).find(x=>String(x.title).toLowerCase().split(/[\s—\-:]+/).some(w=>w.startsWith(pl)))||(S.projects||[]).find(x=>String(x.title).toLowerCase().includes(pl));if(P){o.project_id=P.id;return ' ';}return m;});
  s=s.replace(/\s(?:due|deadline|avant|pour|before)\s+([\p{L}'\d\-]+)/giu,(m,w)=>{const d=v6DayWord(w);if(d){o.deadline=d;return ' ';}return m;});
  s=s.replace(/\s(?:at|à|a)\s+(\d{1,2})(?:[:h](\d{2})?)?(?=\s)/gi,(m,h,mm)=>{if(+h<24){o.planned_time=pad2(h)+':'+(mm||'00');return ' ';}return m;});
  s=s.replace(/\s(\d{1,2}):(\d{2})(?=\s)/g,(m,h,mm)=>{o.planned_time=pad2(h)+':'+mm;return ' ';});
  s=s.replace(/\s(\d+(?:[.,]\d+)?)\s?(h|hr|hrs|heures?|hours?)(\d{2})?(?=\s)/gi,(m,n,u,mm)=>{o.estimate_min=Math.round(parseFloat(n.replace(',','.'))*60)+(mm?+mm:0);return ' ';});
  s=s.replace(/\s(\d+)\s?(min|mins|minutes?|m)(?=\s)/gi,(m,n)=>{o.estimate_min=+n;return ' ';});
  s=s.replace(/\s(today|tomorrow|aujourd'hui|auj|demain|monday|tuesday|wednesday|thursday|friday|saturday|sunday|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|\d{4}-\d{2}-\d{2})(?=\s)/gi,(m,w)=>{const d=v6DayWord(w);if(d&&!o.planned_date){o.planned_date=d;return ' ';}return m;});
  o.title=s.replace(/\s+/g,' ').trim();
  if(o.planned_time&&!o.planned_date) o.planned_date=iso(new Date());
  return o;
}
function captureChips(o){
  const c=(txt,st)=>'<span class="chip" style="'+(st||'background:var(--panel3);color:var(--mut)')+'">'+txt+'</span>';
  return c('📝 '+esc(o.title||'…'),'background:var(--accbg);color:var(--acc2)')+
    (o.planned_date?c('📅 '+fmtd(o.planned_date)+(o.planned_time?' '+o.planned_time:''),'background:var(--tealbg);color:var(--teal)'):'')+
    (o.deadline?c('⏱ due '+fmtd(o.deadline),'background:var(--redbg);color:var(--red)'):'')+
    (o.estimate_min?c('~'+fmtDur(o.estimate_min)):'')+
    (o.priority!=='Normal'?c(o.priority,'background:'+(o.priority==='High'?'var(--redbg);color:var(--red)':'var(--greenbg);color:var(--green)')):'')+
    (o.project_id?c('📁 '+esc(projName(o.project_id))):'')+o.tags.map(t=>c('#'+esc(t))).join('');
}
function captureToTask(o){
  return {title:o.title||'Untitled',status:'To Do',priority:o.priority,task_type:/^(read|lire)\b/i.test(o.title)?'Reading':/^(write|rédiger|ecrire|écrire)\b/i.test(o.title)?'Writing':'Other',
    tags:o.tags.join(','),estimate_min:o.estimate_min||0,planned_date:o.planned_date,planned_time:o.planned_time,deadline:o.deadline,project_id:o.project_id||null};
}

let PAL={sel:0,items:[]};
function v6Commands(){
  const go=v=>()=>setView(v);
  return [
    ['Go to Dashboard',go('dash'),'g d'],['Go to Tasks',go('board'),'g t'],['Go to Day Planner',go('planner'),'g p'],
    ['Go to Timeline',go('timeline'),'g y'],['Go to Meetings',go('agenda'),'g m'],['Go to Notes',go('notes'),'g n'],
    ['Go to VanoLib',go('vanolib'),'g v'],['Open VanoLib reader',()=>vlOpenReader(''),''],
    ['Go to Projects',go('projects'),''],['Go to Daily Tasks',go('daily'),''],
    ['New task',()=>editTask(),'n'],['New note',()=>editNoteMeta(),''],['New meeting',()=>editMeeting(null,iso(new Date())),''],
    ['New project',()=>editProject(),''],
    ['Start Focus / Zen session',()=>openFocusMode(),''],['Auto-plan today',()=>{setView('planner');autoPlanDay();},''],
    ['Switch theme (Aurora · Automne · Cyber)',()=>toggleTheme(),'t'],['Data health',()=>openDataHealth(),''],['Sync Google Calendar',()=>syncGcal(),''],
    ['Backup to Drive',()=>backupDrive(),''],['Keyboard shortcuts',()=>v6Shortcuts(),'?'],['Quotes — manage / collect',()=>qManage(),''],
  ];
}
function fuzzyScore(q,s){
  q=q.toLowerCase(); s=String(s||'').toLowerCase(); if(!q) return 1;
  const idx=s.indexOf(q); if(idx>=0) return 100-Math.min(idx,40)+(idx===0||s[idx-1]===' '?20:0);
  let i=0,sc=0,last=-2; for(let j=0;j<s.length&&i<q.length;j++){ if(s[j]===q[i]){ sc+=(j===last+1)?3:1; last=j; i++; } }
  return i===q.length?sc:-1;
}
function fuzzyMark(q,s){
  s=String(s||''); if(!q) return esc(s);
  const idx=s.toLowerCase().indexOf(q.toLowerCase());
  if(idx>=0) return esc(s.slice(0,idx))+'<mark>'+esc(s.slice(idx,idx+q.length))+'</mark>'+esc(s.slice(idx+q.length));
  let i=0,o=''; const ql=q.toLowerCase(); for(const ch of s){ if(i<ql.length&&ch.toLowerCase()===ql[i]){o+='<mark>'+esc(ch)+'</mark>';i++;} else o+=esc(ch); } return o;
}
function v6SearchPool(){
  const P=[];
  (S.tasks||[]).forEach(t=>P.push(['task',t.title,(projName(t.project_id)||t.task_type||'')+(t.status==='Done'?' · done':'')+(t.tags?' · #'+t.tags.split(',').join(' #'):''),()=>editTask(t),(t.description||'')]));
  (S.projects||[]).forEach(p=>P.push(['project',p.title,p.domain||p.status,()=>{PROJFILTER=p.id;setView('board');},p.description||'']));
  (S.notes||[]).forEach(n=>P.push(['note',n.title,(n.category||'')+(n.tags?' · #'+n.tags.split(',').join(' #'):''),()=>{NOTESEL=n.id;setView('notes');},String(n.content||'').slice(0,400)]));
  (S.meetings||[]).forEach(m=>P.push(['meeting',m.title,fmtdy(m.meeting_date)+' '+(m.start_time||m.meeting_time||''),()=>editMeeting(m),m.attendees||'']));
  (S.books||[]).forEach(b=>P.push(['book',b.title,b.author||'',()=>editBook(b),'']));
  (S.daily_tasks||[]).filter(d=>d.task_date>=addDays(iso(new Date()),-7)).forEach(d=>P.push(['daily',d.title,fmtd(d.task_date)+' · '+d.category,()=>{DAILYDATE=d.task_date;setView('daily');},'']));
  return P;
}
function ensurePalette(){
  if(document.getElementById('palovl')) return;
  const d=document.createElement('div'); d.id='palovl';
  d.innerHTML='<div class="pal-box"><input id="palin" autocomplete="off" spellcheck="false" placeholder="Search everything · > commands · + new task · Shift+Enter → Inbox"><div id="palres"></div>'+
    '<div class="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span><kbd>+</kbd> task</span><span><kbd>⇧↵</kbd> to inbox</span><span><kbd>&gt;</kbd> commands</span><span><kbd>Esc</kbd></span></div></div>';
  document.body.appendChild(d);
  d.addEventListener('mousedown',e=>{ if(e.target===d) closePalette(); });
  const inp=d.querySelector('#palin');
  inp.addEventListener('input',()=>{PAL.sel=0;palRender();});
  inp.addEventListener('keydown',e=>{
    if(e.key==='ArrowDown'){ PAL.sel=Math.min(PAL.items.length-1,PAL.sel+1); palMark(true); e.preventDefault(); }
    else if(e.key==='ArrowUp'){ PAL.sel=Math.max(0,PAL.sel-1); palMark(true); e.preventDefault(); }
    else if(e.key==='Enter'){ e.preventDefault(); const v=inp.value.trim();
      if(e.shiftKey&&v){ const t=v.replace(/^\+\s*/,''); closePalette(); mutate('POST','inbox',null,{title:t,source:'Quick capture'}).then(load).then(()=>toast('Added to Inbox')); return; }
      const f=PAL.items[PAL.sel]; if(f){ closePalette(); setTimeout(f,0); } }
    else if(e.key==='Escape'){ e.preventDefault(); e.stopPropagation(); closePalette(); }
  });
}
function openPalette(prefill){
  ensurePalette(); const d=document.getElementById('palovl'), inp=document.getElementById('palin');
  d.classList.add('show'); inp.value=prefill||''; PAL.sel=0; palRender(); inp.focus();
}
function closePalette(){ const d=document.getElementById('palovl'); if(d) d.classList.remove('show'); const i=document.getElementById('palin'); if(i) i.blur(); }
function palRender(){
  const q=document.getElementById('palin').value, R=document.getElementById('palres'); PAL.items=[];
  if(q.startsWith('+')){
    const o=parseCapture(q.slice(1));
    R.innerHTML='<div class="pal-head">New task</div><div class="pal-capture">'+captureChips(o)+'<div class="demo-note" style="margin-top:8px">↵ create task · ⇧↵ send to Inbox · tokens: <b>friday</b> <b>at 14:00</b> <b>2h</b> <b>#tag</b> <b>!high</b> <b>@project</b> <b>due mon</b></div></div>';
    PAL.items=[async()=>{ await mutate('POST','tasks',null,captureToTask(o)); await load(); toast('Task created: '+o.title); }]; return;
  }
  if(q.startsWith('>')){
    const qq=q.slice(1).trim();
    const C=v6Commands().map(c=>[fuzzyScore(qq,c[0]),c]).filter(x=>x[0]>=0).sort((a,b)=>b[0]-a[0]);
    R.innerHTML='<div class="pal-head">Commands</div>'+C.map((x,i)=>'<div class="pal-row" data-i="'+i+'"><span class="pal-ty">cmd</span><span class="pal-main">'+fuzzyMark(qq,x[1][0])+'</span>'+(x[1][2]?'<kbd>'+x[1][2]+'</kbd>':'')+'</div>').join('');
    PAL.items=C.map(x=>x[1][1]); palMark(); return;
  }
  const t0=performance.now();
  let res;
  if(q.trim()){
    res=v6SearchPool().map(p=>{ const a=fuzzyScore(q,p[1]), b=fuzzyScore(q,p[2]), c=String(p[4]||'').toLowerCase().includes(q.toLowerCase())?25:-1; return [Math.max(a,b>=0?b*.7:-1,c),p]; })
      .filter(x=>x[0]>=0).sort((a,b)=>b[0]-a[0]).slice(0,14);
    const cm=v6Commands().map(c=>[fuzzyScore(q,c[0]),c]).filter(x=>x[0]>=60).sort((a,b)=>b[0]-a[0]).slice(0,3);
    const ms=(performance.now()-t0).toFixed(1);
    R.innerHTML='<div class="pal-head">'+res.length+' result'+(res.length===1?'':'s')+' · '+ms+' ms</div>'+
      res.map((x,i)=>'<div class="pal-row" data-i="'+i+'"><span class="pal-ty">'+x[1][0]+'</span><span class="pal-main">'+fuzzyMark(q,x[1][1])+'<small>'+esc(x[1][2])+'</small></span></div>').join('')+
      (cm.length?'<div class="pal-head">Commands</div>'+cm.map((x,i)=>'<div class="pal-row" data-i="'+(res.length+i)+'"><span class="pal-ty">cmd</span><span class="pal-main">'+fuzzyMark(q,x[1][0])+'</span></div>').join(''):'')+
      (!res.length&&!cm.length?'<div class="empty" style="padding:22px">Nothing found — type <b>+ '+esc(q)+'</b> to create it as a task</div>':'');
    PAL.items=res.map(x=>x[1][3]).concat(cm.map(x=>x[1][1]));
    if(typeof vlPaletteSearch==='function') vlPaletteSearch(q.trim());
  } else {
    const recent=(S.tasks||[]).filter(t=>t.status!=='Done').sort((a,b)=>(a.deadline||'9999').localeCompare(b.deadline||'9999')).slice(0,5).map(t=>['task',t.title,projName(t.project_id)||'',()=>editTask(t)]);
    const cmds=[['Start Focus / Zen session',()=>openFocusMode()],['Auto-plan today',()=>{setView('planner');autoPlanDay();}],['New reference',()=>editRef()]];
    R.innerHTML='<div class="pal-head">Next up</div>'+recent.map((x,i)=>'<div class="pal-row" data-i="'+i+'"><span class="pal-ty">task</span><span class="pal-main">'+esc(x[1])+'<small>'+esc(x[2])+'</small></span></div>').join('')+
      '<div class="pal-head">Quick actions</div>'+cmds.map((c,i)=>'<div class="pal-row" data-i="'+(recent.length+i)+'"><span class="pal-ty">cmd</span><span class="pal-main">'+c[0]+'</span></div>').join('')+
      '<div class="pal-head">Tips</div><div class="pal-tip"><b>+ Read paper friday 2h #reading !high</b> creates a scheduled task · <b>&gt;</b> lists every command · <b>g</b> then a letter jumps to a view (<b>?</b> for all shortcuts)</div>';
    PAL.items=recent.map(x=>x[3]).concat(cmds.map(c=>c[1]));
  }
  palMark();
}
function palMark(scroll){
  document.querySelectorAll('#palres .pal-row').forEach(e=>{ const on=+e.dataset.i===PAL.sel; e.classList.toggle('sel',on); if(on&&scroll) e.scrollIntoView({block:'nearest'});
    e.onmouseenter=()=>{PAL.sel=+e.dataset.i;palMark();}; e.onclick=()=>{ const f=PAL.items[+e.dataset.i]; closePalette(); if(f) setTimeout(f,0); }; });
}
openCommandCenter=function(){ openPalette(''); };
function v6Shortcuts(){
  const ov=document.getElementById('ovl'),m=document.getElementById('modal');
  const rows=[['⌘K / Ctrl K','Universal search & commands'],['/','Filter current view'],['n','New item in current view'],['t','Switch theme'],['1 – 8','v5 views'],
    ['g d','Dashboard'],['g t','Tasks'],['g p','Day Planner'],['g y','Timeline'],['g m','Meetings'],['g n','Notes'],['g v','VanoLib'],['c','Quick capture to Inbox'],['?','This help']];
  m.innerHTML='<div style="display:flex;justify-content:space-between;align-items:center"><h3>Keyboard shortcuts</h3><button class="btn ghost" onclick="closeRawModal()">Esc</button></div><div class="v6-keys">'+rows.map(r=>'<div><kbd>'+r[0]+'</kbd><span>'+r[1]+'</span></div>').join('')+'</div>';
  ov.classList.add('show'); ov.onclick=e=>{ if(e.target===ov) closeRawModal(); };
}
let V6_G=0;
window.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&!e.shiftKey&&!e.altKey&&e.key.toLowerCase()==='k'){ e.preventDefault(); e.stopImmediatePropagation(); const d=document.getElementById('palovl'); if(d&&d.classList.contains('show')) closePalette(); else openPalette(''); return; }
  const ae=document.activeElement, typing=ae&&(['INPUT','TEXTAREA','SELECT'].includes(ae.tagName)||ae.isContentEditable);
  if(typing||e.metaKey||e.ctrlKey||e.altKey) return;
  if(document.getElementById('ovl').classList.contains('show')) return;
  if(V6_G&&Date.now()-V6_G<1200){
    const m={d:'dash',t:'board',p:'planner',y:'timeline',m:'agenda',n:'notes',j:'projects',v:'vanolib'}[e.key];
    V6_G=0; if(m){ e.preventDefault(); e.stopImmediatePropagation(); setView(m); } return;
  }
  if(e.key==='g'){ V6_G=Date.now(); e.preventDefault(); e.stopImmediatePropagation(); return; }
  if(e.key==='?'){ e.preventDefault(); e.stopImmediatePropagation(); v6Shortcuts(); return; }
  if(e.key==='c'){ e.preventDefault(); e.stopImmediatePropagation(); openPalette('+ '); return; }
},true);

function rInbox(el){
  const I=(S.inbox||[]).filter(x=>match(x.title+' '+x.source)).sort((a,b)=>(b.created_at||'').localeCompare(a.created_at||''));
  el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">Inbox</h2><div class="demo-note">Capture everything in one line, triage later. Also: <kbd>c</kbd> anywhere, or ⌘K then ⇧↵.</div></div><span class="chip" style="background:var(--accbg);color:var(--acc2)">${I.length} to triage</span></div>
  <div class="panel" style="margin-bottom:14px"><input id="inboxIn" style="width:100%;font-size:14px;padding:12px 14px" placeholder="Capture… e.g. Read paper friday 2h #reading !high @shifted   (Enter → Inbox · ⌘Enter → task directly)"><div id="inboxPrev" style="margin-top:10px;min-height:24px"></div></div>
  <div class="panel">${I.length?I.map(x=>{ const o=parseCapture(x.title); const hasId=/(10\.\d{4,9}\/\S+|\d{4}\.\d{4,5})/.test(x.title);
    return `<div class="v6-irow"><div style="flex:1;min-width:0"><div style="font-weight:600;font-size:13px">${esc(x.title)}</div><div class="demo-note">${esc(x.source||'Capture')} · ${fmtd((x.created_at||'').slice(0,10))}</div><div style="margin-top:5px">${captureChips(o)}</div></div>
    <div class="v6-acts"><button class="btn ghost" onclick="inboxTo(${x.id},'task')">→ Task</button><button class="btn ghost" onclick="inboxTo(${x.id},'note')">→ Note</button><button class="btn ghost" onclick="inboxTo(${x.id},'ref')">${hasId?'→ Ref (lookup)':'→ Reference'}</button><button class="btn ghost" onclick="inboxTo(${x.id},'result')">→ Result</button><button class="btn ghost" title="Delete" onclick="inboxTo(${x.id},'drop')">✕</button></div></div>`;}).join('')
    :'<div class="empty">Inbox zero ✦ — nothing waiting.</div>'}</div>`;
  const inp=document.getElementById('inboxIn'), prev=document.getElementById('inboxPrev');
  inp.oninput=()=>{ prev.innerHTML=inp.value.trim()?captureChips(parseCapture(inp.value)):''; };
  inp.onkeydown=async e=>{ if(e.key!=='Enter'||!inp.value.trim()) return; const v=inp.value.trim(); inp.value=''; prev.innerHTML='';
    if(e.metaKey||e.ctrlKey){ await mutate('POST','tasks',null,captureToTask(parseCapture(v))); toast('Task created'); }
    else await mutate('POST','inbox',null,{title:v,source:'Capture'});
    await load(); const n=document.getElementById('inboxIn'); if(n) n.focus(); };
  inp.focus();
}
async function inboxTo(id,what){
  const x=byId('inbox',id); if(!x) return;
  if(what==='task'){ await mutate('POST','tasks',null,captureToTask(parseCapture(x.title))); await mutate('DELETE','inbox',id); toast('Task created'); }
  else if(what==='note'){ await mutate('POST','notes',null,{title:x.title,content:x.note||'',category:NOTECATDEFAULT,note_date:iso(new Date())}); await mutate('DELETE','inbox',id); toast('Note created'); }
  else if(what==='result'){ editResult({title:x.title},()=>mutate('DELETE','inbox',id)); return; }
  else if(what==='ref'){ const m=x.title.match(/(10\.\d{4,9}\/[^\s]+|\d{4}\.\d{4,5}(v\d+)?)/);
    if(m){ const r=await refLookup(m[1]); if(r){ editRef(r,()=>mutate('DELETE','inbox',id)); return; } }
    editRef({title:x.title},()=>mutate('DELETE','inbox',id)); return; }
  else if(what==='drop'){ await mutate('DELETE','inbox',id); toast('Removed'); }
  await load();
}

let PLANDATE=iso(new Date());
const PLAN_START=7*60, PLAN_END=22*60, PLAN_PX=1.1;
function planBlocks(day){
  const B=[];
  agendaItems().filter(x=>x.kind==='meeting'&&x.date===day).forEach(m=>{ const s=hm2min(m.time); if(s==null) return; const e=hm2min(m.end)||s+60; B.push({kind:'meeting',start:s,end:Math.max(e,s+15),title:m.title,src:m.src}); });
  (S.daily_tasks||[]).filter(d=>d.task_date===day&&d.task_time).forEach(d=>{ const s=hm2min(d.task_time); const e=hm2min(d.task_time_end)||s+30; B.push({kind:'daily',start:s,end:Math.max(e,s+15),title:d.title,src:d,done:d.done}); });
  (S.tasks||[]).filter(t=>t.planned_date===day&&t.planned_time).forEach(t=>{ const s=hm2min(t.planned_time); B.push({kind:'task',start:s,end:s+Math.max(15,+t.estimate_min||60),title:t.title,src:t,done:t.status==='Done'}); });
  return B.sort((a,b)=>a.start-b.start);
}
function rPlanner(el){
  const day=PLANDATE, B=planBlocks(day);
  const pool=(S.tasks||[]).filter(t=>t.status!=='Done'&&!(t.planned_date===day&&t.planned_time)).filter(t=>match(t.title+' '+(t.tags||'')))
    .sort((a,b)=>{ const pr={High:0,Normal:1,Low:2}; const ta=(a.planned_date===day?0:1), tb=(b.planned_date===day?0:1); return ta-tb||pr[a.priority]-pr[b.priority]||(a.deadline||'9999').localeCompare(b.deadline||'9999'); });
  const busyMin=B.filter(b=>b.kind!=='daily').reduce((s,b)=>s+(b.end-b.start),0), cap=8*60;
  const hours=[]; for(let h=PLAN_START/60;h<=PLAN_END/60;h++) hours.push(h);
  const H=(PLAN_END-PLAN_START)*PLAN_PX;
  const now=new Date(), nowMin=now.getHours()*60+now.getMinutes();
  const lanes=layoutLanes(B);
  el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">Day Planner</h2><div class="demo-note">Drag tasks into the day. Meetings (Google / iCloud) and timed routines are placed automatically. Click a block to edit, drag it to move.</div></div>
   <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="btn ghost" onclick="PLANDATE=addDays(PLANDATE,-1);render()">‹</button><input type="date" value="${day}" onchange="PLANDATE=this.value;render()" style="width:150px"><button class="btn ghost" onclick="PLANDATE=iso(new Date());render()">Today</button><button class="btn ghost" onclick="PLANDATE=addDays(PLANDATE,1);render()">›</button><button class="btn pri" onclick="autoPlanDay()">⚡ Auto-plan</button></div></div>
  <div class="v6-plan">
   <div class="panel v6-pool"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><b style="font-size:13px">To schedule</b><span class="demo-note">${pool.length}</span></div>
    <div class="demo-note" style="margin-bottom:6px">Capacity ${fmtDur(busyMin)} / 8h</div><div class="planner-bar" style="margin-bottom:12px"><i style="width:${Math.min(100,busyMin/cap*100)}%;${busyMin>cap?'background:var(--red)':''}"></i></div>
    <div id="planPool">${pool.slice(0,40).map(t=>`<div class="v6-chipcard" draggable="true" data-task="${t.id}" ondblclick="editTask(byId('tasks',${t.id}))"><div style="font-size:12.5px;font-weight:600">${esc(t.title)}</div><div class="demo-note">${t.estimate_min?'~'+fmtDur(t.estimate_min):'no estimate · 1h'}${t.deadline?' · due '+fmtd(t.deadline):''}${t.planned_date===day?' · planned today':''}</div><span class="v6-pri" style="background:${PRICOL[t.priority]}"></span></div>`).join('')||'<div class="empty" style="padding:18px">All set ✦</div>'}</div>
    <div class="v6-unsched" id="planUnsched">Drop here to unschedule</div></div>
   <div class="panel" style="padding:12px 12px 12px 0;overflow:hidden"><div style="padding-left:14px;margin-bottom:8px;font-weight:650;font-size:13px">${new Date(day+'T00:00:00').toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric'})}</div>
    <div class="v6-grid" style="height:${H}px"><div class="v6-hours">${hours.map(h=>`<div style="top:${(h*60-PLAN_START)*PLAN_PX}px">${pad2(h)}:00</div>`).join('')}</div>
     <div class="v6-lane" id="planLane">${hours.map(h=>`<div class="v6-hline" style="top:${(h*60-PLAN_START)*PLAN_PX}px"></div>`).join('')}
      ${day===iso(now)&&nowMin>PLAN_START&&nowMin<PLAN_END?`<div class="v6-now" style="top:${(nowMin-PLAN_START)*PLAN_PX}px"></div>`:''}
      ${B.map((b,i)=>{ const L=lanes[i]; const top=(Math.max(b.start,PLAN_START)-PLAN_START)*PLAN_PX, h=Math.max(18,(Math.min(b.end,PLAN_END)-Math.max(b.start,PLAN_START))*PLAN_PX-2);
        const st=b.kind==='meeting'?'background:var(--tealbg);color:var(--teal);border-color:color-mix(in srgb,var(--teal) 45%,transparent)':b.kind==='daily'?'background:var(--amberbg);color:var(--amber);border-color:color-mix(in srgb,var(--amber) 40%,transparent)':'background:var(--accbg);color:var(--txt);border-color:color-mix(in srgb,var(--acc) 50%,transparent)';
        const click=b.kind==='meeting'?`editMeeting(byId('meetings',${b.src.id}))`:b.kind==='daily'?`editDaily(byId('daily_tasks',${b.src.id}))`:`editTask(byId('tasks',${b.src.id}))`;
        return `<div class="v6-blk${b.done?' done':''}" ${b.kind==='task'?`draggable="true" data-task="${b.src.id}"`:''} style="top:${top}px;height:${h}px;left:calc(${L.col/L.cols*100}% + 6px);width:calc(${100/L.cols}% - 10px);${st}" onclick="${click}"><b>${min2hm(b.start)}–${min2hm(b.end)}</b> ${esc(b.title)}${b.kind==='task'?`<span class="v6-blk-acts"><button title="Focus" onclick="event.stopPropagation();focusOnTask(${b.src.id})">◎</button><button title="Unschedule" onclick="event.stopPropagation();unschedule(${b.src.id})">×</button></span>`:''}</div>`; }).join('')}
     </div></div></div>
  </div>`;
  plannerDnD();
}
function layoutLanes(B){
  const out=B.map(()=>({col:0,cols:1})); let group=[], gEnd=-1;
  const flush=()=>{ const cols=[]; group.forEach(i=>{ let c=0; while(cols[c]!=null&&cols[c]>B[i].start) c++; cols[c]=B[i].end; out[i].col=c; }); group.forEach(i=>out[i].cols=Math.max(1,cols.length)); group=[]; };
  B.forEach((b,i)=>{ if(b.start>=gEnd&&group.length) flush(); group.push(i); gEnd=Math.max(gEnd,b.end); });
  if(group.length) flush(); return out;
}
function plannerDnD(){
  document.querySelectorAll('[data-task][draggable]').forEach(d=>{ d.ondragstart=e=>{ e.dataTransfer.setData('text/plain',d.dataset.task); const r=d.getBoundingClientRect(); e.dataTransfer.setData('offset',String(d.classList.contains('v6-blk')?e.clientY-r.top:0)); d.classList.add('dragging'); }; d.ondragend=()=>d.classList.remove('dragging'); });
  const lane=document.getElementById('planLane'), un=document.getElementById('planUnsched'); if(!lane) return;
  lane.ondragover=e=>{ e.preventDefault(); lane.classList.add('drop'); }; lane.ondragleave=()=>lane.classList.remove('drop');
  lane.ondrop=async e=>{ e.preventDefault(); lane.classList.remove('drop'); const id=+e.dataTransfer.getData('text/plain'); const off=+(e.dataTransfer.getData('offset')||0);
    const r=lane.getBoundingClientRect(); let m=PLAN_START+(e.clientY-r.top-off)/PLAN_PX; m=Math.round(m/15)*15; m=Math.max(PLAN_START,Math.min(PLAN_END-15,m));
    const t=byId('tasks',id); if(!t) return; const body={planned_date:PLANDATE,planned_time:min2hm(m)}; if(!+t.estimate_min) body.estimate_min=60;
    await v6put('tasks',id,body); render(); load(); };
  un.ondragover=e=>{ e.preventDefault(); un.classList.add('drop'); }; un.ondragleave=()=>un.classList.remove('drop');
  un.ondrop=async e=>{ e.preventDefault(); un.classList.remove('drop'); unschedule(+e.dataTransfer.getData('text/plain')); };
}
async function unschedule(id){ await v6put('tasks',id,{planned_time:''}); render(); load(); }
function focusOnTask(id){ openFocusMode(); const s=document.getElementById('focusTaskSel'); if(s) s.value=String(id); const n=document.getElementById('focusTaskName'); const t=byId('tasks',id); if(n&&t) n.textContent=t.title; }
async function autoPlanDay(){
  const day=PLANDATE, B=planBlocks(day), busy=B.filter(b=>b.kind!=='daily').map(b=>[b.start,b.end]);
  const free=(s,e)=>busy.every(([a,b])=>e<=a||s>=b);
  const nowMin=day===iso(new Date())?Math.ceil((new Date().getHours()*60+new Date().getMinutes())/15)*15:0;
  const startDay=Math.max(9*60,nowMin), endDay=19*60;
  const pr={High:0,Normal:1,Low:2}, en={High:0,Medium:1,Low:2};
  const cand=(S.tasks||[]).filter(t=>t.status!=='Done'&&!(t.planned_date===day&&t.planned_time)&&(t.planned_date===day||!t.planned_date||t.planned_date<day||(t.deadline&&t.deadline<=addDays(day,3))))
    .sort((a,b)=>(a.planned_date===day?0:1)-(b.planned_date===day?0:1)||pr[a.priority]-pr[b.priority]||(a.deadline||'9999').localeCompare(b.deadline||'9999')||en[a.energy||'Medium']-en[b.energy||'Medium']);
  let used=busy.reduce((s,[a,b])=>s+(b-a),0), placed=0;
  for(const t of cand){
    const dur=Math.max(15,+t.estimate_min||60); if(used+dur>8*60) break;
    for(let s=startDay;s+dur<=endDay;s+=15){ if(free(s,s+dur+10)){ busy.push([s,s+dur+10]); used+=dur; placed++; await v6put('tasks',t.id,{planned_date:day,planned_time:min2hm(s),estimate_min:dur}); break; } }
  }
  toast(placed?`Planned ${placed} task${placed>1?'s':''} around your meetings`:'Nothing to place — the day is full or empty'); await load();
}

let TLDAYS=+(localStorage.getItem('m2_tldays')||120);
function rTimeline(el){
  const today=iso(new Date()), start=addDays(today,-30), end=addDays(today,TLDAYS-30), span=TLDAYS;
  const x=ds=>Math.max(0,Math.min(100,(new Date(ds+'T00:00:00')-new Date(start+'T00:00:00'))/864e5/span*100));
  const P=(S.projects||[]).filter(p=>match(p.title+' '+p.domain)).filter(p=>p.status!=='Done'||(p.deadline&&p.deadline>=start));
  const months=[]; { const d=new Date(start+'T00:00:00'); d.setDate(1); d.setMonth(d.getMonth()+1); while(iso(d)<end){ months.push(iso(d)); d.setMonth(d.getMonth()+1); } }
  const row=(p,i)=>{
    const T=(S.tasks||[]).filter(t=>t.project_id==p.id), done=T.filter(t=>t.status==='Done').length, pct=T.length?Math.round(done/T.length*100):0;
    const s=(p.created_at||today).slice(0,10), e=p.deadline||addDays(today,30);
    const col=projColor(p.id), l=x(s<start?start:s), w=Math.max(1.5,x(e)-l);
    const ms=(S.milestones||[]).filter(m=>m.project_id==p.id&&m.milestone_date&&m.milestone_date>=start&&m.milestone_date<=end);
    const dl=T.filter(t=>t.deadline&&t.status!=='Done'&&t.deadline>=start&&t.deadline<=end);
    return `<div class="v6-tlrow"><div class="v6-tlname" onclick="editProject(byId('projects',${p.id}))"><span class="dot" style="background:${col}"></span><div><div style="font-weight:600;font-size:12.5px">${esc(p.title)}</div><div class="demo-note">${pct}% · ${done}/${T.length} tasks${p.deadline?' · due '+fmtd(p.deadline):''}</div></div></div>
     <div class="v6-tltrack"><div class="v6-tlbar" style="left:${l}%;width:${w}%;border-color:${col};background:color-mix(in srgb,${col} 18%,transparent);${p.deadline?'':'border-style:dashed'}" onclick="editProject(byId('projects',${p.id}))"><i style="width:${pct}%;background:color-mix(in srgb,${col} 45%,transparent)"></i></div>
      ${dl.map(t=>`<span class="v6-tldot" title="${esc(t.title)} · ${fmtd(t.deadline)}" style="left:${x(t.deadline)}%;background:${t.deadline<today?'var(--red)':col}" onclick="editTask(byId('tasks',${t.id}))"></span>`).join('')}
      ${ms.map(m=>`<span class="v6-tlms${m.done?' done':''}" title="◆ ${esc(m.title)} · ${fmtd(m.milestone_date)}" style="left:${x(m.milestone_date)}%;background:${col}" onclick="editMilestone(byId('milestones',${m.id}))"></span><span class="v6-tlmslab" style="left:${x(m.milestone_date)}%">${esc(m.title)}</span>`).join('')}</div></div>`; };
  const loose=(S.milestones||[]).filter(m=>!m.project_id&&m.milestone_date&&m.milestone_date>=start&&m.milestone_date<=end);
  el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">Timeline</h2><div class="demo-note">Projects as bars (start → deadline, filled by % done) · ◆ milestones · ● task deadlines. Click anything to edit.</div></div>
   <div style="display:flex;gap:6px">${[90,120,180,365].map(d=>`<button class="btn ghost${TLDAYS===d?' on':''}" onclick="TLDAYS=${d};localStorage.setItem('m2_tldays',${d});render()">${d===365?'1 year':d+' days'}</button>`).join('')}<button class="btn pri" onclick="editMilestone(null,iso(new Date()))">+ Milestone</button></div></div>
   <div class="panel v6-tl"><div class="v6-tlrow v6-tlhead"><div class="v6-tlname"></div><div class="v6-tltrack">${months.filter(m=>Math.abs(x(m)-x(today))>4).map(m=>`<span class="v6-tlmonth" style="left:${x(m)}%">${new Date(m+'T00:00:00').toLocaleDateString('en-US',{month:'short'})}</span>`).join('')}<span class="v6-tltoday" style="left:${x(today)}%"><b>Today</b></span></div></div>
   ${P.length?P.map(row).join(''):'<div class="empty">No active projects — create one in Projects.</div>'}
   ${loose.length?`<div class="v6-tlrow"><div class="v6-tlname"><span class="dot" style="background:var(--dim)"></span><b style="font-size:12.5px">Other milestones</b></div><div class="v6-tltrack">${loose.map(m=>`<span class="v6-tlms" style="left:${x(m.milestone_date)}%;background:var(--amber)" title="${esc(m.title)}" onclick="editMilestone(byId('milestones',${m.id}))"></span><span class="v6-tlmslab" style="left:${x(m.milestone_date)}%">${esc(m.title)}</span>`).join('')}</div></div>`:''}
   <div class="v6-tlgrid">${months.map(m=>`<i style="left:${x(m)}%"></i>`).join('')}<i class="today" style="left:${x(today)}%"></i></div></div>`;
}

let RADARFILTER=localStorage.getItem('m2_radarf')||'new', RADARMIN=+(localStorage.getItem('m2_radarmin')||0), RADAR_BUSY=false, RADAR_OPEN={};
async function radarFetch(){
  if(RADAR_BUSY) return; RADAR_BUSY=true; render(); toast('Querying arXiv…');
  let r; try{ r=await api('POST','/api/radar/fetch',{}); }catch(e){ r={ok:false,error:'VanoAtlas server unreachable'}; }
  RADAR_BUSY=false;
  if(r.ok) toast(r.new?`${r.new} new paper${r.new>1?'s':''}${r.hot?' · '+r.hot+' highly relevant':''}`:'No new papers since last check'); else toast(r.error||'Radar failed');
  NOTIF_SETTINGS=null; await load();
}
async function radarSettings(){
  let st={}; try{ st=await api('GET','/api/settings'); }catch(e){}
  modal('Radar settings',[
    {k:'radar_keywords',label:'Keywords / phrases (comma-separated)',type:'textarea',rows:3},
    {k:'radar_categories',label:'arXiv categories (e.g. math.SG, math.AG)'},
    {k:'radar_authors',label:'Followed authors (comma-separated, e.g. Safronov, Calaque)'},
    {k:'radar_auto',label:'Automatic check every 12 h (macOS notification if score ≥ 90)',type:'select',options:[['1','On'],['0','Off']]},
  ],st,async o=>{ await api('POST','/api/settings',o); NOTIF_SETTINGS=null; toast('Radar settings saved'); radarFetch(); });
}
function rRadar(el){
  const all=(S.radar_items||[]);
  const L=all.filter(r=>RADARFILTER==='all'?r.state!=='dismissed':r.state===RADARFILTER).filter(r=>(+r.score||0)>=RADARMIN).filter(r=>match(r.title+' '+r.authors+' '+r.abstract+' '+r.cats))
    .sort((a,b)=>(+b.score-+a.score)||(b.published||'').localeCompare(a.published||''));
  const cnt=k=>all.filter(r=>k==='all'?r.state!=='dismissed':r.state===k).length;
  el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">arXiv Radar</h2><div class="demo-note">New preprints ranked for <i>you</i>: keywords in title/abstract, followed authors, categories, freshness. Runs on your Mac via the public arXiv API.</div></div>
   <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn ghost" onclick="radarSettings()">⚙ Keywords & authors</button><button class="btn pri" onclick="radarFetch()" ${RADAR_BUSY?'disabled':''}>${RADAR_BUSY?'Fetching…':'↻ Fetch now'}</button></div></div>
   <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">${[['new','New'],['saved','Saved'],['all','All'],['dismissed','Dismissed']].map(([k,l])=>`<button class="btn ghost${RADARFILTER===k?' on':''}" style="padding:5px 12px;font-size:12px" onclick="RADARFILTER='${k}';localStorage.setItem('m2_radarf','${k}');render()">${l} <span class="demo-note">${cnt(k)}</span></button>`).join('')}
    <span class="sp" style="flex:1"></span><span class="demo-note">Min score ${RADARMIN}</span><input type="range" min="0" max="90" step="10" value="${RADARMIN}" style="width:140px" onchange="RADARMIN=+this.value;localStorage.setItem('m2_radarmin',this.value);render()">
    ${RADARFILTER==='new'&&L.length?'<button class="btn ghost" style="padding:5px 12px;font-size:12px" onclick="radarDismissShown()">Dismiss shown</button>':''}</div>
   <div class="panel">${L.length?L.slice(0,120).map(r=>{ const sc=+r.score||0; const col=sc>=80?'var(--green)':sc>=50?'var(--acc2)':'var(--dim)'; const inRefs=(S.refs||[]).some(x=>x.arxiv_id&&x.arxiv_id===r.arxiv_id);
    return `<div class="v6-paper"><div class="v6-score" style="color:${col};border-color:${col}">${sc}</div><div style="flex:1;min-width:0">
      <a class="v6-ptitle" href="https://arxiv.org/abs/${esc(r.arxiv_id)}" target="_blank" rel="noopener">${esc(r.title)}</a>
      <div class="demo-note" style="margin-top:2px">${esc(r.authors)}</div>
      <div style="margin-top:5px">${String(r.cats||'').split(',').filter(Boolean).slice(0,3).map(c=>`<span class="chip" style="background:var(--panel3);color:var(--mut)">${esc(c.trim())}</span>`).join('')}<span class="chip" style="background:var(--panel3);color:var(--mut)">arXiv:${esc(r.arxiv_id)}</span><span class="chip" style="background:var(--panel3);color:var(--mut)">${fmtdy(r.published)}</span>${inRefs?'<span class="chip" style="background:var(--greenbg);color:var(--green)">in bibliography</span>':''}</div>
      <div class="demo-note" style="margin-top:5px;color:var(--acc2)">Why: ${esc(r.why)}</div>
      <div class="v6-abs${RADAR_OPEN[r.id]?' open':''}" onclick="RADAR_OPEN[${r.id}]=!RADAR_OPEN[${r.id}];render()">${esc(r.abstract)}</div></div>
      <div class="v6-acts col"><button class="btn ghost" onclick="vlOpenByArxiv('${esc(r.arxiv_id)}','https://arxiv.org/abs/${esc(r.arxiv_id)}')">📖 Read</button><button class="btn ghost" onclick="radarToRef(${r.id},false)">${inRefs?'✓ Saved':'+ Bibliography'}</button><button class="btn ghost" onclick="radarToRef(${r.id},true)">→ To read</button>${r.state!=='dismissed'?`<button class="btn ghost" onclick="radarState(${r.id},'dismissed')">✕ Dismiss</button>`:`<button class="btn ghost" onclick="radarState(${r.id},'new')">↺ Restore</button>`}</div></div>`; }).join('')
    :`<div class="empty">${all.length?'Nothing in this filter.':'No papers yet. Set your keywords / authors, then “Fetch now”. VanoAtlas also checks automatically every 12 hours.'}</div>`}</div>`;
}
async function radarState(id,state){ await v6put('radar_items',id,{state}); render(); load(); }
async function radarDismissShown(){ const L=(S.radar_items||[]).filter(r=>r.state==='new'&&(+r.score||0)>=RADARMIN); for(const r of L) await mutate('PUT','radar_items',r.id,{state:'dismissed'}); toast(L.length+' dismissed'); load(); }
function radarRefObj(r){ return {title:r.title,authors:r.authors,year:(r.published||'').slice(0,4),venue:r.journal_ref||'',doi:r.doi||'',arxiv_id:r.arxiv_id,url:'https://arxiv.org/abs/'+r.arxiv_id,abstract:r.abstract,status:'To read',bibkey:makeBibKey(r.authors,(r.published||'').slice(0,4))}; }
async function radarToRef(id,alsoTask){
  const r=byId('radar_items',id); if(!r) return;
  if(!(S.refs||[]).some(x=>x.arxiv_id===r.arxiv_id)) await mutate('POST','refs',null,radarRefObj(r));
  if(alsoTask) await mutate('POST','tasks',null,{title:'Read: '+r.title,task_type:'Reading',status:'To Do',priority:(+r.score>=80?'High':'Normal'),estimate_min:60,tags:'arxiv',description:'https://arxiv.org/abs/'+r.arxiv_id});
  await mutate('PUT','radar_items',id,{state:'saved'});
  toast(alsoTask?'Saved + reading task created':'Saved to bibliography'); load();
}

const REF_STATUSES=['To read','Reading','Read','Cited','Skimmed'];
let REFFILTER='All', REF_OPEN={};
function makeBibKey(authors,year){
  const names=String(authors||'').split(/,| and /).map(s=>s.trim()).filter(Boolean);
  const last=n=>{ const p=n.split(/\s+/); return (p[p.length-1]||'').normalize('NFD').replace(/[^A-Za-z]/g,''); };
  let base=names.length>=3?names.slice(0,5).map(n=>last(n)[0]||'').join(''):names.map(last).join('');
  base=base||'Ref'; const key0=base+String(year||'').slice(-2);
  const keys=new Set((S.refs||[]).map(r=>r.bibkey)); if(!keys.has(key0)) return key0;
  for(const c of 'abcdefghijklmnopqrstuvwxyz') if(!keys.has(key0+c)) return key0+c; return key0+Date.now();
}
function refUsage(r){
  const k=r.bibkey; if(!k) return 0; const re=new RegExp('(@'+k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b|\\\\cite[pt]?\\*?(\\[[^\\]]*\\])?\\{[^}]*\\b'+k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b)');
  return (S.notes||[]).filter(n=>re.test(n.content||'')).length+(S.results||[]).filter(x=>String(x.ref_key||'').split(',').map(s=>s.trim()).includes(k)||re.test(x.statement||'')).length;
}
async function refLookup(ident){
  toast('Looking up '+ident+'…');
  let r; try{ r=await api('POST','/api/refs/lookup',{id:ident}); }catch(e){ r={ok:false,error:'server unreachable'}; }
  if(!r.ok){ toast(r.error||'Lookup failed'); return null; }
  r.ref.status='To read'; return r.ref;
}
async function addRefById(){
  const el=document.getElementById('refId'); const v=(el&&el.value||'').trim(); if(!v){ editRef(); return; }
  const ref=await refLookup(v); if(ref) editRef(ref);
}
function editRef(r,after){
  r=r||{}; const isNew=!r.id;
  modal(isNew?'New reference':'Edit reference',[
    {k:'title',label:'Title'},{k:'authors',label:'Authors (comma-separated)'},
    {k:'bibkey',label:'BibTeX key',half:true},{k:'year',label:'Year',half:true},
    {k:'venue',label:'Journal / venue',half:true},{k:'status',label:'Status',type:'select',options:REF_STATUSES,def:'To read',half:true},
    {k:'arxiv_id',label:'arXiv id',half:true},{k:'doi',label:'DOI',half:true},
    {k:'url',label:'URL'},{k:'project_id',label:'Project',type:'select',options:projOptions()},{k:'tags',label:'Tags (comma-separated)'},
    {k:'notes',label:'Reading notes',type:'textarea',rows:4},{k:'abstract',label:'Abstract',type:'textarea',rows:3},
    {k:'bibtex',label:'Custom BibTeX (leave empty to auto-generate)',type:'textarea',rows:3},
  ],r,async o=>{ if(!o.bibkey) o.bibkey=makeBibKey(o.authors,o.year); if(!o.project_id) o.project_id=null;
    await mutate(isNew?'POST':'PUT','refs',r.id,o); if(after) await after(); await load(); toast(isNew?'Reference added':'Reference updated'); },
  r.id?()=>del('refs',r.id).then(()=>toast('Reference deleted')):null);
}
function refBibtex(r){
  if(r.bibtex) return r.bibtex;
  const au=String(r.authors||'').split(',').map(s=>s.trim()).filter(Boolean).join(' and ');
  const f=['  author = {'+au+'}','  title = {{'+(r.title||'')+'}}']; if(r.year) f.push('  year = {'+r.year+'}'); if(r.venue) f.push('  journal = {'+r.venue+'}');
  if(r.doi) f.push('  doi = {'+r.doi+'}'); if(r.arxiv_id) f.push('  eprint = {'+r.arxiv_id+'}','  archivePrefix = {arXiv}'); if(r.url) f.push('  url = {'+r.url+'}');
  return '@'+(r.venue?'article':'misc')+'{'+(r.bibkey||'ref')+',\n'+f.join(',\n')+'\n}';
}
function copyText(t,msg){ (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>toast(msg||'Copied')).catch(()=>{ const ta=document.createElement('textarea'); ta.value=t; document.body.appendChild(ta); ta.select(); try{document.execCommand('copy');toast(msg||'Copied');}catch(e){} ta.remove(); }); }
function importBib(){
  const ov=document.getElementById('ovl'),m=document.getElementById('modal');
  m.innerHTML='<h3>Import BibTeX</h3><div class="field"><label>Paste .bib content or choose a file</label><input type="file" id="bibFile" accept=".bib,.txt"><textarea id="bibText" rows="10" style="margin-top:8px;font-family:ui-monospace,monospace;font-size:12px" placeholder="@article{PTVV13, ...}"></textarea></div><div class="acts"><span class="sp"></span><button class="btn ghost" onclick="closeRawModal()">Cancel</button><button class="btn pri" id="bibGo">Import</button></div>';
  ov.classList.add('show'); ov.onclick=e=>{ if(e.target===ov) closeRawModal(); };
  m.querySelector('#bibFile').onchange=e=>{ const f=e.target.files[0]; if(f) f.text().then(t=>m.querySelector('#bibText').value=t); };
  m.querySelector('#bibGo').onclick=async()=>{ const t=m.querySelector('#bibText').value; if(!t.trim()) return; const r=await api('POST','/api/refs/import',{bibtex:t}); closeRawModal(); toast(r.ok?`Imported ${r.added} · skipped ${r.skipped} duplicate key(s)`:'Import failed'); load(); };
}
function rBiblio(el){
  const R=(S.refs||[]).filter(r=>REFFILTER==='All'||r.status===REFFILTER).filter(r=>match([r.title,r.authors,r.bibkey,r.venue,r.tags,r.year,r.notes].join(' ')))
    .sort((a,b)=>(a.bibkey||'').localeCompare(b.bibkey||''));
  const cnt=s=>(S.refs||[]).filter(r=>s==='All'||r.status===s).length;
  el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">Bibliography</h2><div class="demo-note">Add by DOI or arXiv id, import/export .bib for Overleaf · Cite in notes with <code>@key</code> or <code>\\cite{key}</code>; usage is counted automatically.</div></div>
   <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn ghost" onclick="importBib()">Import .bib</button><a class="btn ghost" href="/api/refs/export.bib" download="references.bib" style="text-decoration:none">Export .bib</a><button class="btn ghost" onclick="copyText((S.refs||[]).map(refBibtex).join('\\n\\n'),'All BibTeX copied')">Copy all</button></div></div>
   <div class="panel" style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap"><input id="refId" style="flex:1;min-width:240px" placeholder="DOI (10.1007/…) or arXiv id (2405.11821) or URL — Enter to fetch metadata" onkeydown="if(event.key==='Enter')addRefById()"><button class="btn pri" onclick="addRefById()">+ Add</button><button class="btn ghost" onclick="editRef()">Manual</button></div>
   <div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap">${['All'].concat(REF_STATUSES).map(s=>`<button class="btn ghost${REFFILTER===s?' on':''}" style="padding:5px 12px;font-size:12px" onclick="REFFILTER='${s}';render()">${s} <span class="demo-note">${cnt(s)}</span></button>`).join('')}</div>
   <div class="panel" style="padding:6px">${R.length?`<table class="v6-table"><thead><tr><th>Key</th><th>Reference</th><th>Year</th><th>Status</th><th title="Notes & results citing it">Used</th><th></th></tr></thead><tbody>${R.map(r=>{ const u=refUsage(r);
     return `<tr><td><button class="v6-key" title="Copy \\cite{${esc(r.bibkey)}}" onclick="copyText('\\\\cite{${esc(r.bibkey)}}','Copied \\\\cite{${esc(r.bibkey)}}')">${esc(r.bibkey||'—')}</button></td>
      <td style="cursor:pointer" onclick="editRef(byId('refs',${r.id}))"><div style="font-weight:600">${esc(r.title)}</div><div class="demo-note">${esc(r.authors)}${r.venue?' · <i>'+esc(r.venue)+'</i>':''}${r.arxiv_id?' · arXiv:'+esc(r.arxiv_id):''}${r.project_id?' · 📁 '+esc(projName(r.project_id)):''}</div>${REF_OPEN[r.id]?`<pre class="v6-bib">${esc(refBibtex(r))}</pre>`:''}</td>
      <td>${esc(r.year||'')}</td><td><select style="padding:4px 8px;font-size:12px" onchange="v6put('refs',${r.id},{status:this.value}).then(load)">${REF_STATUSES.map(s=>`<option${s===r.status?' selected':''}>${s}</option>`).join('')}</select></td>
      <td><span class="chip" style="background:${u?'var(--accbg);color:var(--acc2)':'var(--panel3);color:var(--dim)'}">${u}</span></td>
      <td style="white-space:nowrap">${r.arxiv_id?`<button class="btn ghost" style="padding:4px 9px;font-size:11px" onclick="vlOpenByArxiv('${esc(r.arxiv_id)}','https://arxiv.org/abs/${esc(r.arxiv_id)}')">📖 Read</button> `:''}<button class="btn ghost" style="padding:4px 9px;font-size:11px" onclick="REF_OPEN[${r.id}]=!REF_OPEN[${r.id}];render()">BibTeX</button> ${r.url||r.arxiv_id||r.doi?`<a class="btn ghost" style="padding:4px 9px;font-size:11px;text-decoration:none" target="_blank" rel="noopener" href="${esc(r.url||(r.arxiv_id?'https://arxiv.org/abs/'+r.arxiv_id:'https://doi.org/'+r.doi))}">Open ↗</a>`:''}</td></tr>`; }).join('')}</tbody></table>`
   :'<div class="empty">No references yet — paste a DOI or arXiv id above, import a .bib, or save papers from the arXiv Radar.</div>'}</div>`;
}

const RES_KINDS=['Definition','Lemma','Proposition','Theorem','Corollary','Conjecture','Example','Remark'];
const RES_STATUS=['Proved','To check','In progress','Conjecture','False'];
const RES_RANK={'Proved':0,'To check':1,'In progress':2,'Conjecture':3,'False':4};
const RES_STYLE={'Proved':'background:var(--greenbg);color:var(--green)','To check':'background:var(--amberbg);color:var(--amber)','In progress':'background:var(--accbg);color:var(--acc2)','Conjecture':'background:var(--redbg);color:var(--pink)','False':'background:var(--redbg);color:var(--red)'};
const RES_COL={'Proved':'var(--green)','To check':'var(--amber)','In progress':'var(--acc2)','Conjecture':'var(--pink)','False':'var(--red)'};
let RESPROJ=0;
function resDeps(r){ return String(r.deps||'').split(',').map(s=>s.trim()).filter(Boolean); }
function resByLabel(l){ return (S.results||[]).find(x=>(x.label||'').toLowerCase()===String(l).toLowerCase()); }
function resEffective(r,seen){
  seen=seen||new Set(); if(seen.has(r.id)) return {status:r.status,weak:[]}; seen.add(r.id);
  let worst=RES_RANK[r.status]??2, weak=[];
  resDeps(r).forEach(l=>{ const d=resByLabel(l); if(!d) return; const e=resEffective(d,new Set(seen)); const rk=RES_RANK[e.status]??2; if(rk>0) weak.push(d.label||d.title); if(rk>worst) worst=rk; });
  return {status:Object.keys(RES_RANK).find(k=>RES_RANK[k]===worst),weak:[...new Set(weak)]};
}
function editResult(r,after){
  r=r||{}; const isNew=!r.id;
  modal(isNew?'New result':'Edit result',[
    {k:'label',label:'Label (e.g. Lemma 3.4)',half:true},{k:'kind',label:'Kind',type:'select',options:RES_KINDS,def:'Lemma',half:true},
    {k:'title',label:'Short name'},
    {k:'status',label:'Status',type:'select',options:RES_STATUS,def:'In progress',half:true},{k:'project_id',label:'Project',type:'select',options:projOptions(),half:true},
    {k:'statement',label:'Statement (LaTeX allowed)',type:'textarea',rows:4},
    {k:'deps',label:'Depends on (labels or BibTeX keys, comma-separated)'},{k:'ref_key',label:'Source reference key(s), if from the literature'},
    {k:'notes',label:'Proof sketch / notes',type:'textarea',rows:4},
  ],r,async o=>{ if(!o.project_id) o.project_id=null; await mutate(isNew?'POST':'PUT','results',r.id,o); if(after) await after(); await load(); toast(isNew?'Result added':'Result updated'); },
  r.id?()=>del('results',r.id).then(()=>toast('Result deleted')):null);
}
function resultsLatex(){
  const env={Definition:'definition',Lemma:'lemma',Proposition:'proposition',Theorem:'theorem',Corollary:'corollary',Conjecture:'conjecture',Example:'example',Remark:'remark'};
  return (S.results||[]).filter(r=>!RESPROJ||r.project_id==RESPROJ).map(r=>{ const e=env[r.kind]||'lemma', lab=(r.label||r.title).toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');
    return '\\begin{'+e+'}['+(r.title||'')+']\\label{'+e.slice(0,3)+':'+lab+'}\n'+(r.statement||'% statement')+'\n\\end{'+e+'}'+(['definition','example','remark','conjecture'].includes(e)?'':'\n\\begin{proof}\n% '+r.status+(resDeps(r).length?' — uses '+resDeps(r).join(', '):'')+'\n\\end{proof}'); }).join('\n\n');
}
function rResults(el){
  const R=(S.results||[]).filter(r=>!RESPROJ||r.project_id==RESPROJ).filter(r=>match([r.label,r.title,r.statement,r.notes,r.deps].join(' ')));
  const stats=RES_STATUS.map(s=>[s,R.filter(r=>r.status===s).length]);
  const ordered=R.slice().sort((a,b)=>String(a.label||'').localeCompare(String(b.label||''),undefined,{numeric:true}));
  el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">Results Bank</h2><div class="demo-note">Every definition, lemma and theorem with its status and dependencies. A result is only as solid as what it relies on — weak links are flagged automatically.</div></div>
   <div style="display:flex;gap:8px;flex-wrap:wrap"><select onchange="RESPROJ=+this.value;render()" style="width:200px"><option value="0">All projects</option>${(S.projects||[]).map(p=>`<option value="${p.id}"${RESPROJ==p.id?' selected':''}>${esc(p.title)}</option>`).join('')}</select><button class="btn ghost" onclick="copyText(resultsLatex(),'LaTeX skeleton copied')">Copy as LaTeX</button><button class="btn pri" onclick="editResult({project_id:RESPROJ||''})">+ Result</button></div></div>
   <div class="kpis">${stats.map(([s,n])=>`<div class="kpi"><b style="color:${RES_COL[s]}">${n}</b><span>${s}</span></div>`).join('')}</div>
   <div class="v6-res">
    <div class="panel">${ordered.length?ordered.map(r=>{ const e=resEffective(r); const weakened=RES_RANK[e.status]>RES_RANK[r.status];
      return `<div class="v6-resrow" onclick="editResult(byId('results',${r.id}))"><div style="flex:1;min-width:0"><div><b>${esc(r.label||r.kind)}</b> <span style="color:var(--mut)">${esc(r.title)}</span></div>
       ${r.statement?`<div class="v6-stmt">${esc(r.statement).slice(0,240)}</div>`:''}
       <div class="demo-note" style="margin-top:4px">${resDeps(r).length?'uses '+resDeps(r).map(l=>{const d=resByLabel(l);return d?`<span style="color:${RES_COL[d.status]}">${esc(l)}</span>`:`<span title="reference">${esc(l)}</span>`;}).join(', '):'no dependencies'}${r.project_id?' · 📁 '+esc(projName(r.project_id)):''}</div>
       ${weakened?`<div class="demo-note" style="color:var(--amber);margin-top:3px">⚠ effectively <b>${e.status}</b> — relies on ${esc(e.weak.join(', '))}</div>`:''}</div>
       <span class="chip" style="${RES_STYLE[r.status]||''}">${esc(r.status)}</span></div>`; }).join('')
     :'<div class="empty">No results yet. Add your definitions, lemmas and theorems — then link them with “Depends on”.</div>'}</div>
    <div class="panel"><div style="font-weight:650;font-size:13px;margin-bottom:8px">Dependency graph</div>${resGraphSvg(R)}</div>
   </div>`;
}
function resGraphSvg(R){
  if(!R.length) return '<div class="empty" style="padding:20px">—</div>';
  const nodes={}; R.forEach(r=>nodes['r'+r.id]={id:'r'+r.id,label:r.label||r.title,r});
  const edges=[];
  R.forEach(r=>resDeps(r).forEach(l=>{ const d=resByLabel(l); let k; if(d&&nodes['r'+d.id]) k='r'+d.id; else { k='x'+l; nodes[k]=nodes[k]||{id:k,label:l,ext:true}; } edges.push([k,'r'+r.id]); }));
  const depth={}; const ids=Object.keys(nodes);
  const dfs=(k,seen)=>{ if(depth[k]!=null) return depth[k]; if(seen.has(k)) return 0; seen.add(k); const ins=edges.filter(e=>e[1]===k).map(e=>e[0]); depth[k]=ins.length?1+Math.max(...ins.map(i=>dfs(i,seen))):0; return depth[k]; };
  ids.forEach(k=>dfs(k,new Set()));
  const cols={}; ids.forEach(k=>(cols[depth[k]]=cols[depth[k]]||[]).push(k));
  const nC=Object.keys(cols).length, W=Math.max(300,nC*135), maxR=Math.max(...Object.values(cols).map(c=>c.length)), H=Math.max(160,maxR*54+30);
  const pos={}; Object.entries(cols).forEach(([d,ks])=>ks.forEach((k,i)=>pos[k]=[60+(+d)*(W-120)/Math.max(1,nC-1||1),30+(i+.5)*(H-40)/ks.length]));
  if(nC===1) ids.forEach(k=>pos[k][0]=W/2);
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-height:520px"><defs><marker id="v6arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--line2)"/></marker></defs>`;
  edges.forEach(([a,b])=>{ const p=pos[a],q=pos[b]; s+=`<path d="M${p[0]+52} ${p[1]} C ${(p[0]+q[0])/2} ${p[1]}, ${(p[0]+q[0])/2} ${q[1]}, ${q[0]-54} ${q[1]}" fill="none" stroke="var(--line2)" stroke-width="1.6" marker-end="url(#v6arr)"/>`; });
  ids.forEach(k=>{ const n=nodes[k],p=pos[k],c=n.ext?'var(--teal)':RES_COL[n.r.status]||'var(--acc)'; const lab=String(n.label); s+=`<g style="cursor:pointer" ${n.r?`onclick="editResult(byId('results',${n.r.id}))"`:''}><rect x="${p[0]-52}" y="${p[1]-15}" width="104" height="30" rx="9" fill="color-mix(in srgb,${c} 16%,transparent)" stroke="${c}"/><text x="${p[0]}" y="${p[1]+4}" text-anchor="middle" font-size="11" fill="var(--txt)">${esc(lab.length>16?lab.slice(0,15)+'…':lab)}</text></g>`; });
  return s+'</svg>';
}

let REVWEEK=null;
function weekStart(ds){ const d=new Date((ds||iso(new Date()))+'T00:00:00'); const k=(d.getDay()+6)%7; d.setDate(d.getDate()-k); return iso(d); }
function rReview(el){
  const ws=REVWEEK||weekStart(), we=addDays(ws,6), inW=s=>s&&s.slice(0,10)>=ws&&s.slice(0,10)<=we;
  const TE=(S.time_entries||[]).filter(e=>inW(e.started_at)&&+e.duration_min>0), focus=TE.reduce((s,e)=>s+(+e.duration_min||0),0);
  const prevWs=addDays(ws,-7), prevWe=addDays(ws,-1), prevFocus=(S.time_entries||[]).filter(e=>e.started_at&&e.started_at.slice(0,10)>=prevWs&&e.started_at.slice(0,10)<=prevWe).reduce((s,e)=>s+(+e.duration_min||0),0);
  const doneT=(S.tasks||[]).filter(t=>t.status==='Done'&&inW(t.completed_at));
  const plannedT=(S.tasks||[]).filter(t=>t.planned_date&&t.planned_date>=ws&&t.planned_date<=we);
  const meet=agendaItems().filter(x=>x.kind==='meeting'&&x.date>=ws&&x.date<=we);
  const notesNew=(S.notes||[]).filter(n=>inW(n.created_at)), refsNew=(S.refs||[]).filter(r=>inW(r.created_at)), resNew=(S.results||[]).filter(r=>inW(r.created_at));
  const overdue=(S.tasks||[]).filter(t=>t.status!=='Done'&&t.deadline&&t.deadline<iso(new Date()));
  const hrs=Array(24).fill(0); (S.time_entries||[]).filter(e=>e.started_at&&+e.duration_min>0).forEach(e=>{ const d=new Date(e.started_at); if(!isNaN(d)) hrs[d.getHours()]+=+e.duration_min; });
  let bestH=-1,bestV=0; for(let h=5;h<=21;h++){ const v=hrs[h]+hrs[h+1]+hrs[h+2]; if(v>bestV){bestV=v;bestH=h;} }
  const totalHrs=hrs.reduce((a,b)=>a+b,0);
  const spent={}; (S.time_entries||[]).forEach(e=>{ spent[e.task_id]=(spent[e.task_id]||0)+(+e.duration_min||0); });
  const byType={}; (S.tasks||[]).filter(t=>t.status==='Done'&&+t.estimate_min>0&&spent[t.id]).forEach(t=>{ const k=t.task_type||'Other'; byType[k]=byType[k]||[0,0]; byType[k][0]+=+t.estimate_min; byType[k][1]+=spent[t.id]; });
  const typeIns=Object.entries(byType).map(([k,[e,a]])=>[k,Math.round((a/e-1)*100)]).filter(x=>Math.abs(x[1])>=15).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,3);
  const since=addDays(iso(new Date()),-28);
  const eta=(S.projects||[]).filter(p=>p.status!=='Done').map(p=>{ const T=(S.tasks||[]).filter(t=>t.project_id==p.id); const left=T.filter(t=>t.status!=='Done').length; const rate=T.filter(t=>t.status==='Done'&&(t.completed_at||'').slice(0,10)>=since).length/4; return {p,left,rate,pct:T.length?Math.round((T.length-left)/T.length*100):0,eta:rate>0?addDays(iso(new Date()),Math.ceil(left/rate*7)):null}; }).filter(x=>x.left);
  const ins=[];
  if(totalHrs>=60&&bestH>=0) ins.push(`✦ Your most focused window is <b>${pad2(bestH)}:00–${pad2(bestH+3)}:00</b> (${Math.round(bestV/totalHrs*100)}% of all tracked focus). Protect it for proofs and writing.`);
  typeIns.forEach(([k,p])=>ins.push(p>0?`⚠ <b>${esc(k)}</b> tasks take <b>${p}% longer</b> than estimated — pad your estimates.`:`✓ <b>${esc(k)}</b> tasks finish <b>${-p}% faster</b> than estimated.`));
  eta.slice(0,4).forEach(x=>ins.push(`📈 <b>${esc(x.p.title)}</b>: ${x.pct}% done, ${x.left} open task${x.left>1?'s':''} — ${x.eta?`at the current pace, finished around <b>${fmtdy(x.eta)}</b>${x.p.deadline?(x.eta>x.p.deadline?` <span style="color:var(--red)">(after the ${fmtd(x.p.deadline)} deadline)</span>`:' ✓ before deadline'):''}`:'no completions in the last 4 weeks'}.`));
  if(overdue.length) ins.push(`⏱ ${overdue.length} overdue task${overdue.length>1?'s':''} — reschedule or drop them.`);
  if(!ins.length) ins.push('Track focus sessions and complete tasks to unlock personal insights.');
  const CK=[['inbox','Empty the Inbox ('+(S.inbox||[]).length+')',()=>"setView('inbox')"],['overdue','Review overdue tasks ('+overdue.length+')',()=>"TASKQUICK='overdue';setView('board')"],['plan','Plan next week’s 3 priorities',()=>"document.getElementById('revPri').focus()"],['meet','Prepare questions for supervisor meeting',()=>"setView('agenda')"]];
  const key='m2_review_'+ws; let st={}; try{ st=JSON.parse(localStorage.getItem(key)||'{}'); }catch(e){}
  const delta=prevFocus?Math.round((focus/prevFocus-1)*100):null;
  el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">Weekly Review</h2><div class="demo-note">Week of ${fmtdy(ws)} → ${fmtdy(we)} · a guided 15-minute reset.</div></div>
   <div style="display:flex;gap:8px"><button class="btn ghost" onclick="REVWEEK=addDays('${ws}',-7);render()">‹ Previous</button><button class="btn ghost" onclick="REVWEEK=null;render()">This week</button><button class="btn ghost" onclick="REVWEEK=addDays('${ws}',7);render()">Next ›</button></div></div>
   <div class="kpis">${kpi(fmtHours(focus),'Focus tracked'+(delta!=null?` · ${delta>=0?'+':''}${delta}% vs last week`:''),'var(--acc2)')}${kpi(doneT.length+' / '+(plannedT.length||'–'),'Tasks done / planned','var(--green)')}${kpi(meet.length,'Meetings','var(--teal)')}${kpi(notesNew.length+refsNew.length+resNew.length,'New notes · refs · results','var(--amber)')}${typeof VL_SUM!=='undefined'&&VL_SUM&&VL_SUM.available?kpi(Object.entries(VL_SUM.log||{}).filter(([d])=>d>=ws&&d<=we).reduce((a,[,v])=>a+v,0),'Pages read in VanoLib','var(--pink)'):''}</div>
   <div class="v6-rev">
    <div class="panel"><div style="font-weight:650;font-size:13px;margin-bottom:6px">Checklist</div>${CK.map(([k,l,go])=>`<label class="v6-check"><input type="checkbox" ${st[k]?'checked':''} onchange="revToggle('${key}','${k}',this.checked)"><span style="flex:1">${l}</span><button class="btn ghost" style="padding:3px 9px;font-size:11px" onclick="event.preventDefault();${go()}">Open</button></label>`).join('')}
     <div class="planner-bar" style="margin-top:10px"><i style="width:${Math.round(CK.filter(c=>st[c[0]]).length/CK.length*100)}%"></i></div>
     <div class="field" style="margin-top:14px"><label>Top 3 priorities for next week</label><textarea id="revPri" rows="3" placeholder="1. …">${esc(st.pri||'')}</textarea></div>
     <div class="field"><label>Reflection — what worked, what didn’t?</label><textarea id="revRef" rows="4">${esc(st.ref||'')}</textarea></div>
     <div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn ghost" onclick="copyText(reviewMarkdown('${ws}'),'Review copied as Markdown')">Copy</button><button class="btn pri" onclick="saveReview('${ws}')">Save to Journal</button></div></div>
    <div><div class="panel" style="margin-bottom:14px"><div style="font-weight:650;font-size:13px;margin-bottom:6px">Insights</div>${ins.map(i=>`<div class="v6-ins">${i}</div>`).join('')}</div>
     <div class="panel"><div style="font-weight:650;font-size:13px;margin-bottom:6px">Completed this week</div>${doneT.length?doneT.slice(0,14).map(t=>`<div class="row" onclick="editTask(byId('tasks',${t.id}))" style="cursor:pointer"><span style="color:var(--green)">✓</span><span class="t">${esc(t.title)}</span><span class="d">${esc(projName(t.project_id)||'')}</span></div>`).join(''):'<div class="empty" style="padding:18px">Nothing completed yet this week.</div>'}</div></div>
   </div>`;
  ['revPri','revRef'].forEach(id=>{ const t=document.getElementById(id); t.oninput=()=>{ let s={}; try{s=JSON.parse(localStorage.getItem(key)||'{}');}catch(e){} s[id==='revPri'?'pri':'ref']=t.value; localStorage.setItem(key,JSON.stringify(s)); }; });
}
function revToggle(key,k,v){ let s={}; try{s=JSON.parse(localStorage.getItem(key)||'{}');}catch(e){} s[k]=v; localStorage.setItem(key,JSON.stringify(s)); render(); }
function reviewMarkdown(ws){
  let s={}; try{s=JSON.parse(localStorage.getItem('m2_review_'+ws)||'{}');}catch(e){}
  const we=addDays(ws,6), done=(S.tasks||[]).filter(t=>t.status==='Done'&&t.completed_at&&t.completed_at.slice(0,10)>=ws&&t.completed_at.slice(0,10)<=we);
  const focus=(S.time_entries||[]).filter(e=>e.started_at&&e.started_at.slice(0,10)>=ws&&e.started_at.slice(0,10)<=we).reduce((a,e)=>a+(+e.duration_min||0),0);
  return `Weekly review — ${ws} → ${we}\n\nFocus: ${fmtHours(focus)} · Tasks done: ${done.length}\n\nDone:\n${done.map(t=>'- '+t.title).join('\n')||'-'}\n\nPriorities next week:\n${s.pri||'-'}\n\nReflection:\n${s.ref||'-'}\n`;
}
async function saveReview(ws){
  const txt=reviewMarkdown(ws); const ex=(S.ts_entries||[]).find(j=>j.kind==='journal'&&j.title==='Weekly review — '+ws);
  await mutate(ex?'PUT':'POST','ts_entries',ex&&ex.id,{kind:'journal',title:'Weekly review — '+ws,content:txt,entry_date:iso(new Date())});
  await load(); toast('Saved to Goals → Journal');
}

function v6DashStrip(){
  const today=iso(new Date());
  const inbox=(S.inbox||[]).length, rnew=(S.radar_items||[]).filter(r=>r.state==='new'), hot=rnew.filter(r=>+r.score>=80).length;
  const unproved=(S.results||[]).filter(r=>r.status!=='Proved'&&!['Definition','Example','Remark'].includes(r.kind)).length;
  const toread=(S.refs||[]).filter(r=>r.status==='To read').length;
  const blocks=planBlocks(today).filter(b=>b.kind==='task'), nowMin=new Date().getHours()*60+new Date().getMinutes();
  const pr={High:0,Normal:1,Low:2};
  const next=blocks.find(b=>b.end>nowMin&&!b.done)?.src || (S.tasks||[]).filter(t=>t.status!=='Done').sort((a,b)=>(a.deadline||'9999').localeCompare(b.deadline||'9999')||pr[a.priority]-pr[b.priority])[0];
  const tile=(ic,n,l,sub,go)=>`<button class="v6-tile" onclick="${go}"><span class="v6-ic">${ic}</span><span><b>${n}</b><small>${l}</small>${sub?`<em>${sub}</em>`:''}</span></button>`;
  return `<div class="v6-strip">
    ${typeof VL_SUM!=='undefined'&&VL_SUM&&VL_SUM.available?tile('📖',VL_SUM.in_progress,'Reading in VanoLib',VL_SUM.pages_week+' pages this week',"VL_TAB='overview';setView('vanolib')"):''}
    <div class="v6-next"><div class="demo-note">Next focus${blocks.length?` · ${blocks.length} block${blocks.length>1?'s':''} planned today`:''}</div><div class="v6-next-t">${next?esc(next.title):'Nothing planned'}</div>
     <div style="display:flex;gap:6px;margin-top:8px">${next?`<button class="btn pri" style="padding:6px 12px;font-size:12px" onclick="focusOnTask(${next.id})">◎ Focus</button>`:''}<button class="btn ghost" style="padding:6px 12px;font-size:12px" onclick="PLANDATE=iso(new Date());setView('planner')">Plan day</button></div></div>
  </div>`;
}

V6_TITLES.vanolib='VanoLib'; TITLES.vanolib='VanoLib';
TITLES.vanonote='VanoNote'; TITLES.vanoleaf='VanoLeaf · LaTeX';
const EMB={vanonote:{port:8766,name:'VanoNote',desc:'Notes, annotated PDFs and audio — inside VanoAtlas.'},
           vanoleaf:{port:5500,name:'VanoLeaf',desc:'LaTeX editor with compilation and PDF preview — inside VanoAtlas.'}};
const EMB_OK={};
function embHead(k){ const a=EMB[k]; return `<div class="chart-head"><div><h2 style="font-size:19px">${a.name}</h2><div class="demo-note">${a.desc}</div></div><div style="display:flex;gap:6px"><button class="btn ghost" title="Reload" onclick="embReload()">↻</button><button class="btn ghost" title="Full screen" onclick="const f=document.getElementById('embFrame');if(f)f.requestFullscreen()">⤢</button></div></div>`; }
async function rEmbed(el,k){
  const a=EMB[k], url=`http://${location.hostname}:${a.port}/`;
  const ex=el.querySelector('#embFrame'); if(ex&&ex.dataset.k===k) return;
  if(EMB_OK[k]){ el.innerHTML=embHead(k)+`<iframe id="embFrame" data-k="${k}" class="v6-vlframe" src="${url}" allow="fullscreen; clipboard-write; microphone; camera" title="${a.name}"></iframe>`; return; }
  el.innerHTML=embHead(k)+`<div class="panel"><div class="empty">Starting ${a.name}…</div></div>`;
  let r; try{ r=await api('GET','/api/apps/'+k+'/start'); }catch(e){ r={ok:false,error:'VanoAtlas server unreachable'}; }
  if(VIEW!==k) { if(r&&r.ok) EMB_OK[k]=true; return; }
  if(r&&r.ok){ EMB_OK[k]=true; rEmbed(el,k); }
  else el.innerHTML=embHead(k)+`<div class="panel"><div class="empty">${esc((r&&r.error)||('Could not start '+a.name))}<br><button class="btn ghost" style="margin-top:10px" onclick="render()">Retry</button></div></div>`;
}
function embReload(){ const f=document.getElementById('embFrame'); if(f) f.src=f.src; }
function rVanonote(el){ rEmbed(el,'vanonote'); }
function rVanoleaf(el){ rEmbed(el,'vanoleaf'); }
let PLC=null;
async function plReplan(){ await api('GET','/api/planner/replan'); toast('Today is planned'); await load(); }
async function plSettings(){ PLC=await api('GET','/api/planner/config');
  const D=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
  modal('Planner settings',[
    {k:'wake',label:'Wake up',type:'time',half:true},{k:'leave',label:'Leave home',type:'time',half:true},
    {k:'am_start',label:'Morning work starts',type:'time',half:true},{k:'am_end',label:'Morning work ends',type:'time',half:true},
    {k:'pm_start',label:'Afternoon work starts',type:'time',half:true},{k:'pm_end',label:'Afternoon work ends',type:'time',half:true},
    {k:'meet_title',label:'Weekly meeting'},
    {k:'meet_day',label:'Meeting day',type:'select',options:D.map((d,i)=>[String(i),d]),half:true},
    {k:'meet_start',label:'Meeting starts',type:'time',half:true},{k:'meet_end',label:'Meeting ends',type:'time',half:true},
  ],{...PLC,meet_day:String(PLC.meet_day)},async o=>{ await api('GET','/api/planner/save?'+Object.entries(o).map(([k,v])=>k+'='+encodeURIComponent(v)).join('&')); toast('Planner saved — click Re-plan to apply today'); await load(); });
}
function plCard(){ return '<div class="panel" style="padding:12px 14px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap"><div><b style="font-size:14px">Day Planner</b><div class="demo-note">Fill today from your routine, your open tasks and your weekly meeting. Set your hours in Settings.</div></div><div style="display:flex;gap:6px"><button class="btn ghost" onclick="plSettings()">Settings</button><button class="btn" onclick="plReplan()">Re-plan today</button></div></div>'; }

const VA_FR={
"## Context and problem":"## Contexte et probl\u00e8me",
"## Detailed summary":"## R\u00e9sum\u00e9 d\u00e9taill\u00e9",
"## Key references":"## R\u00e9f\u00e9rences principales",
"## Main contributions":"## Contributions principales",
"## Main results":"## R\u00e9sultats principaux",
"## Methods and key ideas":"## M\u00e9thodes et id\u00e9es cl\u00e9s",
"## Open questions":"## Questions ouvertes",
"% actual focus vs planned estimates.":"% de concentration r\u00e9elle par rapport aux estimations.",
"% done,":"% termin\u00e9,",
"% faster":"% plus rapide",
"% longer":"% plus long",
"% of all tracked focus). Protect it for proofs and writing.":"% de toute la concentration suivie). Prot\u00e9gez-le pour les preuves et la r\u00e9daction.",
"% read":"% lu",
"% used":"% utilis\u00e9",
"% vs last week":"% par rapport \u00e0 la semaine derni\u00e8re",
"(16 GB+). Your articles never leave your Mac.":"(16 Go et plus). Vos articles ne quittent jamais votre Mac.",
"(8 GB Mac) or":"(Mac 8 Go) ou",
"(after the":"(apr\u00e8s le",
"(empty note)":"(note vide)",
"(not available yet)":"(pas encore disponible)",
"(will be downloaded at the first check)":"(sera t\u00e9l\u00e9charg\u00e9 \u00e0 la premi\u00e8re v\u00e9rification)",
"(your plan) \u2014 back around":"(votre forfait) \u2014 de retour vers",
"**In short.**":"**En bref.**",
"+ Add":"+ Ajouter",
"+ Add quote":"+ Ajouter une citation",
"+ Bibliography":"+ Bibliographie",
"+ Event":"+ \u00c9v\u00e9nement",
"+ Milestone":"+ Jalon",
"+ New":"+ Nouveau",
"+ New task":"+ Nouvelle t\u00e2che",
"+ Result":"+ R\u00e9sultat",
", no web.":", sans web.",
"1 day":"1 jour",
"1 hour":"1 heure",
"1 month":"1 mois",
"1 year":"1 an",
"30 days":"30 jours",
"5 days":"5 jours",
": keywords in title/abstract, followed authors, categories, freshness. Runs on your Mac via the public arXiv API.":" : mots-cl\u00e9s dans le titre et le r\u00e9sum\u00e9, auteurs suivis, cat\u00e9gories, fra\u00eecheur. Fonctionne sur votre Mac avec l'API publique d'arXiv.",
"A private space for your goals, personal growth notes, and how you've been doing \u2014 just for you.":"Un espace priv\u00e9 pour vos objectifs, vos notes de progression et votre \u00e9tat d'esprit \u2014 rien que pour vous.",
"Abstract":"R\u00e9sum\u00e9",
"Accepted":"Accept\u00e9",
"Achievements":"R\u00e9ussites",
"Activate Focus / Zen Mode":"Activer le mode Focus / Zen",
"Active":"Actif",
"Active Projects":"Projets actifs",
"Activity \u00b7 14 days":"Activit\u00e9 \u00b7 14 jours",
"Actual":"R\u00e9el",
"Actual ($)":"R\u00e9el ($)",
"Add an event":"Ajouter un \u00e9v\u00e9nement",
"Add book":"Ajouter un livre",
"Add by DOI or arXiv id, import/export .bib for Overleaf \u00b7 Cite in notes with":"Ajout par DOI ou identifiant arXiv, import/export .bib pour Overleaf \u00b7 Citer dans les notes avec",
"Add estimated minutes to tasks to compare plan vs reality.":"Ajoutez des minutes estim\u00e9es aux t\u00e2ches pour comparer le pr\u00e9vu et le r\u00e9el.",
"Add quote":"Ajouter une citation",
"Added":"Ajout\u00e9",
"Added to Bibliography":"Ajout\u00e9 \u00e0 la bibliographie",
"Added to Inbox":"Ajout\u00e9 \u00e0 la bo\u00eete de r\u00e9ception",
"Added to VanoLib To Read":"Ajout\u00e9 \u00e0 \u00ab \u00c0 lire \u00bb dans VanoLib",
"Added to favourites \u2014 it will appear more often":"Ajout\u00e9e aux favoris \u2014 elle appara\u00eetra plus souvent",
"Adjacent":"Voisin",
"Admin":"Administration",
"Administrative":"Administratif",
"Advisory Meeting":"R\u00e9union de direction",
"Afternoon work ends":"Fin du travail de l'apr\u00e8s-midi",
"Afternoon work starts":"D\u00e9but du travail de l'apr\u00e8s-midi",
"Aged cream paper":"Papier cr\u00e8me vieilli",
"Algebra":"Alg\u00e8bre",
"Align":"Aligner",
"Aligned equations":"\u00c9quations align\u00e9es",
"All":"Tous",
"All categories":"Toutes les cat\u00e9gories",
"All projects":"Tous les projets",
"All quotes":"Toutes les citations",
"All recent":"Tous les r\u00e9cents",
"All set \u2726":"Tout est pr\u00eat \u2726",
"Already in Bibliography":"D\u00e9j\u00e0 dans la bibliographie",
"Amount":"Montant",
"Amount ($)":"Montant ($)",
"Analysis":"Analyse",
"Analyze now":"Analyser maintenant",
"Another quote":"Une autre citation",
"Anything on your mind? (optional)":"Quelque chose en t\u00eate ? (facultatif)",
"App-specific password \u2014 from appleid.apple.com, not your real Apple ID password":"Mot de passe pour app \u2014 sur appleid.apple.com, pas votre vrai mot de passe Apple",
"Applied Mathematics":"Math\u00e9matiques appliqu\u00e9es",
"Apps":"Applications",
"Archive":"Archiver",
"Ask":"Demander",
"Attendees":"Participants",
"Audit + safe cleanup":"Audit + nettoyage s\u00fbr",
"Aurora mode":"Mode Aurora",
"Author":"Auteur",
"Authors (comma-separated)":"Auteurs (s\u00e9par\u00e9s par des virgules)",
"Auto-corrected \u00b7 saved":"Corrig\u00e9 automatiquement \u00b7 enregistr\u00e9",
"Auto-plan today":"Planifier aujourd'hui automatiquement",
"Automatic check every 12 h (macOS notification if score \u2265 90)":"V\u00e9rification automatique toutes les 12 h (notification macOS si score \u2265 90)",
"Automne mode":"Mode Automne",
"Autumn":"Automne",
"Award":"Prix",
"Back online":"De nouveau en ligne",
"Backed up to Drive at":"Sauvegard\u00e9 sur Drive \u00e0",
"Backing up\u2026":"Sauvegarde\u2026",
"Backlog":"En attente",
"Backup + Safe Clean":"Sauvegarde + nettoyage s\u00fbr",
"Backup to Drive":"Sauvegarder sur Drive",
"Being read.":"En cours de lecture.",
"Best activity day":"Meilleur jour d'activit\u00e9",
"BibTeX key":"Cl\u00e9 BibTeX",
"Bibliography":"Bibliographie",
"Biography":"Biographie",
"Black":"Noir",
"Bold":"Gras",
"Budgeted":"Budg\u00e9t\u00e9",
"Budgeted ($)":"Budg\u00e9t\u00e9 ($)",
"Bullet list":"Liste \u00e0 puces",
"Calls":"Appels",
"Cancel":"Annuler",
"Cancelled":"Annul\u00e9",
"Capacity":"Capacit\u00e9",
"Capture":"Saisir",
"Capture an idea":"Noter une id\u00e9e",
"Capture everything in one line, triage later. Also:":"Tout noter en une ligne, trier plus tard. Aussi :",
"Casual handwriting":"\u00c9criture d\u00e9contract\u00e9e",
"Category":"Cat\u00e9gorie",
"Center align":"Centrer",
"Centered text":"Texte centr\u00e9",
"Changes":"Modifications",
"Chapter":"Chapitre",
"Checking video tools\u2026":"V\u00e9rification des outils vid\u00e9o\u2026",
"Checklist":"Liste de contr\u00f4le",
"Choose a task first":"Choisissez d'abord une t\u00e2che",
"Cited":"Cit\u00e9",
"Class":"Classe",
"Clean screen off":"\u00c9cran \u00e9pur\u00e9 d\u00e9sactiv\u00e9",
"Clean screen on \u00b7 Esc to exit":"\u00c9cran \u00e9pur\u00e9 activ\u00e9 \u00b7 \u00c9chap pour quitter",
"Cleaned safely \u00b7 backup created":"Nettoy\u00e9 en s\u00e9curit\u00e9 \u00b7 sauvegarde cr\u00e9\u00e9e",
"Cleanup failed":"\u00c9chec du nettoyage",
"Clear":"Effacer",
"Click a concept in the graph to see the papers, their contributions and related concepts.":"Cliquez sur un concept du graphe pour voir les articles, leurs contributions et les concepts li\u00e9s.",
"Close":"Fermer",
"Clothing":"V\u00eatements",
"Code block":"Bloc de code",
"Collect online now":"Collecter en ligne maintenant",
"Collected every morning at 7:10 from Quanta, Tao, Kalai, n-Category Caf\u00e9, Not Even Wrong, Phys.org, Science News, Nature, New Scientist and Google News.":"Collect\u00e9es chaque matin \u00e0 7 h 10 depuis Quanta, Tao, Kalai, n-Category Caf\u00e9, Not Even Wrong, Phys.org, Science News, Nature, New Scientist et Google News.",
"Collecting quotes online (Wikiquote + ZenQuotes)\u2026":"Collecte de citations en ligne (Wikiquote + ZenQuotes)\u2026",
"Combinatorics":"Combinatoire",
"Command Center":"Centre de commandes",
"Commands":"Commandes",
"Compilation failed":"\u00c9chec de la compilation",
"Compile error":"Erreur de compilation",
"Compiling live as you type\u2026":"Compilation en direct pendant la saisie\u2026",
"Compiling\u2026":"Compilation\u2026",
"Completed tasks":"T\u00e2ches termin\u00e9es",
"Completed this week":"Termin\u00e9 cette semaine",
"Computation":"Calcul",
"Computer Science":"Informatique",
"Conference":"Conf\u00e9rence",
"Connected":"Connect\u00e9",
"Consistency turns small steps into real progress.":"La r\u00e9gularit\u00e9 transforme les petits pas en vrais progr\u00e8s.",
"Context":"Contexte",
"Context and files (optional)":"Contexte et fichiers (facultatif)",
"Context and problem":"Contexte et probl\u00e8me",
"Continue":"Continuer",
"Continue reading":"Continuer la lecture",
"Contribution:":"Contribution :",
"Copied":"Copi\u00e9",
"Copy":"Copier",
"Copy all":"Tout copier",
"Copy as LaTeX":"Copier en LaTeX",
"Copy as Markdown":"Copier en Markdown",
"Corollary":"Corollaire",
"Cost":"Co\u00fbt",
"Could not read data health":"Impossible de lire l'\u00e9tat des donn\u00e9es",
"Could not start":"Impossible de d\u00e9marrer",
"Could not start dictation":"Impossible de d\u00e9marrer la dict\u00e9e",
"Create + estimate work":"Cr\u00e9er + estimer le travail",
"Create a task first":"Cr\u00e9ez d'abord une t\u00e2che",
"Creditor":"Cr\u00e9ancier",
"Custom BibTeX (leave empty to auto-generate)":"BibTeX personnalis\u00e9 (laisser vide pour le g\u00e9n\u00e9rer)",
"Customizable Colors":"Couleurs personnalisables",
"Cyber mode":"Mode Cyber",
"DOI (10.1007/\u2026) or arXiv id (2405.11821) or URL \u2014 Enter to fetch metadata":"DOI (10.1007/\u2026), identifiant arXiv (2405.11821) ou URL \u2014 Entr\u00e9e pour r\u00e9cup\u00e9rer les m\u00e9tadonn\u00e9es",
"Daily Streak":"S\u00e9rie quotidienne",
"Daily Tasks":"T\u00e2ches quotidiennes",
"Daily activity":"Activit\u00e9 quotidienne",
"Daily focus average":"Concentration moyenne par jour",
"Daily summary time":"Heure du r\u00e9sum\u00e9 quotidien",
"Daily task/meeting summary":"R\u00e9sum\u00e9 quotidien des t\u00e2ches et r\u00e9unions",
"Dashboard":"Tableau de bord",
"Data Health":"\u00c9tat des donn\u00e9es",
"Data health":"\u00c9tat des donn\u00e9es",
"Day":"Jour",
"Day Planner":"Planificateur",
"Deadline":"\u00c9ch\u00e9ance",
"Deadlines":"\u00c9ch\u00e9ances",
"Definition":"D\u00e9finition",
"Delete":"Supprimer",
"Deleted":"Supprim\u00e9",
"Dependency graph":"Graphe des d\u00e9pendances",
"Depends on (labels or BibTeX keys, comma-separated)":"D\u00e9pend de (\u00e9tiquettes ou cl\u00e9s BibTeX, s\u00e9par\u00e9es par des virgules)",
"Detailed summary":"R\u00e9sum\u00e9 d\u00e9taill\u00e9",
"Dictation error:":"Erreur de dict\u00e9e :",
"Dictation language":"Langue de la dict\u00e9e",
"Difficult":"Difficile",
"Directly about your work":"Directement li\u00e9 \u00e0 votre travail",
"Discipline today, freedom tomorrow.":"Discipline aujourd'hui, libert\u00e9 demain.",
"Dismiss shown":"Masquer ceux affich\u00e9s",
"Dismissed":"Masqu\u00e9",
"Do now":"\u00c0 faire maintenant",
"Does not repeat":"Ne se r\u00e9p\u00e8te pas",
"Domain (e.g. Poisson geometry)":"Domaine (par ex. g\u00e9om\u00e9trie de Poisson)",
"Done":"Termin\u00e9",
"Done:":"Termin\u00e9 :",
"Dotted paper":"Papier pointill\u00e9",
"Drag to resize":"Glisser pour redimensionner",
"Drop here to unschedule":"D\u00e9poser ici pour d\u00e9planifier",
"Due date":"Date d'\u00e9ch\u00e9ance",
"Edit":"Modifier",
"Edit book":"Modifier le livre",
"Edit budget line":"Modifier la ligne de budget",
"Edit check-in":"Modifier le bilan",
"Edit daily item":"Modifier l'\u00e9l\u00e9ment quotidien",
"Edit debt":"Modifier la dette",
"Edit event":"Modifier l'\u00e9v\u00e9nement",
"Edit expense":"Modifier la d\u00e9pense",
"Edit goal":"Modifier l'objectif",
"Edit journal entry":"Modifier l'entr\u00e9e du journal",
"Edit milestone":"Modifier le jalon",
"Edit project":"Modifier le projet",
"Edit quote":"Modifier la citation",
"Edit reference":"Modifier la r\u00e9f\u00e9rence",
"Edit result":"Modifier le r\u00e9sultat",
"Edit task":"Modifier la t\u00e2che",
"Edit win":"Modifier la r\u00e9ussite",
"Education":"\u00c9ducation",
"Elegant antique":"Ancien \u00e9l\u00e9gant",
"Empty the Inbox (":"Vider la bo\u00eete de r\u00e9ception (",
"End":"Fin",
"End time":"Heure de fin",
"Energy":"\u00c9nergie",
"Entertainment":"Loisirs",
"Equation":"\u00c9quation",
"Error":"Erreur",
"Error:":"Erreur :",
"Estimate variance":"\u00c9cart d'estimation",
"Estimated":"Estim\u00e9",
"Estimated / planned work":"Travail estim\u00e9 / pr\u00e9vu",
"Estimated time (minutes)":"Temps estim\u00e9 (minutes)",
"Event":"\u00c9v\u00e9nement",
"Event deleted":"\u00c9v\u00e9nement supprim\u00e9",
"Every definition, lemma and theorem with its status and dependencies. A result is only as solid as what it relies on \u2014 weak links are flagged automatically.":"Chaque d\u00e9finition, lemme et th\u00e9or\u00e8me avec son statut et ses d\u00e9pendances. Un r\u00e9sultat n'est pas plus solide que ce sur quoi il repose \u2014 les maillons faibles sont signal\u00e9s automatiquement.",
"Every proof counts \u2014 log your wins \ud83c\udfc6":"Chaque preuve compte \u2014 notez vos r\u00e9ussites \ud83c\udfc6",
"Everything":"Tout",
"Example":"Exemple",
"Example request (optional)":"Exemple de demande (facultatif)",
"Exit large view (Esc)":"Quitter la grande vue (\u00c9chap)",
"Export .bib":"Exporter .bib",
"Favourite":"Favori",
"Fetching\u2026":"R\u00e9cup\u00e9ration\u2026",
"Files on your Mac, one path per line (e.g. ~/Documents/paper/main.tex)":"Fichiers sur votre Mac, un chemin par ligne (par ex. ~/Documents/article/main.tex)",
"Files:":"Fichiers :",
"Filter by title, author, concept\u2026":"Filtrer par titre, auteur, concept\u2026",
"Filter current view":"Filtrer la vue actuelle",
"Fine calligraphy":"Calligraphie fine",
"Finish session":"Terminer la session",
"Finish the focus session before closing":"Terminez la session de concentration avant de fermer",
"Fit / reset view (0)":"Ajuster / r\u00e9initialiser la vue (0)",
"Fluid Animations":"Animations fluides",
"Focus":"Concentration",
"Focus Session":"Session de concentration",
"Focus session":"Session de concentration",
"Focus time saved":"Temps de concentration enregistr\u00e9",
"Focus tracked":"Concentration suivie",
"Focus:":"Concentration :",
"Focused work tracked":"Travail concentr\u00e9 suivi",
"Followed authors (comma-separated, e.g. Safronov, Calaque)":"Auteurs suivis (s\u00e9par\u00e9s par des virgules, par ex. Safronov, Calaque)",
"Font / math renderer unavailable offline \u2014 simplified preview.":"Police / rendu math\u00e9matique indisponible hors ligne \u2014 aper\u00e7u simplifi\u00e9.",
"Food":"Alimentation",
"Footnote":"Note de bas de page",
"Footnote text:":"Texte de la note :",
"Fountain pen blue":"Bleu stylo plume",
"Free key from aistudio.google.com/apikey (no credit card). Best quality among free options. Note: on the free tier Google may use the text to improve its models.":"Cl\u00e9 gratuite sur aistudio.google.com/apikey (sans carte bancaire). Meilleure qualit\u00e9 parmi les options gratuites. Remarque : avec l'offre gratuite, Google peut utiliser le texte pour am\u00e9liorer ses mod\u00e8les.",
"Free key from aistudio.google.com/apikey (no credit card). Note: on the free tier Google may use the text to improve its models.":"Cl\u00e9 gratuite sur aistudio.google.com/apikey (sans carte bancaire). Remarque : avec l'offre gratuite, Google peut utiliser le texte pour am\u00e9liorer ses mod\u00e8les.",
"Fri":"Ven",
"Friday":"Vendredi",
"Full":"Complet",
"Full power":"Pleine puissance",
"Full reading":"Lecture compl\u00e8te",
"Full readings":"Lectures compl\u00e8tes",
"Full screen":"Plein \u00e9cran",
"Full text ready (":"Texte int\u00e9gral pr\u00eat (",
"General":"G\u00e9n\u00e9ral",
"Generating image\u2026":"G\u00e9n\u00e9ration de l'image\u2026",
"Geometry":"G\u00e9om\u00e9trie",
"Go to Daily Tasks":"Aller aux t\u00e2ches quotidiennes",
"Go to Dashboard":"Aller au tableau de bord",
"Go to Day Planner":"Aller au planificateur",
"Go to Knowledge Graph":"Aller au graphe de connaissances",
"Go to Meetings":"Aller aux r\u00e9unions",
"Go to Notes":"Aller aux notes",
"Go to Projects":"Aller aux projets",
"Go to Tasks":"Aller aux t\u00e2ches",
"Go to Timeline":"Aller \u00e0 la chronologie",
"Go to VanoLib":"Aller \u00e0 VanoLib",
"Goals":"Objectifs",
"Good":"Bien",
"Great":"Excellent",
"Grid paper":"Papier quadrill\u00e9",
"Handwrite note":"Note manuscrite",
"Header 1":"Titre 1",
"Header 2":"Titre 2",
"Heading":"Titre",
"Headlines":"Gros titres",
"Health":"Sant\u00e9",
"Hidden":"Masqu\u00e9",
"Hide":"Masquer",
"Hide distractions and stay focused.":"Masquez les distractions et restez concentr\u00e9.",
"High":"Haute",
"High-tech \u00b7 Futuristic \u00b7 Focused":"High-tech \u00b7 Futuriste \u00b7 Concentr\u00e9",
"Highlight":"Surligner",
"History":"Historique",
"Horizontal rule":"Ligne horizontale",
"Housing":"Logement",
"How are you doing?":"Comment allez-vous ?",
"Idle":"Inactif",
"Image export failed.":"\u00c9chec de l'export de l'image.",
"Image export unavailable \u2014 try \"Export PDF\" instead.":"Export d'image indisponible \u2014 essayez plut\u00f4t \u00ab Export PDF \u00bb.",
"Image exported \u2713":"Image export\u00e9e \u2713",
"Import":"Importer",
"Import .bib":"Importer .bib",
"Import BibTeX":"Importer BibTeX",
"Import failed":"\u00c9chec de l'import",
"Important, not urgent":"Important, pas urgent",
"Imported":"Import\u00e9",
"In Progress":"En cours",
"In Review":"En relecture",
"In To Read":"Dans \u00ab \u00c0 lire \u00bb",
"In progress":"En cours",
"In short.":"En bref.",
"Inbox":"Bo\u00eete de r\u00e9ception",
"Inbox zero \u2726 \u2014 nothing waiting.":"Bo\u00eete vide \u2726 \u2014 rien en attente.",
"Info \u00b7 Science News":"Info \u00b7 Actualit\u00e9s scientifiques",
"Ink":"Encre",
"Insights":"Analyses",
"Instructions: how it works, step by step \u2014 like explaining the job to a new assistant":"Instructions : comment il travaille, \u00e9tape par \u00e9tape \u2014 comme si vous expliquiez le travail \u00e0 un nouvel assistant",
"Irregularity":"Irr\u00e9gularit\u00e9",
"Italic":"Italique",
"Items":"\u00c9l\u00e9ments",
"Journal / venue":"Revue / lieu",
"Key":"Cl\u00e9",
"Key references":"R\u00e9f\u00e9rences principales",
"Key saved (":"Cl\u00e9 enregistr\u00e9e (",
"Keyboard shortcuts":"Raccourcis clavier",
"Keywords / phrases (comma-separated)":"Mots-cl\u00e9s / expressions (s\u00e9par\u00e9s par des virgules)",
"Kind":"Type",
"Knowledge Graph":"Graphe de connaissances",
"LaTeX editor with compilation and PDF preview \u2014 inside VanoAtlas.":"\u00c9diteur LaTeX avec compilation et aper\u00e7u PDF \u2014 dans VanoAtlas.",
"Label (e.g. Lemma 3.4)":"\u00c9tiquette (par ex. Lemme 3.4)",
"Large view":"Grande vue",
"Last":"Dernier",
"Last 14 days":"14 derniers jours",
"Last 30 days":"30 derniers jours",
"Last 7 days":"7 derniers jours",
"Last export":"Dernier export",
"Last run":"Derni\u00e8re ex\u00e9cution",
"Latest in your pinned categories":"Derni\u00e8res parutions dans vos cat\u00e9gories \u00e9pingl\u00e9es",
"Leave home":"D\u00e9part de la maison",
"Lemma":"Lemme",
"Library":"Biblioth\u00e8que",
"Lined paper":"Papier lign\u00e9",
"Link":"Lien",
"Link URL:":"URL du lien :",
"Link \u2197":"Lien \u2197",
"Linked from \u2190":"Li\u00e9 depuis \u2190",
"Links come from project membership,":"Les liens viennent de l'appartenance aux projets,",
"Links \u2192":"Liens \u2192",
"Loading VanoLib\u2026":"Chargement de VanoLib\u2026",
"Loading font and math renderer\u2026":"Chargement de la police et du rendu math\u00e9matique\u2026",
"Loading news\u2026":"Chargement des actualit\u00e9s\u2026",
"Loading\u2026":"Chargement\u2026",
"Local \u00b7 instant":"Local \u00b7 instantan\u00e9",
"Local copy of the site:":"Copie locale du site :",
"Location":"Lieu",
"Logged":"Enregistr\u00e9",
"Looking up":"Recherche",
"Lookup failed":"Recherche \u00e9chou\u00e9e",
"Low":"Basse",
"Main contributions":"Contributions principales",
"Main results":"R\u00e9sultats principaux",
"Manual":"Manuel",
"Market brief \u00b7":"Point march\u00e9s \u00b7",
"Mathematical Logic":"Logique math\u00e9matique",
"Mathematics":"Math\u00e9matiques",
"Media":"M\u00e9dias",
"Medium":"Moyenne",
"Meeting day":"Jour de r\u00e9union",
"Meeting ends":"Fin de la r\u00e9union",
"Meeting reminders":"Rappels de r\u00e9union",
"Meeting starts":"D\u00e9but de la r\u00e9union",
"Meeting time":"Heure de la r\u00e9union",
"Meetings":"R\u00e9unions",
"Mental state":"\u00c9tat d'esprit",
"Methods":"M\u00e9thodes",
"Methods and key ideas":"M\u00e9thodes et id\u00e9es cl\u00e9s",
"Microphone not available:":"Micro indisponible :",
"Milestone":"Jalon",
"Milestone deleted":"Jalon supprim\u00e9",
"Min score":"Score minimum",
"Mine":"Les miens",
"Modern \u00b7 Elegant \u00b7 Inspiring":"Moderne \u00b7 \u00c9l\u00e9gant \u00b7 Inspirant",
"Mon":"Lun",
"Monday":"Lundi",
"Month (YYYY-MM)":"Mois (AAAA-MM)",
"Month insights":"Analyse du mois",
"Monthly Productivity":"Productivit\u00e9 mensuelle",
"Monthly productivity chart":"Graphique de productivit\u00e9 mensuelle",
"Mood":"Humeur",
"More":"Plus",
"Morning work ends":"Fin du travail du matin",
"Morning work starts":"D\u00e9but du travail du matin",
"Move to\u2026":"D\u00e9placer vers\u2026",
"Moved to":"D\u00e9plac\u00e9 vers",
"Natural cursive":"Cursive naturelle",
"Neutral":"Neutre",
"Never synced":"Jamais synchronis\u00e9",
"New":"Nouveau",
"New budget line":"Nouvelle ligne de budget",
"New daily item":"Nouvel \u00e9l\u00e9ment quotidien",
"New debt":"Nouvelle dette",
"New event":"Nouvel \u00e9v\u00e9nement",
"New expense":"Nouvelle d\u00e9pense",
"New goal":"Nouvel objectif",
"New ideas":"Nouvelles id\u00e9es",
"New item in current view":"Nouvel \u00e9l\u00e9ment dans la vue actuelle",
"New journal entry":"Nouvelle entr\u00e9e de journal",
"New meeting":"Nouvelle r\u00e9union",
"New milestone":"Nouveau jalon",
"New note":"Nouvelle note",
"New notes \u00b7 refs \u00b7 results":"Nouvelles notes \u00b7 r\u00e9fs \u00b7 r\u00e9sultats",
"New papers this week":"Nouveaux articles cette semaine",
"New preprints ranked for":"Nouvelles pr\u00e9publications class\u00e9es pour",
"New project":"Nouveau projet",
"New reference":"Nouvelle r\u00e9f\u00e9rence",
"New result":"Nouveau r\u00e9sultat",
"New task":"Nouvelle t\u00e2che",
"New win":"Nouvelle r\u00e9ussite",
"Next focus":"Prochaine concentration",
"Next up":"\u00c0 suivre",
"Night mode":"Mode nuit",
"No":"Non",
"No VanoLeaf project found.":"Aucun projet VanoLeaf trouv\u00e9.",
"No active projects \u2014 create one in Projects.":"Aucun projet actif \u2014 cr\u00e9ez-en un dans Projets.",
"No budget for":"Pas de budget pour",
"No check-ins yet \u2014 log how you're doing with + New":"Aucun bilan pour l'instant \u2014 notez comment vous allez avec + Nouveau",
"No debts \ud83c\udf89":"Aucune dette \ud83c\udf89",
"No expenses this month":"Aucune d\u00e9pense ce mois-ci",
"No goals yet \u2014 add one with + New":"Aucun objectif pour l'instant \u2014 ajoutez-en un avec + Nouveau",
"No items for this day \u2014 add your routine":"Aucun \u00e9l\u00e9ment pour ce jour \u2014 ajoutez votre routine",
"No journal entries yet \u2014 write your first one with + New":"Aucune entr\u00e9e de journal \u2014 \u00e9crivez la premi\u00e8re avec + Nouveau",
"No market brief yet. Click \u201cUpdate now\u201d.":"Pas encore de point march\u00e9s. Cliquez sur \u00ab Update now \u00bb.",
"No match.":"Aucun r\u00e9sultat.",
"No new papers since last check":"Aucun nouvel article depuis la derni\u00e8re v\u00e9rification",
"No new quotes:":"Pas de nouvelles citations :",
"No news collected yet. Click \u201cCollect now\u201d.":"Aucune actualit\u00e9 collect\u00e9e. Cliquez sur \u00ab Collect now \u00bb.",
"No note with this title yet":"Aucune note avec ce titre",
"No notes":"Aucune note",
"No notes yet":"Aucune note pour l'instant",
"No paper found for \u201c":"Aucun article trouv\u00e9 pour \u00ab ",
"No paper here for this period.":"Aucun article pour cette p\u00e9riode.",
"No papers yet. Set your keywords / authors, then \u201cFetch now\u201d. VanoAtlas also checks automatically every 12 hours.":"Aucun article pour l'instant. Indiquez vos mots-cl\u00e9s / auteurs, puis \u00ab Fetch now \u00bb. VanoAtlas v\u00e9rifie aussi automatiquement toutes les 12 heures.",
"No projects \u2014 create one with + New":"Aucun projet \u2014 cr\u00e9ez-en un avec + Nouveau",
"No quote here.":"Aucune citation ici.",
"No references yet \u2014 paste a DOI or arXiv id above, import a .bib, or save papers from the arXiv Radar.":"Aucune r\u00e9f\u00e9rence \u2014 collez un DOI ou un identifiant arXiv ci-dessus, importez un .bib, ou enregistrez des articles depuis le radar arXiv.",
"No report yet. Click \u201cGenerate now\u201d.":"Pas encore de rapport. Cliquez sur \u00ab Generate now \u00bb.",
"No request yet.":"Aucune demande pour l'instant.",
"No results yet. Add your definitions, lemmas and theorems \u2014 then link them with \u201cDepends on\u201d.":"Aucun r\u00e9sultat pour l'instant. Ajoutez vos d\u00e9finitions, lemmes et th\u00e9or\u00e8mes \u2014 puis reliez-les avec \u00ab D\u00e9pend de \u00bb.",
"No task yet.":"Aucune t\u00e2che pour l'instant.",
"No tasks yet":"Aucune t\u00e2che pour l'instant",
"No upcoming meetings":"Aucune r\u00e9union \u00e0 venir",
"No update published yet.":"Aucune mise \u00e0 jour publi\u00e9e.",
"Normal":"Normale",
"Not available:":"Indisponible :",
"Not created:":"Non cr\u00e9\u00e9 :",
"Not enough tracked activity yet.":"Pas encore assez d'activit\u00e9 suivie.",
"Not in VanoLib yet \u2014 it will appear after the next sync":"Pas encore dans VanoLib \u2014 il appara\u00eetra apr\u00e8s la prochaine synchronisation",
"Not queued:":"Non mis en file :",
"Not related":"Sans lien",
"Not saved:":"Non enregistr\u00e9 :",
"Not urgent + lower priority":"Pas urgent + priorit\u00e9 basse",
"Note created":"Note cr\u00e9\u00e9e",
"Note details":"D\u00e9tails de la note",
"Note graph":"Graphe des notes",
"Note not found":"Note introuvable",
"Note saved":"Note enregistr\u00e9e",
"Notes & results citing it":"Notes et r\u00e9sultats qui le citent",
"Notes, annotated PDFs and audio \u2014 inside VanoAtlas.":"Notes, PDF annot\u00e9s et audio \u2014 dans VanoAtlas.",
"Nothing completed yet this week.":"Rien de termin\u00e9 cette semaine pour l'instant.",
"Nothing found \u2014 type":"Rien trouv\u00e9 \u2014 tapez",
"Nothing here":"Rien ici",
"Nothing in progress.":"Rien en cours.",
"Nothing in this filter.":"Rien dans ce filtre.",
"Nothing planned":"Rien de pr\u00e9vu",
"Nothing scheduled \u2014 click + Event to add":"Rien de pr\u00e9vu \u2014 cliquez sur + \u00c9v\u00e9nement pour ajouter",
"Nothing to accept":"Rien \u00e0 accepter",
"Nothing to place \u2014 the day is full or empty":"Rien \u00e0 placer \u2014 la journ\u00e9e est pleine ou vide",
"Now:":"Actuel :",
"Number Theory":"Th\u00e9orie des nombres",
"Numbered list":"Liste num\u00e9rot\u00e9e",
"Off":"D\u00e9sactiv\u00e9",
"Office Hours":"Heures de bureau",
"On":"Activ\u00e9",
"On Hold":"En pause",
"One task. One timer. No clutter.":"Une t\u00e2che. Un minuteur. Rien d'autre.",
"Only a short summary so far.":"Seulement un court r\u00e9sum\u00e9 pour l'instant.",
"Only papers related to your research profile":"Seulement les articles li\u00e9s \u00e0 votre profil de recherche",
"Open":"Ouvrir",
"Open Tasks":"T\u00e2ches ouvertes",
"Open VanoLeaf":"Ouvrir VanoLeaf",
"Open VanoLib in its own tab":"Ouvrir VanoLib dans un onglet",
"Open in VanoLeaf":"Ouvrir dans VanoLeaf",
"Open in VanoLib":"Ouvrir dans VanoLib",
"Open map \u2197":"Ouvrir la carte \u2197",
"Open questions":"Questions ouvertes",
"Open questions and limitations":"Questions ouvertes et limites",
"Open the PDF in a new tab":"Ouvrir le PDF dans un nouvel onglet",
"Open the map in a large window":"Ouvrir la carte en grand",
"Other":"Autre",
"Other milestones":"Autres jalons",
"Outstanding: $":"Restant : $",
"Over budget":"Budget d\u00e9pass\u00e9",
"Over capacity. Consider moving lower-priority work.":"Capacit\u00e9 d\u00e9pass\u00e9e. Pensez \u00e0 d\u00e9placer le travail moins prioritaire.",
"Overdue":"En retard",
"Overview":"Vue d'ensemble",
"PDF preview":"Aper\u00e7u PDF",
"PDF unavailable":"PDF indisponible",
"PDFs downloaded":"PDF t\u00e9l\u00e9charg\u00e9s",
"Page break":"Saut de page",
"Pages read in VanoLib":"Pages lues dans VanoLib",
"Pages read this week":"Pages lues cette semaine",
"Pages read \u00b7 last 7 days":"Pages lues \u00b7 7 derniers jours",
"Paid":"Pay\u00e9",
"Paper":"Article",
"Paper not found in VanoLib":"Article introuvable dans VanoLib",
"Paste .bib content or choose a file":"Collez le contenu .bib ou choisissez un fichier",
"Paste a section, a proof, a LaTeX excerpt, notes\u2026":"Collez une section, une preuve, un extrait LaTeX, des notes\u2026",
"Paused":"En pause",
"Personal":"Personnel",
"Philosophy":"Philosophie",
"Physics":"Physique",
"Plan day":"Planifier la journ\u00e9e",
"Plan next week\u2019s 3 priorities":"Planifier les 3 priorit\u00e9s de la semaine prochaine",
"Planned":"Pr\u00e9vu",
"Planned time, actual focus, meetings, completions and routines. Completion history is recorded precisely from v5 onward.":"Temps pr\u00e9vu, concentration r\u00e9elle, r\u00e9unions, t\u00e2ches termin\u00e9es et routines. L'historique est enregistr\u00e9 pr\u00e9cis\u00e9ment depuis la v5.",
"Planned work day":"Journ\u00e9e de travail pr\u00e9vue",
"Planner rebuilt today\u2019s plan":"Le Planner a refait le plan du jour",
"Planner saved \u2014 click Re-plan to apply today":"Planner enregistr\u00e9 \u2014 cliquez sur Re-planifier pour l'appliquer aujourd'hui",
"Planner settings":"R\u00e9glages du Planner",
"Prepare questions for supervisor meeting":"Pr\u00e9parer les questions pour la r\u00e9union de direction",
"Preparing PDF\u2026":"Pr\u00e9paration du PDF\u2026",
"Primary color adapts automatically to the active theme.":"La couleur principale s'adapte automatiquement au th\u00e8me actif.",
"Priorities next week:":"Priorit\u00e9s de la semaine prochaine :",
"Priority":"Priorit\u00e9",
"Priority Matrix":"Matrice des priorit\u00e9s",
"Probability & Statistics":"Probabilit\u00e9s et statistiques",
"Productivity Analytics":"Analyse de productivit\u00e9",
"Progress Overview":"Vue d'ensemble des progr\u00e8s",
"Project":"Projet",
"Project Progress":"Avancement des projets",
"Project deleted":"Projet supprim\u00e9",
"Project:":"Projet :",
"Projects":"Projets",
"Projects as bars (start \u2192 deadline, filled by % done) \u00b7 \u25c6 milestones \u00b7 \u25cf task deadlines. Click anything to edit.":"Projets en barres (d\u00e9but \u2192 \u00e9ch\u00e9ance, remplies selon le % termin\u00e9) \u00b7 \u25c6 jalons \u00b7 \u25cf \u00e9ch\u00e9ances des t\u00e2ches. Cliquez sur un \u00e9l\u00e9ment pour le modifier.",
"Proof":"Preuve",
"Proof sketch / notes":"Esquisse de preuve / notes",
"Proved":"D\u00e9montr\u00e9",
"Querying arXiv\u2026":"Interrogation d'arXiv\u2026",
"Queue empty \u2014 star papers with \u2606.":"File vide \u2014 marquez des articles avec \u2606.",
"Queued":"En file",
"Quick / delegate":"Rapide / d\u00e9l\u00e9guer",
"Quick Notes":"Notes rapides",
"Quick Themes":"Th\u00e8mes rapides",
"Quick actions":"Actions rapides",
"Quick capture":"Saisie rapide",
"Quick capture to Inbox":"Saisie rapide vers la bo\u00eete de r\u00e9ception",
"Quote":"Citation",
"Quote deleted":"Citation supprim\u00e9e",
"Quote hidden \u2014 it will not be shown again":"Citation masqu\u00e9e \u2014 elle ne sera plus affich\u00e9e",
"Quote saved":"Citation enregistr\u00e9e",
"Quote shown again":"Citation affich\u00e9e de nouveau",
"Quoted text":"Texte cit\u00e9",
"Quotes":"Citations",
"Quotes \u2014 manage / collect":"Citations \u2014 g\u00e9rer / collecter",
"Radar failed":"\u00c9chec du radar",
"Radar settings":"R\u00e9glages du radar",
"Radar settings saved":"R\u00e9glages du radar enregistr\u00e9s",
"Rating":"Note",
"Re-plan today":"Re-planifier aujourd'hui",
"Read":"Lu",
"Read again":"Relire",
"Read date":"Date de lecture",
"Read from:":"Lu \u00e0 partir de :",
"Read in VanoLib":"Lire dans VanoLib",
"Read:":"Lu :",
"Reading":"Lecture",
"Reading (no full text)":"Lecture (sans texte int\u00e9gral)",
"Reading failed":"\u00c9chec de la lecture",
"Reading in VanoLib":"Lecture dans VanoLib",
"Reading notes":"Notes de lecture",
"Reading now":"En lecture",
"Reading task created":"T\u00e2che de lecture cr\u00e9\u00e9e",
"Reading\u2026":"Lecture\u2026",
"Ready to focus":"Pr\u00eat \u00e0 se concentrer",
"Ready to review":"Pr\u00eat \u00e0 relire",
"Recent Wins":"R\u00e9ussites r\u00e9centes",
"Reference":"R\u00e9f\u00e9rence",
"Reference added":"R\u00e9f\u00e9rence ajout\u00e9e",
"Reference deleted":"R\u00e9f\u00e9rence supprim\u00e9e",
"Reference updated":"R\u00e9f\u00e9rence mise \u00e0 jour",
"References":"R\u00e9f\u00e9rences",
"Reflection \u2014 what worked, what didn\u2019t?":"R\u00e9flexion \u2014 qu'est-ce qui a march\u00e9, qu'est-ce qui n'a pas march\u00e9 ?",
"Reflection:":"R\u00e9flexion :",
"Relevant to me":"Pertinents pour moi",
"Reload":"Recharger",
"Remaining":"Restant",
"Remark":"Remarque",
"Remind before meetings":"Rappel avant les r\u00e9unions",
"Removed":"Retir\u00e9",
"Removed from To Read":"Retir\u00e9 de \u00ab \u00c0 lire \u00bb",
"Removed from favourites":"Retir\u00e9e des favoris",
"Renderer on this Mac:":"Rendu sur ce Mac :",
"Rendering the video\u2026":"Rendu de la vid\u00e9o\u2026",
"Repeat until":"R\u00e9p\u00e9ter jusqu'au",
"Repeats":"R\u00e9p\u00e9tition",
"Report":"Rapport",
"Reports":"Rapports",
"Request a research-questions report":"Demander un rapport de questions de recherche",
"Research":"Recherche",
"Research profile saved":"Profil de recherche enregistr\u00e9",
"Result":"R\u00e9sultat",
"Result added":"R\u00e9sultat ajout\u00e9",
"Result deleted":"R\u00e9sultat supprim\u00e9",
"Result updated":"R\u00e9sultat mis \u00e0 jour",
"Results":"R\u00e9sultats",
"Results Bank":"Banque de r\u00e9sultats",
"Retry":"R\u00e9essayer",
"Review":"Relecture",
"Review overdue tasks (":"Revoir les t\u00e2ches en retard (",
"Reuse request":"R\u00e9utiliser la demande",
"Routine completions":"Routines accomplies",
"Routine done today:":"Routine faite aujourd'hui :",
"Run now":"Lancer maintenant",
"Run this file in a terminal:":"Lancez ce fichier dans un terminal :",
"Running on":"Fonctionne sur",
"Running \u00b7 models:":"En marche \u00b7 mod\u00e8les :",
"Safe cleanup creates a database backup first. Duplicate-looking tasks are reported, not auto-deleted.":"Le nettoyage s\u00fbr cr\u00e9e d'abord une sauvegarde de la base. Les t\u00e2ches qui semblent en double sont signal\u00e9es, pas supprim\u00e9es.",
"Same field":"M\u00eame domaine",
"Sat":"Sam",
"Saturday":"Samedi",
"Save":"Enregistrer",
"Save my corrections":"Enregistrer mes corrections",
"Save profile":"Enregistrer le profil",
"Saved":"Enregistr\u00e9",
"Saved + reading task created":"Enregistr\u00e9 + t\u00e2che de lecture cr\u00e9\u00e9e",
"Saved to Goals \u2192 Journal":"Enregistr\u00e9 dans Objectifs \u2192 Journal",
"Saved to bibliography":"Enregistr\u00e9 dans la bibliographie",
"Saved \u2014 repeats weekly":"Enregistr\u00e9 \u2014 se r\u00e9p\u00e8te chaque semaine",
"Savings":"\u00c9pargne",
"Schedule":"Planifier",
"Schedule tasks with a planned day + estimate to use workload planning.":"Planifiez les t\u00e2ches avec un jour pr\u00e9vu + une estimation pour utiliser la planification de charge.",
"Search":"Rechercher",
"Search a concept \u2014 e.g. moduli stack, lie groupoid, poisson structure\u2026":"Rechercher un concept \u2014 par ex. moduli stack, lie groupoid, poisson structure\u2026",
"Search a quote or an author\u2026":"Rechercher une citation ou un auteur\u2026",
"Search anything...":"Rechercher\u2026",
"Searching\u2026":"Recherche\u2026",
"Section heading":"Titre de section",
"Sections found in the full text":"Sections trouv\u00e9es dans le texte int\u00e9gral",
"Select a note on the left, or create one with a category's + button (or + New above)":"Choisissez une note \u00e0 gauche, ou cr\u00e9ez-en une avec le bouton + d'une cat\u00e9gorie (ou + Nouveau en haut)",
"Select the text and copy it (Cmd-C)":"S\u00e9lectionnez le texte et copiez-le (Cmd-C)",
"Seminar":"S\u00e9minaire",
"Seminar Talk":"Expos\u00e9 de s\u00e9minaire",
"Sent \u2014 results within the hour (macOS notification)":"Envoy\u00e9 \u2014 r\u00e9sultats dans l'heure (notification macOS)",
"Sepia":"S\u00e9pia",
"Session complete \u2014 saved to your analytics.":"Session termin\u00e9e \u2014 enregistr\u00e9e dans vos analyses.",
"Session finished \u2014 time saved.":"Session termin\u00e9e \u2014 temps enregistr\u00e9.",
"Settings":"R\u00e9glages",
"Shopping":"Achats",
"Short name":"Nom court",
"Shortcuts & Productivity":"Raccourcis et productivit\u00e9",
"Shown on the dashboard, changing at each visit (\u21bb to change now). \u2605 favourites appear more often. New quotes are collected online every week from Wikiquote (mathematicians, scientists, philosophers) and ZenQuotes.":"Affich\u00e9es sur le tableau de bord, elles changent \u00e0 chaque visite (\u21bb pour changer maintenant). Les \u2605 favorites apparaissent plus souvent. De nouvelles citations sont collect\u00e9es chaque semaine sur Wikiquote (math\u00e9maticiens, scientifiques, philosophes) et ZenQuotes.",
"Skimmed":"Survol\u00e9",
"Smooth transitions \u00b7 hover effects \u00b7 micro-animations":"Transitions douces \u00b7 effets de survol \u00b7 micro-animations",
"Source reference key(s), if from the literature":"Cl\u00e9(s) de r\u00e9f\u00e9rence de la source, si c'est tir\u00e9 de la litt\u00e9rature",
"Sources and rules (optional): where to look, what to check, what to avoid":"Sources et r\u00e8gles (facultatif) : o\u00f9 chercher, quoi v\u00e9rifier, quoi \u00e9viter",
"Spent":"D\u00e9pens\u00e9",
"Start":"D\u00e9but",
"Start Focus / Zen session":"D\u00e9marrer une session Focus / Zen",
"Start a timed session":"D\u00e9marrer une session chronom\u00e9tr\u00e9e",
"Start focus":"D\u00e9marrer la concentration",
"Start time":"Heure de d\u00e9but",
"Start timer":"D\u00e9marrer le minuteur",
"Started":"D\u00e9marr\u00e9",
"Starting":"D\u00e9marrage",
"Starting\u2026":"D\u00e9marrage\u2026",
"Statement (LaTeX allowed)":"\u00c9nonc\u00e9 (LaTeX permis)",
"Status":"Statut",
"Still offline \u2014 changes are saved on this device":"Toujours hors ligne \u2014 les modifications sont enregistr\u00e9es sur cet appareil",
"Stop timer":"Arr\u00eater le minuteur",
"Strikethrough":"Barr\u00e9",
"Structure of the paper":"Structure de l'article",
"Subheading":"Sous-titre",
"Subscript":"Indice",
"Subscriptions":"Abonnements",
"Subsection heading":"Titre de sous-section",
"Subsubsection heading":"Titre de sous-sous-section",
"Subtasks \u2014 one per line, \"[x] done\" / \"[ ] todo\"":"Sous-t\u00e2ches \u2014 une par ligne, \u00ab [x] fait \u00bb / \u00ab [ ] \u00e0 faire \u00bb",
"Summary":"R\u00e9sum\u00e9",
"Sun":"Dim",
"Sunday":"Dimanche",
"Superscript":"Exposant",
"Switch between LaTeX source and a Word-style rich text editor":"Basculer entre la source LaTeX et un \u00e9diteur de texte enrichi de type Word",
"Switch theme":"Changer de th\u00e8me",
"Switch theme (Aurora \u00b7 Automne \u00b7 Cyber)":"Changer de th\u00e8me (Aurora \u00b7 Automne \u00b7 Cyber)",
"Sync Google Calendar":"Synchroniser Google Agenda",
"Synced":"Synchronis\u00e9",
"Syncing arXiv\u2026":"Synchronisation d'arXiv\u2026",
"Syncing\u2026":"Synchronisation\u2026",
"Table":"Tableau",
"Tags (comma separated)":"\u00c9tiquettes (s\u00e9par\u00e9es par des virgules)",
"Tags (comma-separated)":"\u00c9tiquettes (s\u00e9par\u00e9es par des virgules)",
"Tap to retry sync":"Touchez pour relancer la synchronisation",
"Target date":"Date vis\u00e9e",
"Task":"T\u00e2che",
"Task created":"T\u00e2che cr\u00e9\u00e9e",
"Task created:":"T\u00e2che cr\u00e9\u00e9e :",
"Task deleted":"T\u00e2che supprim\u00e9e",
"Task log":"Journal des t\u00e2ches",
"Task sent to":"T\u00e2che envoy\u00e9e \u00e0",
"Task updated":"T\u00e2che mise \u00e0 jour",
"Tasks":"T\u00e2ches",
"Tasks Completed":"T\u00e2ches termin\u00e9es",
"Tasks Today":"T\u00e2ches du jour",
"Tasks completed this month":"T\u00e2ches termin\u00e9es ce mois-ci",
"Tasks done / planned":"T\u00e2ches faites / pr\u00e9vues",
"Teaching":"Enseignement",
"Team Meeting":"R\u00e9union d'\u00e9quipe",
"Test notification sent":"Notification de test envoy\u00e9e",
"Testing\u2026":"Test\u2026",
"Text color":"Couleur du texte",
"The PDF of the selected report will appear here.":"Le PDF du rapport choisi appara\u00eetra ici.",
"The full reading failed.":"La lecture compl\u00e8te a \u00e9chou\u00e9.",
"The full text could not be fetched (":"Le texte int\u00e9gral n'a pas pu \u00eatre r\u00e9cup\u00e9r\u00e9 (",
"The quote is empty":"La citation est vide",
"Theme":"Th\u00e8me",
"Themes \u00b7 Aurora / Automne / Cyber":"Th\u00e8mes \u00b7 Aurora / Automne / Cyber",
"Theorem":"Th\u00e9or\u00e8me",
"Thesis Defense":"Soutenance de th\u00e8se",
"Thinking\u2026":"R\u00e9flexion\u2026",
"This help":"Cette aide",
"This month":"Ce mois-ci",
"This week":"Cette semaine",
"Thu":"Jeu",
"Thursday":"Jeudi",
"Time: plan vs reality":"Temps : pr\u00e9vu et r\u00e9el",
"Timeline":"Chronologie",
"Tips":"Astuces",
"Title":"Titre",
"Title is required":"Le titre est obligatoire",
"To Do":"\u00c0 faire",
"To Read queue":"File \u00ab \u00c0 lire \u00bb",
"To check":"\u00c0 v\u00e9rifier",
"To read":"\u00c0 lire",
"To schedule":"\u00c0 planifier",
"Today":"Aujourd'hui",
"Today in science \u00b7":"Aujourd'hui en science \u00b7",
"Today planned":"Pr\u00e9vu aujourd'hui",
"Today's Tasks":"T\u00e2ches du jour",
"Toggle clean screen":"Basculer l'\u00e9cran \u00e9pur\u00e9",
"Top 3 priorities for next week":"3 priorit\u00e9s pour la semaine prochaine",
"Topology":"Topologie",
"Total: $":"Total : $",
"Track focus sessions and complete tasks to unlock personal insights.":"Suivez vos sessions de concentration et terminez des t\u00e2ches pour obtenir des analyses personnelles.",
"Tracked workload":"Charge suivie",
"Trend \u00b7 last":"Tendance \u00b7 derniers",
"Try again":"R\u00e9essayer",
"Tue":"Mar",
"Tuesday":"Mardi",
"Turn ideas into reality.":"Transformez les id\u00e9es en r\u00e9alit\u00e9.",
"Underline":"Souligner",
"Undo failed":"\u00c9chec de l'annulation",
"Universal search & commands":"Recherche universelle et commandes",
"Unknown":"Inconnu",
"Unsaved changes\u2026":"Modifications non enregistr\u00e9es\u2026",
"Unschedule":"D\u00e9planifier",
"Untitled":"Sans titre",
"Untitled entry":"Entr\u00e9e sans titre",
"Up to date":"\u00c0 jour",
"Upcoming":"\u00c0 venir",
"Upcoming Meetings":"R\u00e9unions \u00e0 venir",
"Updated":"Mis \u00e0 jour",
"Urgent, lower priority":"Urgent, priorit\u00e9 plus basse",
"Used":"Utilis\u00e9",
"Used to pick papers and to link every question to your work.":"Sert \u00e0 choisir les articles et \u00e0 relier chaque question \u00e0 votre travail.",
"VanoAtlas heartbeat missing":"Signal de VanoAtlas absent",
"VanoAtlas is fetching the full text\u2026":"VanoAtlas r\u00e9cup\u00e8re le texte int\u00e9gral\u2026",
"VanoAtlas looks for VanoLib in":"VanoAtlas cherche VanoLib dans",
"VanoAtlas server unreachable":"Serveur VanoAtlas injoignable",
"VanoLib To Read":"VanoLib \u00ab \u00c0 lire \u00bb",
"VanoLib unavailable":"VanoLib indisponible",
"Variance":"\u00c9cart",
"View all":"Tout voir",
"Wait for the compile to finish first":"Attendez d'abord la fin de la compilation",
"Waiting":"En attente",
"Waiting for the next hourly run (VanoAtlas must stay open).":"En attente de la prochaine ex\u00e9cution horaire (VanoAtlas doit rester ouvert).",
"Waiting for your approval":"En attente de votre accord",
"Wake up":"R\u00e9veil",
"Wallpaper follows the selected theme.":"Le fond d'\u00e9cran suit le th\u00e8me choisi.",
"Warm \u00b7 Natural \u00b7 Focused":"Chaleureux \u00b7 Naturel \u00b7 Concentr\u00e9",
"Watching":"Suivi",
"Website updates":"Mises \u00e0 jour du site",
"Wed":"Mer",
"Wednesday":"Mercredi",
"Week":"Semaine",
"Week of":"Semaine du",
"Weekly (e.g. a course)":"Hebdomadaire (par ex. un cours)",
"Weekly Review":"Bilan de la semaine",
"Weekly effort":"Effort hebdomadaire",
"Weekly meeting":"R\u00e9union hebdomadaire",
"Weekly review \u2014":"Bilan de la semaine \u2014",
"Welcome to VanoAtlas":"Bienvenue dans VanoAtlas",
"What it does, in one sentence (shown on its card)":"Ce qu'il fait, en une phrase (affich\u00e9 sur sa carte)",
"What's on your mind?":"Qu'avez-vous en t\u00eate ?",
"Why:":"Pourquoi :",
"Working":"En cours",
"Workload planner":"Planificateur de charge",
"Workshop":"Atelier",
"Write a request first":"\u00c9crivez d'abord une demande",
"Write in Markdown or LaTeX\u2026":"\u00c9crivez en Markdown ou en LaTeX\u2026",
"Writing":"R\u00e9daction",
"Writing style":"Style d'\u00e9criture",
"Year":"Ann\u00e9e",
"Yes":"Oui",
"Your papers found online":"Vos articles trouv\u00e9s en ligne",
"Your plan, no extra cost":"Votre forfait, sans co\u00fbt suppl\u00e9mentaire",
"Your reading list is empty":"Votre liste de lecture est vide",
"Your request":"Votre demande",
"Your research profile":"Votre profil de recherche",
"Your session is logged as actual task time.":"Votre session est enregistr\u00e9e comme temps r\u00e9el pass\u00e9 sur la t\u00e2che.",
"Zen Mode":"Mode Zen",
"Zoom in (+)":"Zoom avant (+)",
"Zoom out (\u2212)":"Zoom arri\u00e8re (\u2212)",
"and attach your files:":"et joignez vos fichiers :",
"and the introduction":"et l'introduction",
"anywhere, or \u2318K then \u21e7\u21b5.":"n'importe o\u00f9, ou \u2318K puis \u21e7\u21b5.",
"arXiv Radar":"Radar arXiv",
"arXiv categories (e.g. math.SG, math.AG)":"Cat\u00e9gories arXiv (par ex. math.SG, math.AG)",
"arXiv id":"Identifiant arXiv",
"arXiv papers":"articles arXiv",
"arXiv papers on your Mac \u00b7":"articles arXiv sur votre Mac \u00b7",
"around your meetings":"autour de vos r\u00e9unions",
"being read":"en cours de lecture",
"completed tasks":"t\u00e2ches termin\u00e9es",
"full readings":"lectures compl\u00e8tes",
"full text":"texte int\u00e9gral",
"highly relevant":"tr\u00e8s pertinent",
"in bibliography":"dans la bibliographie",
"new paper":"nouvel article",
"no dependencies":"aucune d\u00e9pendance",
"no link found":"aucun lien trouv\u00e9",
"no note":"aucune note",
"no time logged":"aucun temps enregistr\u00e9",
"open task":"t\u00e2che ouverte",
"overdue task":"t\u00e2che en retard",
"pages this week":"pages cette semaine",
"planned today":"pr\u00e9vu aujourd'hui",
"recent papers":"articles r\u00e9cents",
"tasks today":"t\u00e2ches aujourd'hui",
"to create it as a task":"pour en faire une t\u00e2che",
"\u2014 none \u2014":"\u2014 aucun \u2014",
"\u2192 Reference":"\u2192 R\u00e9f\u00e9rence",
"\u21bb Fetch now":"\u21bb R\u00e9cup\u00e9rer maintenant",
"\u23fa Listening\u2026":"\u23fa \u00c9coute\u2026",
"\u25cf offline":"\u25cf hors ligne",
"\u2605 Favourites":"\u2605 Favoris",
"\u2699 Keywords & authors":"\u2699 Mots-cl\u00e9s et auteurs",
"\u26a1 Auto-plan":"\u26a1 Planification auto",
"\u2713 Saved":"\u2713 Enregistr\u00e9",
"\u2715 Full map":"\u2715 Carte compl\u00e8te",
"\u2913 Export PDF":"\u2913 Exporter en PDF",
"\u2921 Exit":"\u2921 Quitter",
"\u2922 Large view":"\u2922 Grande vue",
"\ud83c\udfa4 Dictate":"\ud83c\udfa4 Dicter",
"\ud83d\udcd6 Read the whole paper":"\ud83d\udcd6 Lire l'article en entier",
"\ud83d\udcdd Word mode":"\ud83d\udcdd Mode Word",
"\ud83d\udd00 Shuffle variation":"\ud83d\udd00 Varier",
"\ud83d\udd78 Graph":"\ud83d\udd78 Graphe",
"\ud83d\uddbc Export image":"\ud83d\uddbc Exporter l'image",
"Ask Deep Research":"Demander \u00e0 Deep Research",
"Files":"Fichiers",
"Open map":"Ouvrir la carte",
"This week:":"Cette semaine :",
"Good morning":"Bonjour",
"Good afternoon":"Bon apr\u00e8s-midi",
"Good evening":"Bonsoir",
"Save key":"Enregistrer la cl\u00e9",
"Remove":"Retirer",
"remove":"retirer",
"Re-plan":"Re-planifier",
"Update now":"Mettre \u00e0 jour",
"Collect now":"Collecter maintenant",
"Generate now":"G\u00e9n\u00e9rer maintenant",
"Check now":"V\u00e9rifier maintenant",
"Fetch now":"R\u00e9cup\u00e9rer maintenant",
"Accept":"Accepter",
"Reject":"Refuser",
"Back":"Retour",
"Next":"Suivant",
"Previous":"Pr\u00e9c\u00e9dent",
"Add":"Ajouter",
"Send":"Envoyer",
"Submit":"Envoyer",
"Saving\u2026":"Enregistrement\u2026",
"Status:":"Statut :",
"Meeting":"R\u00e9union",
"Projects \u00b7":"Projets \u00b7",
"Collects mathematics, physics and science news every day and summarizes each item.":"Collecte chaque jour les actualit\u00e9s en math\u00e9matiques, physique et sciences, et r\u00e9sume chacune.",
"Keeps your website up to date: finds your new preprints (arXiv) and accepted / published papers (journals, Crossref) and updates research.html \u2014 new papers, journal + DOI, status, abstracts.":"Tient votre site \u00e0 jour : trouve vos nouvelles pr\u00e9publications (arXiv) et vos articles accept\u00e9s ou publi\u00e9s (revues, Crossref) et met \u00e0 jour research.html \u2014 nouveaux articles, revue + DOI, statut, r\u00e9sum\u00e9s.",
"Collects the new arXiv papers into your local VanoLib every Monday.":"Ajoute chaque lundi les nouveaux articles arXiv \u00e0 votre VanoLib locale.",
"Systematic literature search: reformulation, 5+ queries, targeted arXiv, abstracts, citation chasing, structured synthesis.":"Recherche bibliographique syst\u00e9matique : reformulation, 5 requ\u00eates ou plus, arXiv cibl\u00e9, r\u00e9sum\u00e9s, suivi des citations, synth\u00e8se structur\u00e9e.",
"Reads a paper like a Harvard/MIT researcher: open problems, future directions, project ideas, novelty-checked.":"Lit un article comme un chercheur de Harvard/MIT : probl\u00e8mes ouverts, pistes futures, id\u00e9es de projets, nouveaut\u00e9 v\u00e9rifi\u00e9e.",
"Sceptical-referee audit of a paper: real gaps (proof, hypotheses, literature, scope) turned into calibrated project pitches.":"Audit d'un article par un arbitre sceptique : vraies lacunes (preuve, hypoth\u00e8ses, litt\u00e9rature, port\u00e9e) transform\u00e9es en propositions de projets calibr\u00e9es.",
"Anti-hallucination check of references: fabricated or misattributed citations, reliability grade, priority / who proved it first.":"V\u00e9rification des r\u00e9f\u00e9rences : citations invent\u00e9es ou mal attribu\u00e9es, degr\u00e9 de fiabilit\u00e9, priorit\u00e9 / qui l'a d\u00e9montr\u00e9 en premier.",
"Checks and corrects papers, sections and proofs: full coverage, detailed computations, source audit, LaTeX report.":"V\u00e9rifie et corrige articles, sections et preuves : couverture compl\u00e8te, calculs d\u00e9taill\u00e9s, audit des sources, rapport LaTeX.",
"Simulated editorial board: domain reviewer, devil's advocate, journal fit \u2192 consensus, verdict, revision roadmap.":"Comit\u00e9 \u00e9ditorial simul\u00e9 : arbitre du domaine, avocat du diable, ad\u00e9quation \u00e0 la revue \u2192 consensus, verdict, plan de r\u00e9vision.",
"Scientific truth protocol: a demanding advisor who rejects errors, finds missing hypotheses, counterexamples, generalizations.":"Protocole de v\u00e9rit\u00e9 scientifique : un directeur exigeant qui rejette les erreurs, trouve les hypoth\u00e8ses manquantes, les contre-exemples, les g\u00e9n\u00e9ralisations.",
"Five independent advisors, anonymized peer review and an evidence-led verdict for consequential decisions.":"Cinq conseillers ind\u00e9pendants, une relecture anonyme crois\u00e9e et un verdict fond\u00e9 sur les faits pour les d\u00e9cisions importantes.",
"Ultra-concise answers and rewrites \u2014 no preamble, no filler \u2014 rigour unchanged.":"R\u00e9ponses et r\u00e9\u00e9critures ultra-concises \u2014 sans pr\u00e9ambule ni remplissage \u2014 m\u00eame rigueur.",
"Writes a structured referee report for a submitted paper: summary, assessment, major and minor comments.":"\u00c9crit un rapport d'arbitrage structur\u00e9 pour un article soumis : r\u00e9sum\u00e9, \u00e9valuation, commentaires majeurs et mineurs.",
"Wake up \u00b7 water, stretch":"R\u00e9veil \u00b7 eau, \u00e9tirements",
"Breakfast & get ready":"Petit-d\u00e9jeuner et pr\u00e9paration",
"Break \u00b7 walk, water":"Pause \u00b7 marche, eau",
"Lunch break":"Pause du midi",
"Wrap-up: mark what is done, plan tomorrow":"Bilan : cocher ce qui est fait, pr\u00e9parer demain",
"Prepare the meeting: progress since last week, open questions, next steps":"Pr\u00e9parer la r\u00e9union : progr\u00e8s depuis la semaine derni\u00e8re, questions ouvertes, prochaines \u00e9tapes",
"Write the meeting notes and the action items":"R\u00e9diger le compte rendu et les actions \u00e0 faire",
"errors today":"erreurs aujourd'hui",
"Run":"Lancer",
"Running":"En cours",
"Next run":"Prochaine ex\u00e9cution",
"Last run:":"Derni\u00e8re ex\u00e9cution :",
"Next:":"Prochaine :",
"NEW":"NOUVEAU",
"For you":"Pour vous",
"Following":"Abonnements",
"My drafts":"Mes brouillons",
"Manage":"G\u00e9rer",
"Art & Illustration":"Art et illustration",
"Literature":"Litt\u00e9rature",
"Technology":"Technologie",
"\u21bb Refresh":"\u21bb Actualiser",
"Opening the post\u2026":"Ouverture du post\u2026",
"\u2190 Back to the feed":"\u2190 Retour au fil",
"\u270d Write a post about this":"\u270d \u00c9crire un post \u00e0 ce sujet",
"This post has no text (podcast or video).":"Ce post n'a pas de texte (podcast ou vid\u00e9o).",
"What do you want to say about this post? e.g. a reaction, a mathematical point of view, a disagreement\u2026":"Que voulez-vous dire sur ce post ? ex. une r\u00e9action, un point de vue math\u00e9matique, un d\u00e9saccord\u2026",
"\u270d Write the draft (Full)":"\u270d \u00c9crire le brouillon (Complet)",
"Quick draft (Local)":"Brouillon rapide (Local)",
"Full = complete draft with sources, ready within the hour \u00b7 Local = immediate, shorter.":"Complet = brouillon complet avec sources, pr\u00eat dans l'heure \u00b7 Local = imm\u00e9diat, plus court.",
"No draft yet.":"Aucun brouillon pour l'instant.",
"Draft requested \u2014 it will appear here within the hour":"Brouillon demand\u00e9 \u2014 il appara\u00eetra ici dans l'heure",
"Writing the draft now\u2026":"R\u00e9daction du brouillon\u2026",
"Say what the post is about":"Dites de quoi parle le post",
"Saved \u2014 reading the new topics in the background":"Enregistr\u00e9 \u2014 lecture des nouveaux th\u00e8mes en arri\u00e8re-plan",
"Adding\u2026":"Ajout\u2026",
"Topics in your feed":"Th\u00e8mes de votre fil",
"Save topics":"Enregistrer les th\u00e8mes",
"The most read publications of each topic are followed automatically.":"Les publications les plus lues de chaque th\u00e8me sont suivies automatiquement.",
"+ Follow":"+ Suivre",
"Unfollow":"Ne plus suivre",
"Themes":"Th\u00e8mes",
"Ivory":"Ivoire",
"Midnight":"Minuit",
"Ocean":"Oc\u00e9an",
"Forest":"For\u00eat",
"Calm \u00b7 Editorial \u00b7 Clear":"Calme \u00b7 \u00c9ditorial \u00b7 Clair",
"Dark \u00b7 Refined \u00b7 Quiet":"Sombre \u00b7 Raffin\u00e9 \u00b7 Silencieux",
"Deep \u00b7 Fluid \u00b7 Serene":"Profond \u00b7 Fluide \u00b7 Serein",
"Soft \u00b7 Light \u00b7 Gentle":"Doux \u00b7 Lumineux \u00b7 D\u00e9licat",
"Cool \u00b7 Clean \u00b7 Precise":"Frais \u00b7 Net \u00b7 Pr\u00e9cis",
"Grounded \u00b7 Natural \u00b7 Calm":"Ancr\u00e9 \u00b7 Naturel \u00b7 Calme",
"University":"Universit\u00e9",
"Connect your inboxes":"Connecter vos bo\u00eetes courriel",
"Unread":"Non lus",
"To reply":"\u00c0 r\u00e9pondre",
"Newsletters":"Infolettres",
"Newsletter":"Infolettre",
"No message here for the last 10 days.":"Aucun message ici depuis 10 jours.",
"(no subject)":"(sans objet)",
"Yesterday":"Hier",
"Opening the message\u2026":"Ouverture du message\u2026",
"Choose a message on the left.":"Choisissez un message \u00e0 gauche.",
"Mark as read":"Marquer comme lu",
"Mark as unread":"Marquer comme non lu",
"\u2726 Summarize":"\u2726 R\u00e9sumer",
"\u2192 Task":"\u2192 T\u00e2che",
"Reply":"R\u00e9pondre",
"Your reply\u2026":"Votre r\u00e9ponse\u2026",
"\u270d Draft the reply":"\u270d R\u00e9diger la r\u00e9ponse",
"What should the reply say? e.g. accept, propose Thursday 2 pm, ask for the file\u2026 (optional)":"Que doit dire la r\u00e9ponse ? ex. accepter, proposer jeudi 14 h, demander le fichier\u2026 (facultatif)",
"Summarizing\u2026":"R\u00e9sum\u00e9 en cours\u2026",
"Writing\u2026":"R\u00e9daction\u2026",
"Task created with its deadline":"T\u00e2che cr\u00e9\u00e9e avec son \u00e9ch\u00e9ance",
"Loading your courses\u2026":"Chargement de vos cours\u2026",
"Coming up \u2014 courses and deadlines":"\u00c0 venir \u2014 cours et \u00e9ch\u00e9ances",
"Quick links":"Liens rapides",
"Calendars (iCal links)":"Calendriers (liens iCal)",
"All day":"Toute la journ\u00e9e",
"Nothing coming up.":"Rien \u00e0 venir.",
"Calendar link (https://\u2026 or webcal://\u2026)":"Lien du calendrier (https://\u2026 ou webcal://\u2026)",
"+ Add the calendar":"+ Ajouter le calendrier",
"Calendar added":"Calendrier ajout\u00e9",
"Open \u2197":"Ouvrir \u2197",
"No message here.":"Aucun message ici.",
"full texts":"textes complets",
"PDFs in VanoLib":"PDF dans VanoLib",
"waiting":"en attente",
"next try":"prochain essai",
"Start requested":"D\u00e9marrage demand\u00e9",
"every hour, day and night":"toutes les heures, jour et nuit",
"as soon as the day's papers are in":"d\u00e8s que les articles du jour sont arriv\u00e9s",
"weekly check-up":"bilan hebdomadaire",
"Read the check-up":"Lire le bilan",
"first weekly check-up on Saturday evening":"premier bilan hebdomadaire samedi soir",
"every Saturday evening":"chaque samedi soir",
"Not connected.":"Non connect\u00e9.",
"questions this month":"questions ce mois-ci",
"Reconnect":"Reconnecter",
"Test":"Tester",
"Key saved":"Cl\u00e9 enregistr\u00e9e",
"this month":"ce mois-ci",
"searches":"recherches",
"Monthly limit $":"Limite mensuelle $",
"+ New question":"+ Nouvelle question",
"Fast":"Rapide",
"Deep":"Approfondi",
"Independent approaches, then the final answer":"Approches ind\u00e9pendantes, puis la r\u00e9ponse finale",
"Approaches, cross-review, final answer and final check":"Approches, relecture crois\u00e9e, r\u00e9ponse finale et v\u00e9rification finale",
"Download the conversation (Markdown)":"T\u00e9l\u00e9charger la conversation (Markdown)",
"sign-in needed":"connexion requise",
"not connected":"non connect\u00e9",
"your plan":"votre abonnement",
"no key":"pas de cl\u00e9",
"monthly limit reached":"limite mensuelle atteinte",
"Click to leave out of this question":"Cliquez pour l'exclure de cette question",
"Default of your plan":"Par d\u00e9faut de votre abonnement",
"Most capable":"Le plus puissant",
"Faster":"Plus rapide",
"Check the connections again":"V\u00e9rifier \u00e0 nouveau les connexions",
"At least one member must stay":"Au moins un membre doit rester",
"A Terminal window opened: sign in there once, then come back":"Une fen\u00eatre Terminal s'est ouverte : connectez-vous une fois, puis revenez ici",
"Enter to send \u00b7 Shift+Enter for a new line \u00b7 Fast \u2248 2\u20134 min, Deep \u2248 5\u201310 min":"Entr\u00e9e pour envoyer \u00b7 Maj+Entr\u00e9e pour une nouvelle ligne \u00b7 Rapide \u2248 2\u20134 min, Approfondi \u2248 5\u201310 min",
"Connect at least one member first":"Connectez d'abord au moins un membre",
"Stopping after the current step":"Arr\u00eat apr\u00e8s l'\u00e9tape en cours",
"Working\u2026":"Au travail\u2026",
"1 \u00b7 Independent approaches":"1 \u00b7 Approches ind\u00e9pendantes",
"2 \u00b7 Cross-review":"2 \u00b7 Relecture crois\u00e9e",
"2 \u00b7 Final answer":"2 \u00b7 R\u00e9ponse finale",
"3 \u00b7 Final answer":"3 \u00b7 R\u00e9ponse finale",
"3 \u00b7 Final check":"3 \u00b7 V\u00e9rification finale",
"4 \u00b7 Final check":"4 \u00b7 V\u00e9rification finale",
"Written by":"R\u00e9dig\u00e9 par",
"Stop":"Arr\u00eater",
"Stopped.":"Arr\u00eat\u00e9.",
"Interrupted (the app restarted during the work).":"Interrompu (l'application a red\u00e9marr\u00e9 pendant le travail).",
"Ask again":"Redemander",
"Three members, one question. Nobody works alone: every answer is read and checked by the others.":"Trois membres, une question. Personne ne travaille seul : chaque r\u00e9ponse est lue et v\u00e9rifi\u00e9e par les autres.",
"1 \u00b7 Approaches":"1 \u00b7 Approches",
"(Deep)":"(Approfondi)",
"Each checks the others: errors, gaps, references verified.":"Chacun v\u00e9rifie les autres : erreurs, lacunes, r\u00e9f\u00e9rences v\u00e9rifi\u00e9es.",
"One complete answer, with agreements, disagreements and sources.":"Une r\u00e9ponse compl\u00e8te, avec accords, d\u00e9saccords et sources.",
"web search only":"la recherche web seulement",
"Guard on":"Garde active",
"Guard missing":"Garde absente",
"blocked":"bloqu\u00e9es",
"alerts to":"alertes \u00e0",
"Send a test alert":"Envoyer une alerte test",
"Sending\u2026":"Envoi\u2026",
"turns your readings into projects":"transforme vos lectures en projets",
"Turns your readings into research projects: detailed description, questions to explore, milestones and planned tasks, written in Projects.":"Transforme vos lectures en projets de recherche : description d\u00e9taill\u00e9e, questions \u00e0 explorer, jalons et t\u00e2ches planifi\u00e9es, \u00e9crits dans Projets.",
"new project(s)":"nouveau(x) projet(s)",
"task(s)":"t\u00e2che(s)",
"project(s) extended":"projet(s) compl\u00e9t\u00e9(s)",
"nothing new to plan":"rien de nouveau \u00e0 planifier",
"Never run yet":"Jamais lanc\u00e9",
"Daily":"Chaque jour",
"Plan from my readings now":"Planifier depuis mes lectures maintenant"
};
const VA_FRAG=[
[
"Shown on the dashboard, changing at each visit (\u21bb to change now). \u2605 favourites appear more often. New quotes are collected online every week from Wikiquote (mathematicians, scientists, philosophers) and ZenQuotes.",
"Affich\u00e9es sur le tableau de bord, elles changent \u00e0 chaque visite (\u21bb pour changer maintenant). Les \u2605 favorites apparaissent plus souvent. De nouvelles citations sont collect\u00e9es chaque semaine sur Wikiquote (math\u00e9maticiens, scientifiques, philosophes) et ZenQuotes."
],
[
"Keeps your website up to date: finds your new preprints (arXiv) and accepted / published papers (journals, Crossref) and updates research.html \u2014 new papers, journal + DOI, status, abstracts.",
"Tient votre site \u00e0 jour : trouve vos nouvelles pr\u00e9publications (arXiv) et vos articles accept\u00e9s ou publi\u00e9s (revues, Crossref) et met \u00e0 jour research.html \u2014 nouveaux articles, revue + DOI, statut, r\u00e9sum\u00e9s."
],
[
"Free key from aistudio.google.com/apikey (no credit card). Best quality among free options. Note: on the free tier Google may use the text to improve its models.",
"Cl\u00e9 gratuite sur aistudio.google.com/apikey (sans carte bancaire). Meilleure qualit\u00e9 parmi les options gratuites. Remarque : avec l'offre gratuite, Google peut utiliser le texte pour am\u00e9liorer ses mod\u00e8les."
],
[
"Every definition, lemma and theorem with its status and dependencies. A result is only as solid as what it relies on \u2014 weak links are flagged automatically.",
"Chaque d\u00e9finition, lemme et th\u00e9or\u00e8me avec son statut et ses d\u00e9pendances. Un r\u00e9sultat n'est pas plus solide que ce sur quoi il repose \u2014 les maillons faibles sont signal\u00e9s automatiquement."
],
[
"Collected every morning at 7:10 from Quanta, Tao, Kalai, n-Category Caf\u00e9, Not Even Wrong, Phys.org, Science News, Nature, New Scientist and Google News.",
"Collect\u00e9es chaque matin \u00e0 7 h 10 depuis Quanta, Tao, Kalai, n-Category Caf\u00e9, Not Even Wrong, Phys.org, Science News, Nature, New Scientist et Google News."
],
[
"Turns your readings into research projects: detailed description, questions to explore, milestones and planned tasks, written in Projects.",
"Transforme vos lectures en projets de recherche : description d\u00e9taill\u00e9e, questions \u00e0 explorer, jalons et t\u00e2ches planifi\u00e9es, \u00e9crits dans Projets."
],
[
"Anti-hallucination check of references: fabricated or misattributed citations, reliability grade, priority / who proved it first.",
"V\u00e9rification des r\u00e9f\u00e9rences : citations invent\u00e9es ou mal attribu\u00e9es, degr\u00e9 de fiabilit\u00e9, priorit\u00e9 / qui l'a d\u00e9montr\u00e9 en premier."
],
[
"Free key from aistudio.google.com/apikey (no credit card). Note: on the free tier Google may use the text to improve its models.",
"Cl\u00e9 gratuite sur aistudio.google.com/apikey (sans carte bancaire). Remarque : avec l'offre gratuite, Google peut utiliser le texte pour am\u00e9liorer ses mod\u00e8les."
],
[
"Scientific truth protocol: a demanding advisor who rejects errors, finds missing hypotheses, counterexamples, generalizations.",
"Protocole de v\u00e9rit\u00e9 scientifique : un directeur exigeant qui rejette les erreurs, trouve les hypoth\u00e8ses manquantes, les contre-exemples, les g\u00e9n\u00e9ralisations."
],
[
"Sceptical-referee audit of a paper: real gaps (proof, hypotheses, literature, scope) turned into calibrated project pitches.",
"Audit d'un article par un arbitre sceptique : vraies lacunes (preuve, hypoth\u00e8ses, litt\u00e9rature, port\u00e9e) transform\u00e9es en propositions de projets calibr\u00e9es."
],
[
"Systematic literature search: reformulation, 5+ queries, targeted arXiv, abstracts, citation chasing, structured synthesis.",
"Recherche bibliographique syst\u00e9matique : reformulation, 5 requ\u00eates ou plus, arXiv cibl\u00e9, r\u00e9sum\u00e9s, suivi des citations, synth\u00e8se structur\u00e9e."
],
[
"Planned time, actual focus, meetings, completions and routines. Completion history is recorded precisely from v5 onward.",
"Temps pr\u00e9vu, concentration r\u00e9elle, r\u00e9unions, t\u00e2ches termin\u00e9es et routines. L'historique est enregistr\u00e9 pr\u00e9cis\u00e9ment depuis la v5."
],
[
"Checks and corrects papers, sections and proofs: full coverage, detailed computations, source audit, LaTeX report.",
"V\u00e9rifie et corrige articles, sections et preuves : couverture compl\u00e8te, calculs d\u00e9taill\u00e9s, audit des sources, rapport LaTeX."
],
[
": keywords in title/abstract, followed authors, categories, freshness. Runs on your Mac via the public arXiv API.",
" : mots-cl\u00e9s dans le titre et le r\u00e9sum\u00e9, auteurs suivis, cat\u00e9gories, fra\u00eecheur. Fonctionne sur votre Mac avec l'API publique d'arXiv."
],
[
"No papers yet. Set your keywords / authors, then \u201cFetch now\u201d. VanoAtlas also checks automatically every 12 hours.",
"Aucun article pour l'instant. Indiquez vos mots-cl\u00e9s / auteurs, puis \u00ab Fetch now \u00bb. VanoAtlas v\u00e9rifie aussi automatiquement toutes les 12 heures."
],
[
"Simulated editorial board: domain reviewer, devil's advocate, journal fit \u2192 consensus, verdict, revision roadmap.",
"Comit\u00e9 \u00e9ditorial simul\u00e9 : arbitre du domaine, avocat du diable, ad\u00e9quation \u00e0 la revue \u2192 consensus, verdict, plan de r\u00e9vision."
],
[
"Projects as bars (start \u2192 deadline, filled by % done) \u00b7 \u25c6 milestones \u00b7 \u25cf task deadlines. Click anything to edit.",
"Projets en barres (d\u00e9but \u2192 \u00e9ch\u00e9ance, remplies selon le % termin\u00e9) \u00b7 \u25c6 jalons \u00b7 \u25cf \u00e9ch\u00e9ances des t\u00e2ches. Cliquez sur un \u00e9l\u00e9ment pour le modifier."
],
[
"Reads a paper like a Harvard/MIT researcher: open problems, future directions, project ideas, novelty-checked.",
"Lit un article comme un chercheur de Harvard/MIT : probl\u00e8mes ouverts, pistes futures, id\u00e9es de projets, nouveaut\u00e9 v\u00e9rifi\u00e9e."
],
[
". If you clicked \u201cDon\u2019t Allow\u201d before, open System Settings \u203a Privacy & Security \u203a Automation and turn on ",
". Si vous avez cliqu\u00e9 sur \u00ab Refuser \u00bb avant, ouvrez R\u00e9glages Syst\u00e8me \u203a Confidentialit\u00e9 et s\u00e9curit\u00e9 \u203a Automatisation et activez "
],
[
"Five independent advisors, anonymized peer review and an evidence-led verdict for consequential decisions.",
"Cinq conseillers ind\u00e9pendants, une relecture anonyme crois\u00e9e et un verdict fond\u00e9 sur les faits pour les d\u00e9cisions importantes."
],
[
"Writes a structured referee report for a submitted paper: summary, assessment, major and minor comments.",
"\u00c9crit un rapport d'arbitrage structur\u00e9 pour un article soumis : r\u00e9sum\u00e9, \u00e9valuation, commentaires majeurs et mineurs."
],
[
"What do you want to say about this post? e.g. a reaction, a mathematical point of view, a disagreement\u2026",
"Que voulez-vous dire sur ce post ? ex. une r\u00e9action, un point de vue math\u00e9matique, un d\u00e9saccord\u2026"
],
[
"No references yet \u2014 paste a DOI or arXiv id above, import a .bib, or save papers from the arXiv Radar.",
"Aucune r\u00e9f\u00e9rence \u2014 collez un DOI ou un identifiant arXiv ci-dessus, importez un .bib, ou enregistrez des articles depuis le radar arXiv."
],
[
"Safe cleanup creates a database backup first. Duplicate-looking tasks are reported, not auto-deleted.",
"Le nettoyage s\u00fbr cr\u00e9e d'abord une sauvegarde de la base. Les t\u00e2ches qui semblent en double sont signal\u00e9es, pas supprim\u00e9es."
],
[
"A private space for your goals, personal growth notes, and how you've been doing \u2014 just for you.",
"Un espace priv\u00e9 pour vos objectifs, vos notes de progression et votre \u00e9tat d'esprit \u2014 rien que pour vous."
],
[
"Three members, one question. Nobody works alone: every answer is read and checked by the others.",
"Trois membres, une question. Personne ne travaille seul : chaque r\u00e9ponse est lue et v\u00e9rifi\u00e9e par les autres."
],
[
"No results yet. Add your definitions, lemmas and theorems \u2014 then link them with \u201cDepends on\u201d.",
"Aucun r\u00e9sultat pour l'instant. Ajoutez vos d\u00e9finitions, lemmes et th\u00e9or\u00e8mes \u2014 puis reliez-les avec \u00ab D\u00e9pend de \u00bb."
],
[
"What should the reply say? e.g. accept, propose Thursday 2 pm, ask for the file\u2026 (optional)",
"Que doit dire la r\u00e9ponse ? ex. accepter, proposer jeudi 14 h, demander le fichier\u2026 (facultatif)"
],
[
"Click a concept in the graph to see the papers, their contributions and related concepts.",
"Cliquez sur un concept du graphe pour voir les articles, leurs contributions et les concepts li\u00e9s."
],
[
"Full = complete draft with sources, ready within the hour \u00b7 Local = immediate, shorter.",
"Complet = brouillon complet avec sources, pr\u00eat dans l'heure \u00b7 Local = imm\u00e9diat, plus court."
],
[
"Instructions: how it works, step by step \u2014 like explaining the job to a new assistant",
"Instructions : comment il travaille, \u00e9tape par \u00e9tape \u2014 comme si vous expliquiez le travail \u00e0 un nouvel assistant"
],
[
"Select a note on the left, or create one with a category's + button (or + New above)",
"Choisissez une note \u00e0 gauche, ou cr\u00e9ez-en une avec le bouton + d'une cat\u00e9gorie (ou + Nouveau en haut)"
],
[
"Collects mathematics, physics and science news every day and summarizes each item.",
"Collecte chaque jour les actualit\u00e9s en math\u00e9matiques, physique et sciences, et r\u00e9sume chacune."
],
[
"App-specific password \u2014 from appleid.apple.com, not your real Apple ID password",
"Mot de passe pour app \u2014 sur appleid.apple.com, pas votre vrai mot de passe Apple"
],
[
"Ultra-concise answers and rewrites \u2014 no preamble, no filler \u2014 rigour unchanged.",
"R\u00e9ponses et r\u00e9\u00e9critures ultra-concises \u2014 sans pr\u00e9ambule ni remplissage \u2014 m\u00eame rigueur."
],
[
"Add by DOI or arXiv id, import/export .bib for Overleaf \u00b7 Cite in notes with",
"Ajout par DOI ou identifiant arXiv, import/export .bib pour Overleaf \u00b7 Citer dans les notes avec"
],
[
"Enter to send \u00b7 Shift+Enter for a new line \u00b7 Fast \u2248 2\u20134 min, Deep \u2248 5\u201310 min",
"Entr\u00e9e pour envoyer \u00b7 Maj+Entr\u00e9e pour une nouvelle ligne \u00b7 Rapide \u2248 2\u20134 min, Approfondi \u2248 5\u201310 min"
],
[
"DOI (10.1007/\u2026) or arXiv id (2405.11821) or URL \u2014 Enter to fetch metadata",
"DOI (10.1007/\u2026), identifiant arXiv (2405.11821) ou URL \u2014 Entr\u00e9e pour r\u00e9cup\u00e9rer les m\u00e9tadonn\u00e9es"
],
[
"Sources and rules (optional): where to look, what to check, what to avoid",
"Sources et r\u00e8gles (facultatif) : o\u00f9 chercher, quoi v\u00e9rifier, quoi \u00e9viter"
],
[
"Prepare the meeting: progress since last week, open questions, next steps",
"Pr\u00e9parer la r\u00e9union : progr\u00e8s depuis la semaine derni\u00e8re, questions ouvertes, prochaines \u00e9tapes"
],
[
"full power (web, arXiv, your files, LaTeX), runs every hour on your plan",
"pleine puissance (web, arXiv, vos fichiers, LaTeX), ex\u00e9cut\u00e9 chaque heure avec votre forfait"
],
[
"Files on your Mac, one path per line (e.g. ~/Documents/paper/main.tex)",
"Fichiers sur votre Mac, un chemin par ligne (par ex. ~/Documents/article/main.tex)"
],
[
"Schedule tasks with a planned day + estimate to use workload planning.",
"Planifiez les t\u00e2ches avec un jour pr\u00e9vu + une estimation pour utiliser la planification de charge."
],
[
"Search a concept \u2014 e.g. moduli stack, lie groupoid, poisson structure\u2026",
"Rechercher un concept \u2014 par ex. moduli stack, lie groupoid, poisson structure\u2026"
],
[
"Track focus sessions and complete tasks to unlock personal insights.",
"Suivez vos sessions de concentration et terminez des t\u00e2ches pour obtenir des analyses personnelles."
],
[
"The most read publications of each topic are followed automatically.",
"Les publications les plus lues de chaque th\u00e8me sont suivies automatiquement."
],
[
"Collects the new arXiv papers into your local VanoLib every Monday.",
"Ajoute chaque lundi les nouveaux articles arXiv \u00e0 votre VanoLib locale."
],
[
"LaTeX editor with compilation and PDF preview \u2014 inside VanoAtlas.",
"\u00c9diteur LaTeX avec compilation et aper\u00e7u PDF \u2014 dans VanoAtlas."
],
[
"One complete answer, with agreements, disagreements and sources.",
"Une r\u00e9ponse compl\u00e8te, avec accords, d\u00e9saccords et sources."
],
[
"Font / math renderer unavailable offline \u2014 simplified preview.",
"Police / rendu math\u00e9matique indisponible hors ligne \u2014 aper\u00e7u simplifi\u00e9."
],
[
"Automatic check every 12 h (macOS notification if score \u2265 90)",
"V\u00e9rification automatique toutes les 12 h (notification macOS si score \u2265 90)"
],
[
"Switch between LaTeX source and a Word-style rich text editor",
"Basculer entre la source LaTeX et un \u00e9diteur de texte enrichi de type Word"
],
[
"Used to pick papers and to link every question to your work.",
"Sert \u00e0 choisir les articles et \u00e0 relier chaque question \u00e0 votre travail."
],
[
"A Terminal window opened: sign in there once, then come back",
"Une fen\u00eatre Terminal s'est ouverte : connectez-vous une fois, puis revenez ici"
],
[
"% of all tracked focus). Protect it for proofs and writing.",
"% de toute la concentration suivie). Prot\u00e9gez-le pour les preuves et la r\u00e9daction."
],
[
"Waiting for the next hourly run (VanoAtlas must stay open).",
"En attente de la prochaine ex\u00e9cution horaire (VanoAtlas doit rester ouvert)."
],
[
"Add estimated minutes to tasks to compare plan vs reality.",
"Ajoutez des minutes estim\u00e9es aux t\u00e2ches pour comparer le pr\u00e9vu et le r\u00e9el."
],
[
"Followed authors (comma-separated, e.g. Safronov, Calaque)",
"Auteurs suivis (s\u00e9par\u00e9s par des virgules, par ex. Safronov, Calaque)"
],
[
"Each checks the others: errors, gaps, references verified.",
"Chacun v\u00e9rifie les autres : erreurs, lacunes, r\u00e9f\u00e9rences v\u00e9rifi\u00e9es."
],
[
"No journal entries yet \u2014 write your first one with + New",
"Aucune entr\u00e9e de journal \u2014 \u00e9crivez la premi\u00e8re avec + Nouveau"
],
[
"Not in VanoLib yet \u2014 it will appear after the next sync",
"Pas encore dans VanoLib \u2014 il appara\u00eetra apr\u00e8s la prochaine synchronisation"
],
[
"Primary color adapts automatically to the active theme.",
"La couleur principale s'adapte automatiquement au th\u00e8me actif."
],
[
"Approaches, cross-review, final answer and final check",
"Approches, relecture crois\u00e9e, r\u00e9ponse finale et v\u00e9rification finale"
],
[
"Smooth transitions \u00b7 hover effects \u00b7 micro-animations",
"Transitions douces \u00b7 effets de survol \u00b7 micro-animations"
],
[
"Draft requested \u2014 it will appear here within the hour",
"Brouillon demand\u00e9 \u2014 il appara\u00eetra ici dans l'heure"
],
[
"Image export unavailable \u2014 try \"Export PDF\" instead.",
"Export d'image indisponible \u2014 essayez plut\u00f4t \u00ab Export PDF \u00bb."
],
[
"VanoAtlas reads the accounts already set up in the ",
"VanoAtlas lit les comptes d\u00e9j\u00e0 configur\u00e9s dans l'app "
],
[
"Capture everything in one line, triage later. Also:",
"Tout noter en une ligne, trier plus tard. Aussi :"
],
[
"Depends on (labels or BibTeX keys, comma-separated)",
"D\u00e9pend de (\u00e9tiquettes ou cl\u00e9s BibTeX, s\u00e9par\u00e9es par des virgules)"
],
[
"Notes, annotated PDFs and audio \u2014 inside VanoAtlas.",
"Notes, PDF annot\u00e9s et audio \u2014 dans VanoAtlas."
],
[
"Over capacity. Consider moving lower-priority work.",
"Capacit\u00e9 d\u00e9pass\u00e9e. Pensez \u00e0 d\u00e9placer le travail moins prioritaire."
],
[
"Sent \u2014 results within the hour (macOS notification)",
"Envoy\u00e9 \u2014 r\u00e9sultats dans l'heure (notification macOS)"
],
[
"No check-ins yet \u2014 log how you're doing with + New",
"Aucun bilan pour l'instant \u2014 notez comment vous allez avec + Nouveau"
],
[
"Collecting quotes online (Wikiquote + ZenQuotes)\u2026",
"Collecte de citations en ligne (Wikiquote + ZenQuotes)\u2026"
],
[
"Consistency turns small steps into real progress.",
"La r\u00e9gularit\u00e9 transforme les petits pas en vrais progr\u00e8s."
],
[
"Paste a section, a proof, a LaTeX excerpt, notes\u2026",
"Collez une section, une preuve, un extrait LaTeX, des notes\u2026"
],
[
"What it does, in one sentence (shown on its card)",
"Ce qu'il fait, en une phrase (affich\u00e9 sur sa carte)"
],
[
"Still offline \u2014 changes are saved on this device",
"Toujours hors ligne \u2014 les modifications sont enregistr\u00e9es sur cet appareil"
],
[
"Subtasks \u2014 one per line, \"[x] done\" / \"[ ] todo\"",
"Sous-t\u00e2ches \u2014 une par ligne, \u00ab [x] fait \u00bb / \u00ab [ ] \u00e0 faire \u00bb"
],
[
"The PDF of the selected report will appear here.",
"Le PDF du rapport choisi appara\u00eetra ici."
],
[
"Saved \u2014 reading the new topics in the background",
"Enregistr\u00e9 \u2014 lecture des nouveaux th\u00e8mes en arri\u00e8re-plan"
],
[
"Interrupted (the app restarted during the work).",
"Interrompu (l'application a red\u00e9marr\u00e9 pendant le travail)."
],
[
"Added to favourites \u2014 it will appear more often",
"Ajout\u00e9e aux favoris \u2014 elle appara\u00eetra plus souvent"
],
[
"Source reference key(s), if from the literature",
"Cl\u00e9(s) de r\u00e9f\u00e9rence de la source, si c'est tir\u00e9 de la litt\u00e9rature"
],
[
"(16 GB+). Your articles never leave your Mac.",
"(16 Go et plus). Vos articles ne quittent jamais votre Mac."
],
[
"Independent approaches, then the final answer",
"Approches ind\u00e9pendantes, puis la r\u00e9ponse finale"
],
[
"Custom BibTeX (leave empty to auto-generate)",
"BibTeX personnalis\u00e9 (laisser vide pour le g\u00e9n\u00e9rer)"
],
[
"No active projects \u2014 create one in Projects.",
"Aucun projet actif \u2014 cr\u00e9ez-en un dans Projets."
],
[
"Only papers related to your research profile",
"Seulement les articles li\u00e9s \u00e0 votre profil de recherche"
],
[
"Planner saved \u2014 click Re-plan to apply today",
"Planner enregistr\u00e9 \u2014 cliquez sur Re-planifier pour l'appliquer aujourd'hui"
],
[
"Write the meeting notes and the action items",
"R\u00e9diger le compte rendu et les actions \u00e0 faire"
],
[
"No news collected yet. Click \u201cCollect now\u201d.",
"Aucune actualit\u00e9 collect\u00e9e. Cliquez sur \u00ab Collect now \u00bb."
],
[
"Nothing to place \u2014 the day is full or empty",
"Rien \u00e0 placer \u2014 la journ\u00e9e est pleine ou vide"
],
[
"Session complete \u2014 saved to your analytics.",
"Session termin\u00e9e \u2014 enregistr\u00e9e dans vos analyses."
],
[
"Your session is logged as actual task time.",
"Votre session est enregistr\u00e9e comme temps r\u00e9el pass\u00e9 sur la t\u00e2che."
],
[
"Quote hidden \u2014 it will not be shown again",
"Citation masqu\u00e9e \u2014 elle ne sera plus affich\u00e9e"
],
[
"Wrap-up: mark what is done, plan tomorrow",
"Bilan : cocher ce qui est fait, pr\u00e9parer demain"
],
[
"This post has no text (podcast or video).",
"Ce post n'a pas de texte (podcast ou vid\u00e9o)."
],
[
"No items for this day \u2014 add your routine",
"Aucun \u00e9l\u00e9ment pour ce jour \u2014 ajoutez votre routine"
],
[
"No market brief yet. Click \u201cUpdate now\u201d.",
"Pas encore de point march\u00e9s. Cliquez sur \u00ab Update now \u00bb."
],
[
"Nothing scheduled \u2014 click + Event to add",
"Rien de pr\u00e9vu \u2014 cliquez sur + \u00c9v\u00e9nement pour ajouter"
],
[
"Prepare questions for supervisor meeting",
"Pr\u00e9parer les questions pour la r\u00e9union de direction"
],
[
"arXiv categories (e.g. math.SG, math.AG)",
"Cat\u00e9gories arXiv (par ex. math.SG, math.AG)"
],
[
"minutes/day across this calendar month.",
"minutes par jour sur ce mois."
],
[
"(will be downloaded at the first check)",
"(sera t\u00e9l\u00e9charg\u00e9 \u00e0 la premi\u00e8re v\u00e9rification)"
],
[
"Finish the focus session before closing",
"Terminez la session de concentration avant de fermer"
],
[
"Switch theme (Aurora \u00b7 Automne \u00b7 Cyber)",
"Changer de th\u00e8me (Aurora \u00b7 Automne \u00b7 Cyber)"
],
[
"Calendar link (https://\u2026 or webcal://\u2026)",
"Lien du calendrier (https://\u2026 ou webcal://\u2026)"
],
[
"Reflection \u2014 what worked, what didn\u2019t?",
"R\u00e9flexion \u2014 qu'est-ce qui a march\u00e9, qu'est-ce qui n'a pas march\u00e9 ?"
],
[
"minutes remain in an 8-hour capacity.",
"minutes restent sur une capacit\u00e9 de 8 heures."
],
[
"Wallpaper follows the selected theme.",
"Le fond d'\u00e9cran suit le th\u00e8me choisi."
],
[
"No message here for the last 10 days.",
"Aucun message ici depuis 10 jours."
],
[
"than estimated \u2014 pad your estimates.",
"que pr\u00e9vu \u2014 pr\u00e9voyez plus large."
],
[
"at the current pace, finished around",
"au rythme actuel, termin\u00e9 vers"
],
[
"% actual focus vs planned estimates.",
"% de concentration r\u00e9elle par rapport aux estimations."
],
[
"Every proof counts \u2014 log your wins \ud83c\udfc6",
"Chaque preuve compte \u2014 notez vos r\u00e9ussites \ud83c\udfc6"
],
[
"Keywords / phrases (comma-separated)",
"Mots-cl\u00e9s / expressions (s\u00e9par\u00e9s par des virgules)"
],
[
"No report yet. Click \u201cGenerate now\u201d.",
"Pas encore de rapport. Cliquez sur \u00ab Generate now \u00bb."
],
[
"The full text could not be fetched (",
"Le texte int\u00e9gral n'a pas pu \u00eatre r\u00e9cup\u00e9r\u00e9 ("
],
[
"VanoAtlas is fetching the full text\u2026",
"VanoAtlas r\u00e9cup\u00e8re le texte int\u00e9gral\u2026"
],
[
"Wait for the compile to finish first",
"Attendez d'abord la fin de la compilation"
],
[
"Download the conversation (Markdown)",
"T\u00e9l\u00e9charger la conversation (Markdown)"
],
[
"Discipline today, freedom tomorrow.",
"Discipline aujourd'hui, libert\u00e9 demain."
],
[
"Hide distractions and stay focused.",
"Masquez les distractions et restez concentr\u00e9."
],
[
"Links come from project membership,",
"Les liens viennent de l'appartenance aux projets,"
],
[
"No projects \u2014 create one with + New",
"Aucun projet \u2014 cr\u00e9ez-en un avec + Nouveau"
],
[
"Paste .bib content or choose a file",
"Collez le contenu .bib ou choisissez un fichier"
],
[
"Request a research-questions report",
"Demander un rapport de questions de recherche"
],
[
"Select the text and copy it (Cmd-C)",
"S\u00e9lectionnez le texte et copiez-le (Cmd-C)"
],
[
"Click to leave out of this question",
"Cliquez pour l'exclure de cette question"
],
[
"blue ring = close to your research",
"anneau bleu = proche de votre recherche"
],
[
"as soon as the day's papers are in",
"d\u00e8s que les articles du jour sont arriv\u00e9s"
],
[
"Anything on your mind? (optional)",
"Quelque chose en t\u00eate ? (facultatif)"
],
[
"Filter by title, author, concept\u2026",
"Filtrer par titre, auteur, concept\u2026"
],
[
"No goals yet \u2014 add one with + New",
"Aucun objectif pour l'instant \u2014 ajoutez-en un avec + Nouveau"
],
[
"Queue empty \u2014 star papers with \u2606.",
"File vide \u2014 marquez des articles avec \u2606."
],
[
"Themes \u00b7 Aurora / Automne / Cyber",
"Th\u00e8mes \u00b7 Aurora / Automne / Cyber"
],
[
"Coming up \u2014 courses and deadlines",
"\u00c0 venir \u2014 cours et \u00e9ch\u00e9ances"
],
[
"Connect at least one member first",
"Connectez d'abord au moins un membre"
],
[
"turns your readings into projects",
"transforme vos lectures en projets"
],
[
"High-tech \u00b7 Futuristic \u00b7 Focused",
"High-tech \u00b7 Futuriste \u00b7 Concentr\u00e9"
],
[
"Latest in your pinned categories",
"Derni\u00e8res parutions dans vos cat\u00e9gories \u00e9pingl\u00e9es"
],
[
"Not enough tracked activity yet.",
"Pas encore assez d'activit\u00e9 suivie."
],
[
"Nothing completed yet this week.",
"Rien de termin\u00e9 cette semaine pour l'instant."
],
[
"One task. One timer. No clutter.",
"Une t\u00e2che. Un minuteur. Rien d'autre."
],
[
"across focused work + meetings.",
"en travail concentr\u00e9 + r\u00e9unions."
],
[
"Cleaned safely \u00b7 backup created",
"Nettoy\u00e9 en s\u00e9curit\u00e9 \u00b7 sauvegarde cr\u00e9\u00e9e"
],
[
"Inbox zero \u2726 \u2014 nothing waiting.",
"Bo\u00eete vide \u2726 \u2014 rien en attente."
],
[
"Loading font and math renderer\u2026",
"Chargement de la police et du rendu math\u00e9matique\u2026"
],
[
"Sections found in the full text",
"Sections trouv\u00e9es dans le texte int\u00e9gral"
],
[
"Stopping after the current step",
"Arr\u00eat apr\u00e8s l'\u00e9tape en cours"
],
[
"completed daily-routine items.",
"\u00e9l\u00e9ments de routine quotidienne termin\u00e9s."
],
[
"Domain (e.g. Poisson geometry)",
"Domaine (par ex. g\u00e9om\u00e9trie de Poisson)"
],
[
"No new papers since last check",
"Aucun nouvel article depuis la derni\u00e8re v\u00e9rification"
],
[
"No paper here for this period.",
"Aucun article pour cette p\u00e9riode."
],
[
"Open questions and limitations",
"Questions ouvertes et limites"
],
[
"Open the map in a large window",
"Ouvrir la carte en grand"
],
[
"Session finished \u2014 time saved.",
"Session termin\u00e9e \u2014 temps enregistr\u00e9."
],
[
"Top 3 priorities for next week",
"3 priorit\u00e9s pour la semaine prochaine"
],
[
"VanoAtlas looks for VanoLib in",
"VanoAtlas cherche VanoLib dans"
],
[
"Task created with its deadline",
"T\u00e2che cr\u00e9\u00e9e avec son \u00e9ch\u00e9ance"
],
[
"Clean screen on \u00b7 Esc to exit",
"\u00c9cran \u00e9pur\u00e9 activ\u00e9 \u00b7 \u00c9chap pour quitter"
],
[
"Plan next week\u2019s 3 priorities",
"Planifier les 3 priorit\u00e9s de la semaine prochaine"
],
[
"Choose a message on the left.",
"Choisissez un message \u00e0 gauche."
],
[
"Context and files (optional)",
"Contexte et fichiers (facultatif)"
],
[
"Modern \u00b7 Elegant \u00b7 Inspiring",
"Moderne \u00b7 \u00c9l\u00e9gant \u00b7 Inspirant"
],
[
"Only a short summary so far.",
"Seulement un court r\u00e9sum\u00e9 pour l'instant."
],
[
"Planner rebuilt today\u2019s plan",
"Le Planner a refait le plan du jour"
],
[
"Run this file in a terminal:",
"Lancez ce fichier dans un terminal :"
],
[
"Saved + reading task created",
"Enregistr\u00e9 + t\u00e2che de lecture cr\u00e9\u00e9e"
],
[
"Search a quote or an author\u2026",
"Rechercher une citation ou un auteur\u2026"
],
[
"VanoAtlas server unreachable",
"Serveur VanoAtlas injoignable"
],
[
"Compiling live as you type\u2026",
"Compilation en direct pendant la saisie\u2026"
],
[
"No note with this title yet",
"Aucune note avec ce titre"
],
[
"Not urgent + lower priority",
"Pas urgent + priorit\u00e9 basse"
],
[
"Open VanoLib in its own tab",
"Ouvrir VanoLib dans un onglet"
],
[
"Universal search & commands",
"Recherche universelle et commandes"
],
[
"VanoAtlas heartbeat missing",
"Signal de VanoAtlas absent"
],
[
"Write in Markdown or LaTeX\u2026",
"\u00c9crivez en Markdown ou en LaTeX\u2026"
],
[
"\u2014 reschedule or drop them.",
"\u2014 replanifiez-les ou abandonnez-les."
],
[
"Could not read data health",
"Impossible de lire l'\u00e9tat des donn\u00e9es"
],
[
"Daily task/meeting summary",
"R\u00e9sum\u00e9 quotidien des t\u00e2ches et r\u00e9unions"
],
[
"Example request (optional)",
"Exemple de demande (facultatif)"
],
[
"Monthly productivity chart",
"Graphique de productivit\u00e9 mensuelle"
],
[
"New notes \u00b7 refs \u00b7 results",
"Nouvelles notes \u00b7 r\u00e9fs \u00b7 r\u00e9sultats"
],
[
"No VanoLeaf project found.",
"Aucun projet VanoLeaf trouv\u00e9."
],
[
"Paper not found in VanoLib",
"Article introuvable dans VanoLib"
],
[
"Tasks completed this month",
"T\u00e2ches termin\u00e9es ce mois-ci"
],
[
"Your reading list is empty",
"Votre liste de lecture est vide"
],
[
"arXiv papers on your Mac \u00b7",
"articles arXiv sur votre Mac \u00b7"
],
[
"Say what the post is about",
"Dites de quoi parle le post"
],
[
"(your plan) \u2014 back around",
"(votre forfait) \u2014 de retour vers"
],
[
"Activate Focus / Zen Mode",
"Activer le mode Focus / Zen"
],
[
"Authors (comma-separated)",
"Auteurs (s\u00e9par\u00e9s par des virgules)"
],
[
"Could not start dictation",
"Impossible de d\u00e9marrer la dict\u00e9e"
],
[
"Microphone not available:",
"Micro indisponible :"
],
[
"Notes & results citing it",
"Notes et r\u00e9sultats qui le citent"
],
[
"Open the PDF in a new tab",
"Ouvrir le PDF dans un nouvel onglet"
],
[
"Quotes \u2014 manage / collect",
"Citations \u2014 g\u00e9rer / collecter"
],
[
"Start Focus / Zen session",
"D\u00e9marrer une session Focus / Zen"
],
[
"Statement (LaTeX allowed)",
"\u00c9nonc\u00e9 (LaTeX permis)"
],
[
"Waiting for your approval",
"En attente de votre accord"
],
[
"\u270d Write a post about this",
"\u270d \u00c9crire un post \u00e0 ce sujet"
],
[
"Grounded \u00b7 Natural \u00b7 Calm",
"Ancr\u00e9 \u00b7 Naturel \u00b7 Calme"
],
[
"Directly about your work",
"Directement li\u00e9 \u00e0 votre travail"
],
[
"## Methods and key ideas",
"## M\u00e9thodes et id\u00e9es cl\u00e9s"
],
[
"Added to VanoLib To Read",
"Ajout\u00e9 \u00e0 \u00ab \u00c0 lire \u00bb dans VanoLib"
],
[
"Estimated / planned work",
"Travail estim\u00e9 / pr\u00e9vu"
],
[
"Estimated time (minutes)",
"Temps estim\u00e9 (minutes)"
],
[
"New item in current view",
"Nouvel \u00e9l\u00e9ment dans la vue actuelle"
],
[
"New preprints ranked for",
"Nouvelles pr\u00e9publications class\u00e9es pour"
],
[
"No update published yet.",
"Aucune mise \u00e0 jour publi\u00e9e."
],
[
"Pages read \u00b7 last 7 days",
"Pages lues \u00b7 7 derniers jours"
],
[
"Probability & Statistics",
"Probabilit\u00e9s et statistiques"
],
[
"Saved to Goals \u2192 Journal",
"Enregistr\u00e9 dans Objectifs \u2192 Journal"
],
[
"Shortcuts & Productivity",
"Raccourcis et productivit\u00e9"
],
[
"The full reading failed.",
"La lecture compl\u00e8te a \u00e9chou\u00e9."
],
[
"Turn ideas into reality.",
"Transformez les id\u00e9es en r\u00e9alit\u00e9."
],
[
"Warm \u00b7 Natural \u00b7 Focused",
"Chaleureux \u00b7 Naturel \u00b7 Concentr\u00e9"
],
[
"Your papers found online",
"Vos articles trouv\u00e9s en ligne"
],
[
"Your plan, no extra cost",
"Votre forfait, sans co\u00fbt suppl\u00e9mentaire"
],
[
"anywhere, or \u2318K then \u21e7\u21b5.",
"n'importe o\u00f9, ou \u2318K puis \u21e7\u21b5."
],
[
"Wake up \u00b7 water, stretch",
"R\u00e9veil \u00b7 eau, \u00e9tirements"
],
[
"\u270d Write the draft (Full)",
"\u270d \u00c9crire le brouillon (Complet)"
],
[
"Calm \u00b7 Editorial \u00b7 Clear",
"Calme \u00b7 \u00c9ditorial \u00b7 Clair"
],
[
"pink ring = new concept",
"anneau rose = nouveau concept"
],
[
"Already in Bibliography",
"D\u00e9j\u00e0 dans la bibliographie"
],
[
"Drop here to unschedule",
"D\u00e9poser ici pour d\u00e9planifier"
],
[
"Local copy of the site:",
"Copie locale du site :"
],
[
"Nothing in this filter.",
"Rien dans ce filtre."
],
[
"Removed from favourites",
"Retir\u00e9e des favoris"
],
[
"## Context and problem",
"## Contexte et probl\u00e8me"
],
[
"Auto-corrected \u00b7 saved",
"Corrig\u00e9 automatiquement \u00b7 enregistr\u00e9"
],
[
"Create + estimate work",
"Cr\u00e9er + estimer le travail"
],
[
"Label (e.g. Lemma 3.4)",
"\u00c9tiquette (par ex. Lemme 3.4)"
],
[
"No expenses this month",
"Aucune d\u00e9pense ce mois-ci"
],
[
"Productivity Analytics",
"Analyse de productivit\u00e9"
],
[
"Quick capture to Inbox",
"Saisie rapide vers la bo\u00eete de r\u00e9ception"
],
[
"Reading (no full text)",
"Lecture (sans texte int\u00e9gral)"
],
[
"Remind before meetings",
"Rappel avant les r\u00e9unions"
],
[
"Research profile saved",
"Profil de recherche enregistr\u00e9"
],
[
"Review overdue tasks (",
"Revoir les t\u00e2ches en retard ("
],
[
"Saved \u2014 repeats weekly",
"Enregistr\u00e9 \u2014 se r\u00e9p\u00e8te chaque semaine"
],
[
"Structure of the paper",
"Structure de l'article"
],
[
"Tags (comma separated)",
"\u00c9tiquettes (s\u00e9par\u00e9es par des virgules)"
],
[
"Tags (comma-separated)",
"\u00c9tiquettes (s\u00e9par\u00e9es par des virgules)"
],
[
"Test notification sent",
"Notification de test envoy\u00e9e"
],
[
"Urgent, lower priority",
"Urgent, priorit\u00e9 plus basse"
],
[
"Weekly (e.g. a course)",
"Hebdomadaire (par ex. un cours)"
],
[
"and attach your files:",
"et joignez vos fichiers :"
],
[
"to create it as a task",
"pour en faire une t\u00e2che"
],
[
"\ud83d\udcd6 Read the whole paper",
"\ud83d\udcd6 Lire l'article en entier"
],
[
"Writing the draft now\u2026",
"R\u00e9daction du brouillon\u2026"
],
[
"Dark \u00b7 Refined \u00b7 Quiet",
"Sombre \u00b7 Raffin\u00e9 \u00b7 Silencieux"
],
[
"Cool \u00b7 Clean \u00b7 Precise",
"Frais \u00b7 Net \u00b7 Pr\u00e9cis"
],
[
"Calendars (iCal links)",
"Calendriers (liens iCal)"
],
[
"## Main contributions",
"## Contributions principales"
],
[
"Added to Bibliography",
"Ajout\u00e9 \u00e0 la bibliographie"
],
[
"Afternoon work starts",
"D\u00e9but du travail de l'apr\u00e8s-midi"
],
[
"Backed up to Drive at",
"Sauvegard\u00e9 sur Drive \u00e0"
],
[
"Checking video tools\u2026",
"V\u00e9rification des outils vid\u00e9o\u2026"
],
[
"Exit large view (Esc)",
"Quitter la grande vue (\u00c9chap)"
],
[
"Go to Knowledge Graph",
"Aller au graphe de connaissances"
],
[
"Important, not urgent",
"Important, pas urgent"
],
[
"Methods and key ideas",
"M\u00e9thodes et id\u00e9es cl\u00e9s"
],
[
"Pages read in VanoLib",
"Pages lues dans VanoLib"
],
[
"Priorities next week:",
"Priorit\u00e9s de la semaine prochaine :"
],
[
"Renderer on this Mac:",
"Rendu sur ce Mac :"
],
[
"Saved to bibliography",
"Enregistr\u00e9 dans la bibliographie"
],
[
"Start a timed session",
"D\u00e9marrer une session chronom\u00e9tr\u00e9e"
],
[
"Subsubsection heading",
"Titre de sous-sous-section"
],
[
"Time: plan vs reality",
"Temps : pr\u00e9vu et r\u00e9el"
],
[
"Write a request first",
"\u00c9crivez d'abord une demande"
],
[
"Your research profile",
"Votre profil de recherche"
],
[
"Breakfast & get ready",
"Petit-d\u00e9jeuner et pr\u00e9paration"
],
[
"Deep \u00b7 Fluid \u00b7 Serene",
"Profond \u00b7 Fluide \u00b7 Serein"
],
[
"Soft \u00b7 Light \u00b7 Gentle",
"Doux \u00b7 Lumineux \u00b7 D\u00e9licat"
],
[
"Loading your courses\u2026",
"Chargement de vos cours\u2026"
],
[
"scroll or +/\u2212 = zoom",
"molette ou +/\u2212 = zoom"
],
[
"Audit + safe cleanup",
"Audit + nettoyage s\u00fbr"
],
[
"Fit / reset view (0)",
"Ajuster / r\u00e9initialiser la vue (0)"
],
[
"Focused work tracked",
"Travail concentr\u00e9 suivi"
],
[
"Image export failed.",
"\u00c9chec de l'export de l'image."
],
[
"Monthly Productivity",
"Productivit\u00e9 mensuelle"
],
[
"New papers this week",
"Nouveaux articles cette semaine"
],
[
"No paper found for \u201c",
"Aucun article trouv\u00e9 pour \u00ab "
],
[
"No upcoming meetings",
"Aucune r\u00e9union \u00e0 venir"
],
[
"Nothing found \u2014 type",
"Rien trouv\u00e9 \u2014 tapez"
],
[
"Nothing in progress.",
"Rien en cours."
],
[
"Pages read this week",
"Pages lues cette semaine"
],
[
"Proof sketch / notes",
"Esquisse de preuve / notes"
],
[
"Radar settings saved",
"R\u00e9glages du radar enregistr\u00e9s"
],
[
"Reading task created",
"T\u00e2che de lecture cr\u00e9\u00e9e"
],
[
"Removed from To Read",
"Retir\u00e9 de \u00ab \u00c0 lire \u00bb"
],
[
"Rendering the video\u2026",
"Rendu de la vid\u00e9o\u2026"
],
[
"Sync Google Calendar",
"Synchroniser Google Agenda"
],
[
"Tasks done / planned",
"T\u00e2ches faites / pr\u00e9vues"
],
[
"Welcome to VanoAtlas",
"Bienvenue dans VanoAtlas"
],
[
"What's on your mind?",
"Qu'avez-vous en t\u00eate ?"
],
[
"and the introduction",
"et l'introduction"
],
[
"around your meetings",
"autour de vos r\u00e9unions"
],
[
"\u2699 Keywords & authors",
"\u2699 Mots-cl\u00e9s et auteurs"
],
[
"Connect your inboxes",
"Connecter vos bo\u00eetes courriel"
],
[
"Opening the message\u2026",
"Ouverture du message\u2026"
],
[
"= quick answer from",
"= r\u00e9ponse rapide de"
],
[
"instant answer from",
"r\u00e9ponse instantan\u00e9e de"
],
[
"## Detailed summary",
"## R\u00e9sum\u00e9 d\u00e9taill\u00e9"
],
[
"(not available yet)",
"(pas encore disponible)"
],
[
"Afternoon work ends",
"Fin du travail de l'apr\u00e8s-midi"
],
[
"Applied Mathematics",
"Math\u00e9matiques appliqu\u00e9es"
],
[
"Backup + Safe Clean",
"Sauvegarde + nettoyage s\u00fbr"
],
[
"Choose a task first",
"Choisissez d'abord une t\u00e2che"
],
[
"Completed this week",
"Termin\u00e9 cette semaine"
],
[
"Context and problem",
"Contexte et probl\u00e8me"
],
[
"Create a task first",
"Cr\u00e9ez d'abord une t\u00e2che"
],
[
"Customizable Colors",
"Couleurs personnalisables"
],
[
"Daily focus average",
"Concentration moyenne par jour"
],
[
"Filter current view",
"Filtrer la vue actuelle"
],
[
"Info \u00b7 Science News",
"Info \u00b7 Actualit\u00e9s scientifiques"
],
[
"Morning work starts",
"D\u00e9but du travail du matin"
],
[
"Routine completions",
"Routines accomplies"
],
[
"Routine done today:",
"Routine faite aujourd'hui :"
],
[
"Save my corrections",
"Enregistrer mes corrections"
],
[
"Toggle clean screen",
"Basculer l'\u00e9cran \u00e9pur\u00e9"
],
[
"VanoLib unavailable",
"VanoLib indisponible"
],
[
"\ud83d\udd00 Shuffle variation",
"\ud83d\udd00 Varier"
],
[
"Break \u00b7 walk, water",
"Pause \u00b7 marche, eau"
],
[
"Quick draft (Local)",
"Brouillon rapide (Local)"
],
[
"Topics in your feed",
"Th\u00e8mes de votre fil"
],
[
"Activity \u00b7 14 days",
"Activit\u00e9 \u00b7 14 jours"
],
[
"Casual handwriting",
"\u00c9criture d\u00e9contract\u00e9e"
],
[
"Collect online now",
"Collecter en ligne maintenant"
],
[
"Compilation failed",
"\u00c9chec de la compilation"
],
[
"Daily summary time",
"Heure du r\u00e9sum\u00e9 quotidien"
],
[
"Dictation language",
"Langue de la dict\u00e9e"
],
[
"Edit journal entry",
"Modifier l'entr\u00e9e du journal"
],
[
"How are you doing?",
"Comment allez-vous ?"
],
[
"Keyboard shortcuts",
"Raccourcis clavier"
],
[
"Main contributions",
"Contributions principales"
],
[
"Mathematical Logic",
"Logique math\u00e9matique"
],
[
"Reading in VanoLib",
"Lecture dans VanoLib"
],
[
"Search anything...",
"Rechercher\u2026"
],
[
"Subsection heading",
"Titre de sous-section"
],
[
"The quote is empty",
"La citation est vide"
],
[
"Today in science \u00b7",
"Aujourd'hui en science \u00b7"
],
[
"Art & Illustration",
"Art et illustration"
],
[
"\u2190 Back to the feed",
"\u2190 Retour au fil"
],
[
"Nothing coming up.",
"Rien \u00e0 venir."
],
[
"+ Add the calendar",
"+ Ajouter le calendrier"
],
[
"## Key references",
"## R\u00e9f\u00e9rences principales"
],
[
"## Open questions",
"## Questions ouvertes"
],
[
"Aligned equations",
"\u00c9quations align\u00e9es"
],
[
"Best activity day",
"Meilleur jour d'activit\u00e9"
],
[
"Empty the Inbox (",
"Vider la bo\u00eete de r\u00e9ception ("
],
[
"Estimate variance",
"\u00c9cart d'estimation"
],
[
"Fountain pen blue",
"Bleu stylo plume"
],
[
"Full text ready (",
"Texte int\u00e9gral pr\u00eat ("
],
[
"Generating image\u2026",
"G\u00e9n\u00e9ration de l'image\u2026"
],
[
"Go to Daily Tasks",
"Aller aux t\u00e2ches quotidiennes"
],
[
"Go to Day Planner",
"Aller au planificateur"
],
[
"Meeting reminders",
"Rappels de r\u00e9union"
],
[
"Milestone deleted",
"Jalon supprim\u00e9"
],
[
"Morning work ends",
"Fin du travail du matin"
],
[
"New journal entry",
"Nouvelle entr\u00e9e de journal"
],
[
"Nothing to accept",
"Rien \u00e0 accepter"
],
[
"Progress Overview",
"Vue d'ensemble des progr\u00e8s"
],
[
"Quote shown again",
"Citation affich\u00e9e de nouveau"
],
[
"Reference deleted",
"R\u00e9f\u00e9rence supprim\u00e9e"
],
[
"Reference updated",
"R\u00e9f\u00e9rence mise \u00e0 jour"
],
[
"Running \u00b7 models:",
"En marche \u00b7 mod\u00e8les :"
],
[
"Tap to retry sync",
"Touchez pour relancer la synchronisation"
],
[
"Title is required",
"Le titre est obligatoire"
],
[
"Upcoming Meetings",
"R\u00e9unions \u00e0 venir"
],
[
"Ask Deep Research",
"Demander \u00e0 Deep Research"
],
[
"Opening the post\u2026",
"Ouverture du post\u2026"
],
[
"\u270d Draft the reply",
"\u270d R\u00e9diger la r\u00e9ponse"
],
[
" \u00b7 last message ",
" \u00b7 dernier message il y a "
],
[
"Advisory Meeting",
"R\u00e9union de direction"
],
[
"Aged cream paper",
"Papier cr\u00e8me vieilli"
],
[
"Clean screen off",
"\u00c9cran \u00e9pur\u00e9 d\u00e9sactiv\u00e9"
],
[
"Computer Science",
"Informatique"
],
[
"Continue reading",
"Continuer la lecture"
],
[
"Copy as Markdown",
"Copier en Markdown"
],
[
"Dependency graph",
"Graphe des d\u00e9pendances"
],
[
"Detailed summary",
"R\u00e9sum\u00e9 d\u00e9taill\u00e9"
],
[
"Dictation error:",
"Erreur de dict\u00e9e :"
],
[
"Edit budget line",
"Modifier la ligne de budget"
],
[
"Fine calligraphy",
"Calligraphie fine"
],
[
"Fluid Animations",
"Animations fluides"
],
[
"Focus time saved",
"Temps de concentration enregistr\u00e9"
],
[
"Image exported \u2713",
"Image export\u00e9e \u2713"
],
[
"Loading VanoLib\u2026",
"Chargement de VanoLib\u2026"
],
[
"Open in VanoLeaf",
"Ouvrir dans VanoLeaf"
],
[
"Other milestones",
"Autres jalons"
],
[
"Planned work day",
"Journ\u00e9e de travail pr\u00e9vue"
],
[
"Planner settings",
"R\u00e9glages du Planner"
],
[
"Project Progress",
"Avancement des projets"
],
[
"Quick / delegate",
"Rapide / d\u00e9l\u00e9guer"
],
[
"Tracked workload",
"Charge suivie"
],
[
"Unsaved changes\u2026",
"Modifications non enregistr\u00e9es\u2026"
],
[
"Workload planner",
"Planificateur de charge"
],
[
"No message here.",
"Aucun message ici."
],
[
"than estimated.",
"que pr\u00e9vu."
],
[
"items processed",
"\u00e9l\u00e9ments trait\u00e9s"
],
[
"## Main results",
"## R\u00e9sultats principaux"
],
[
"Active Projects",
"Projets actifs"
],
[
"Auto-plan today",
"Planifier aujourd'hui automatiquement"
],
[
"Backup to Drive",
"Sauvegarder sur Drive"
],
[
"Capture an idea",
"Noter une id\u00e9e"
],
[
"Completed tasks",
"T\u00e2ches termin\u00e9es"
],
[
"Could not start",
"Impossible de d\u00e9marrer"
],
[
"Does not repeat",
"Ne se r\u00e9p\u00e8te pas"
],
[
"Edit daily item",
"Modifier l'\u00e9l\u00e9ment quotidien"
],
[
"Elegant antique",
"Ancien \u00e9l\u00e9gant"
],
[
"Go to Dashboard",
"Aller au tableau de bord"
],
[
"Horizontal rule",
"Ligne horizontale"
],
[
"Journal / venue",
"Revue / lieu"
],
[
"Knowledge Graph",
"Graphe de connaissances"
],
[
"Local \u00b7 instant",
"Local \u00b7 instantan\u00e9"
],
[
"Month (YYYY-MM)",
"Mois (AAAA-MM)"
],
[
"Natural cursive",
"Cursive naturelle"
],
[
"New budget line",
"Nouvelle ligne de budget"
],
[
"No request yet.",
"Aucune demande pour l'instant."
],
[
"Nothing planned",
"Rien de pr\u00e9vu"
],
[
"Open in VanoLib",
"Ouvrir dans VanoLib"
],
[
"PDF unavailable",
"PDF indisponible"
],
[
"PDFs downloaded",
"PDF t\u00e9l\u00e9charg\u00e9s"
],
[
"Priority Matrix",
"Matrice des priorit\u00e9s"
],
[
"Project deleted",
"Projet supprim\u00e9"
],
[
"Querying arXiv\u2026",
"Interrogation d'arXiv\u2026"
],
[
"Read in VanoLib",
"Lire dans VanoLib"
],
[
"Ready to review",
"Pr\u00eat \u00e0 relire"
],
[
"Reference added",
"R\u00e9f\u00e9rence ajout\u00e9e"
],
[
"Section heading",
"Titre de section"
],
[
"Tasks Completed",
"T\u00e2ches termin\u00e9es"
],
[
"VanoLib To Read",
"VanoLib \u00ab \u00c0 lire \u00bb"
],
[
"Website updates",
"Mises \u00e0 jour du site"
],
[
"Weekly review \u2014",
"Bilan de la semaine \u2014"
],
[
"completed tasks",
"t\u00e2ches termin\u00e9es"
],
[
"highly relevant",
"tr\u00e8s pertinent"
],
[
"in bibliography",
"dans la bibliographie"
],
[
"no dependencies",
"aucune d\u00e9pendance"
],
[
"pages this week",
"pages cette semaine"
],
[
"% vs last week",
"% par rapport \u00e0 la semaine derni\u00e8re"
],
[
"+ Bibliography",
"+ Bibliographie"
],
[
"Added to Inbox",
"Ajout\u00e9 \u00e0 la bo\u00eete de r\u00e9ception"
],
[
"All categories",
"Toutes les cat\u00e9gories"
],
[
"Cleanup failed",
"\u00c9chec du nettoyage"
],
[
"Command Center",
"Centre de commandes"
],
[
"Daily activity",
"Activit\u00e9 quotidienne"
],
[
"Drag to resize",
"Glisser pour redimensionner"
],
[
"Edit milestone",
"Modifier le jalon"
],
[
"Edit reference",
"Modifier la r\u00e9f\u00e9rence"
],
[
"Finish session",
"Terminer la session"
],
[
"Footnote text:",
"Texte de la note :"
],
[
"Go to Meetings",
"Aller aux r\u00e9unions"
],
[
"Go to Projects",
"Aller aux projets"
],
[
"Go to Timeline",
"Aller \u00e0 la chronologie"
],
[
"Handwrite note",
"Note manuscrite"
],
[
"Key references",
"R\u00e9f\u00e9rences principales"
],
[
"Market brief \u00b7",
"Point march\u00e9s \u00b7"
],
[
"Meeting starts",
"D\u00e9but de la r\u00e9union"
],
[
"Month insights",
"Analyse du mois"
],
[
"New daily item",
"Nouvel \u00e9l\u00e9ment quotidien"
],
[
"No new quotes:",
"Pas de nouvelles citations :"
],
[
"No quote here.",
"Aucune citation ici."
],
[
"Not available:",
"Indisponible :"
],
[
"Note not found",
"Note introuvable"
],
[
"Open questions",
"Questions ouvertes"
],
[
"Outstanding: $",
"Restant : $"
],
[
"Preparing PDF\u2026",
"Pr\u00e9paration du PDF\u2026"
],
[
"Radar settings",
"R\u00e9glages du radar"
],
[
"Reading failed",
"\u00c9chec de la lecture"
],
[
"Ready to focus",
"Pr\u00eat \u00e0 se concentrer"
],
[
"Relevant to me",
"Pertinents pour moi"
],
[
"Result deleted",
"R\u00e9sultat supprim\u00e9"
],
[
"Result updated",
"R\u00e9sultat mis \u00e0 jour"
],
[
"Syncing arXiv\u2026",
"Synchronisation d'arXiv\u2026"
],
[
"Thesis Defense",
"Soutenance de th\u00e8se"
],
[
"Untitled entry",
"Entr\u00e9e sans titre"
],
[
"Weekly meeting",
"R\u00e9union hebdomadaire"
],
[
"no time logged",
"aucun temps enregistr\u00e9"
],
[
"\ud83d\uddbc Export image",
"\ud83d\uddbc Exporter l'image"
],
[
"Good afternoon",
"Bon apr\u00e8s-midi"
],
[
"Mark as unread",
"Marquer comme non lu"
],
[
"Calendar added",
"Calendrier ajout\u00e9"
],
[
"papers \u00b7 last",
"articles \u00b7 derniers"
],
[
"full readings",
"lectures compl\u00e8tes"
],
[
"recent papers",
"articles r\u00e9cents"
],
[
"(free, local)",
"(gratuit, local)"
],
[
" for python3.",
" pour python3."
],
[
" \u00b7 up to date",
" \u00b7 \u00e0 jour"
],
[
"(8 GB Mac) or",
"(Mac 8 Go) ou"
],
[
"**In short.**",
"**En bref.**"
],
[
"Another quote",
"Une autre citation"
],
[
"Centered text",
"Texte centr\u00e9"
],
[
"Compile error",
"Erreur de compilation"
],
[
"Copy as LaTeX",
"Copier en LaTeX"
],
[
"Dismiss shown",
"Masquer ceux affich\u00e9s"
],
[
"Edit check-in",
"Modifier le bilan"
],
[
"Event deleted",
"\u00c9v\u00e9nement supprim\u00e9"
],
[
"Focus Session",
"Session de concentration"
],
[
"Focus session",
"Session de concentration"
],
[
"Focus tracked",
"Concentration suivie"
],
[
"Full readings",
"Lectures compl\u00e8tes"
],
[
"Go to VanoLib",
"Aller \u00e0 VanoLib"
],
[
"Import BibTeX",
"Importer BibTeX"
],
[
"Import failed",
"\u00c9chec de l'import"
],
[
"Linked from \u2190",
"Li\u00e9 depuis \u2190"
],
[
"Loading news\u2026",
"Chargement des actualit\u00e9s\u2026"
],
[
"Lookup failed",
"Recherche \u00e9chou\u00e9e"
],
[
"New milestone",
"Nouveau jalon"
],
[
"New reference",
"Nouvelle r\u00e9f\u00e9rence"
],
[
"No budget for",
"Pas de budget pour"
],
[
"Number Theory",
"Th\u00e9orie des nombres"
],
[
"Numbered list",
"Liste num\u00e9rot\u00e9e"
],
[
"Open VanoLeaf",
"Ouvrir VanoLeaf"
],
[
"Quick actions",
"Actions rapides"
],
[
"Quick capture",
"Saisie rapide"
],
[
"Quote deleted",
"Citation supprim\u00e9e"
],
[
"Re-plan today",
"Re-planifier aujourd'hui"
],
[
"Reading notes",
"Notes de lecture"
],
[
"Reuse request",
"R\u00e9utiliser la demande"
],
[
"Task created:",
"T\u00e2che cr\u00e9\u00e9e :"
],
[
"To Read queue",
"File \u00ab \u00c0 lire \u00bb"
],
[
"Today planned",
"Pr\u00e9vu aujourd'hui"
],
[
"Today's Tasks",
"T\u00e2ches du jour"
],
[
"Weekly Review",
"Bilan de la semaine"
],
[
"Weekly effort",
"Effort hebdomadaire"
],
[
"Writing style",
"Style d'\u00e9criture"
],
[
"no link found",
"aucun lien trouv\u00e9"
],
[
"planned today",
"pr\u00e9vu aujourd'hui"
],
[
"No draft yet.",
"Aucun brouillon pour l'instant."
],
[
"tasks finish",
"t\u00e2ches se terminent"
],
[
"Up to date \u00b7",
"\u00c0 jour \u00b7"
],
[
"(empty note)",
"(note vide)"
],
[
"Add an event",
"Ajouter un \u00e9v\u00e9nement"
],
[
"All projects",
"Tous les projets"
],
[
"Automne mode",
"Mode Automne"
],
[
"Budgeted ($)",
"Budg\u00e9t\u00e9 ($)"
],
[
"Center align",
"Centrer"
],
[
"Daily Streak",
"S\u00e9rie quotidienne"
],
[
"Dotted paper",
"Papier pointill\u00e9"
],
[
"Edit expense",
"Modifier la d\u00e9pense"
],
[
"Edit project",
"Modifier le projet"
],
[
"Full reading",
"Lecture compl\u00e8te"
],
[
"Last 14 days",
"14 derniers jours"
],
[
"Last 30 days",
"30 derniers jours"
],
[
"Main results",
"R\u00e9sultats principaux"
],
[
"Meeting ends",
"Fin de la r\u00e9union"
],
[
"Meeting time",
"Heure de la r\u00e9union"
],
[
"Mental state",
"\u00c9tat d'esprit"
],
[
"Never synced",
"Jamais synchronis\u00e9"
],
[
"No notes yet",
"Aucune note pour l'instant"
],
[
"No task yet.",
"Aucune t\u00e2che pour l'instant."
],
[
"No tasks yet",
"Aucune t\u00e2che pour l'instant"
],
[
"Not created:",
"Non cr\u00e9\u00e9 :"
],
[
"Note created",
"Note cr\u00e9\u00e9e"
],
[
"Note details",
"D\u00e9tails de la note"
],
[
"Nothing here",
"Rien ici"
],
[
"Office Hours",
"Heures de bureau"
],
[
"Quick Themes",
"Th\u00e8mes rapides"
],
[
"Radar failed",
"\u00c9chec du radar"
],
[
"Repeat until",
"R\u00e9p\u00e9ter jusqu'au"
],
[
"Result added",
"R\u00e9sultat ajout\u00e9"
],
[
"Results Bank",
"Banque de r\u00e9sultats"
],
[
"Save profile",
"Enregistrer le profil"
],
[
"Seminar Talk",
"Expos\u00e9 de s\u00e9minaire"
],
[
"Switch theme",
"Changer de th\u00e8me"
],
[
"Task created",
"T\u00e2che cr\u00e9\u00e9e"
],
[
"Task deleted",
"T\u00e2che supprim\u00e9e"
],
[
"Task sent to",
"T\u00e2che envoy\u00e9e \u00e0"
],
[
"Task updated",
"T\u00e2che mise \u00e0 jour"
],
[
"Team Meeting",
"R\u00e9union d'\u00e9quipe"
],
[
"Trend \u00b7 last",
"Tendance \u00b7 derniers"
],
[
"Your request",
"Votre demande"
],
[
"Zoom out (\u2212)",
"Zoom arri\u00e8re (\u2212)"
],
[
"arXiv papers",
"articles arXiv"
],
[
"overdue task",
"t\u00e2che en retard"
],
[
"\u23fa Listening\u2026",
"\u23fa \u00c9coute\u2026"
],
[
"\u2605 Favourites",
"\u2605 Favoris"
],
[
"\u2913 Export PDF",
"\u2913 Exporter en PDF"
],
[
"\u2922 Large view",
"\u2922 Grande vue"
],
[
"Good morning",
"Bonjour"
],
[
"Good evening",
"Bonsoir"
],
[
"Generate now",
"G\u00e9n\u00e9rer maintenant"
],
[
"errors today",
"erreurs aujourd'hui"
],
[
"(no subject)",
"(sans objet)"
],
[
"Mark as read",
"Marquer comme lu"
],
[
"tasks done.",
"t\u00e2ches faites."
],
[
"papers over",
"articles sur"
],
[
"drag = move",
"glisser = d\u00e9placer"
],
[
"Connected \u00b7",
"Connect\u00e9 \u00b7"
],
[
"(free tier)",
"(offre gratuite)"
],
[
"Deep work: ",
"Travail profond : "
],
[
"+ Add quote",
"+ Ajouter une citation"
],
[
"+ Milestone",
"+ Jalon"
],
[
"Analyze now",
"Analyser maintenant"
],
[
"Aurora mode",
"Mode Aurora"
],
[
"Back online",
"De nouveau en ligne"
],
[
"Backing up\u2026",
"Sauvegarde\u2026"
],
[
"Being read.",
"En cours de lecture."
],
[
"Bullet list",
"Liste \u00e0 puces"
],
[
"Daily Tasks",
"T\u00e2ches quotidiennes"
],
[
"Data Health",
"\u00c9tat des donn\u00e9es"
],
[
"Data health",
"\u00c9tat des donn\u00e9es"
],
[
"Day Planner",
"Planificateur"
],
[
"Edit result",
"Modifier le r\u00e9sultat"
],
[
"Export .bib",
"Exporter .bib"
],
[
"Full screen",
"Plein \u00e9cran"
],
[
"Go to Notes",
"Aller aux notes"
],
[
"Go to Tasks",
"Aller aux t\u00e2ches"
],
[
"Import .bib",
"Importer .bib"
],
[
"In Progress",
"En cours"
],
[
"In progress",
"En cours"
],
[
"Key saved (",
"Cl\u00e9 enregistr\u00e9e ("
],
[
"Last 7 days",
"7 derniers jours"
],
[
"Last export",
"Dernier export"
],
[
"Lined paper",
"Papier lign\u00e9"
],
[
"Meeting day",
"Jour de r\u00e9union"
],
[
"New expense",
"Nouvelle d\u00e9pense"
],
[
"New meeting",
"Nouvelle r\u00e9union"
],
[
"New project",
"Nouveau projet"
],
[
"Not queued:",
"Non mis en file :"
],
[
"Not related",
"Sans lien"
],
[
"Over budget",
"Budget d\u00e9pass\u00e9"
],
[
"PDF preview",
"Aper\u00e7u PDF"
],
[
"Quick Notes",
"Notes rapides"
],
[
"Quote saved",
"Citation enregistr\u00e9e"
],
[
"Quoted text",
"Texte cit\u00e9"
],
[
"Reading now",
"En lecture"
],
[
"Recent Wins",
"R\u00e9ussites r\u00e9centes"
],
[
"Start focus",
"D\u00e9marrer la concentration"
],
[
"Start timer",
"D\u00e9marrer le minuteur"
],
[
"Target date",
"Date vis\u00e9e"
],
[
"Tasks Today",
"T\u00e2ches du jour"
],
[
"To schedule",
"\u00c0 planifier"
],
[
"Undo failed",
"\u00c9chec de l'annulation"
],
[
"Zoom in (+)",
"Zoom avant (+)"
],
[
"arXiv Radar",
"Radar arXiv"
],
[
"tasks today",
"t\u00e2ches aujourd'hui"
],
[
"\u2192 Reference",
"\u2192 R\u00e9f\u00e9rence"
],
[
"\u21bb Fetch now",
"\u21bb R\u00e9cup\u00e9rer maintenant"
],
[
"\u26a1 Auto-plan",
"\u26a1 Planification auto"
],
[
"\ud83d\udcdd Word mode",
"\ud83d\udcdd Mode Word"
],
[
"Collect now",
"Collecter maintenant"
],
[
"Lunch break",
"Pause du midi"
],
[
"Save topics",
"Enregistrer les th\u00e8mes"
],
[
"\u2726 Summarize",
"\u2726 R\u00e9sumer"
],
[
"Your reply\u2026",
"Votre r\u00e9ponse\u2026"
],
[
"Quick links",
"Liens rapides"
],
[
"tasks take",
"t\u00e2ches prennent"
],
[
"concepts \u00b7",
"concepts \u00b7"
],
[
"being read",
"en cours de lecture"
],
[
"Same field",
"M\u00eame domaine"
],
[
"last check",
"derni\u00e8re v\u00e9rification"
],
[
"(after the",
"(apr\u00e8s le"
],
[
"+ New task",
"+ Nouvelle t\u00e2che"
],
[
"Actual ($)",
"R\u00e9el ($)"
],
[
"All quotes",
"Toutes les citations"
],
[
"All recent",
"Tous les r\u00e9cents"
],
[
"Amount ($)",
"Montant ($)"
],
[
"BibTeX key",
"Cl\u00e9 BibTeX"
],
[
"Code block",
"Bloc de code"
],
[
"Cyber mode",
"Mode Cyber"
],
[
"Edit event",
"Modifier l'\u00e9v\u00e9nement"
],
[
"Edit quote",
"Modifier la citation"
],
[
"Full power",
"Pleine puissance"
],
[
"Grid paper",
"Papier quadrill\u00e9"
],
[
"In To Read",
"Dans \u00ab \u00c0 lire \u00bb"
],
[
"Large view",
"Grande vue"
],
[
"Leave home",
"D\u00e9part de la maison"
],
[
"Looking up",
"Recherche"
],
[
"New result",
"Nouveau r\u00e9sultat"
],
[
"Next focus",
"Prochaine concentration"
],
[
"Night mode",
"Mode nuit"
],
[
"No debts \ud83c\udf89",
"Aucune dette \ud83c\udf89"
],
[
"Not saved:",
"Non enregistr\u00e9 :"
],
[
"Note graph",
"Graphe des notes"
],
[
"Note saved",
"Note enregistr\u00e9e"
],
[
"Open Tasks",
"T\u00e2ches ouvertes"
],
[
"Open map \u2197",
"Ouvrir la carte \u2197"
],
[
"Page break",
"Saut de page"
],
[
"Read again",
"Relire"
],
[
"Read from:",
"Lu \u00e0 partir de :"
],
[
"Running on",
"Fonctionne sur"
],
[
"Short name",
"Nom court"
],
[
"Start time",
"Heure de d\u00e9but"
],
[
"Stop timer",
"Arr\u00eater le minuteur"
],
[
"Text color",
"Couleur du texte"
],
[
"This month",
"Ce mois-ci"
],
[
"Up to date",
"\u00c0 jour"
],
[
"\u2715 Full map",
"\u2715 Carte compl\u00e8te"
],
[
"This week:",
"Cette semaine :"
],
[
"Update now",
"Mettre \u00e0 jour"
],
[
"Projects \u00b7",
"Projets \u00b7"
],
[
"paid cost",
"co\u00fbt payant"
],
[
"Waiting \u00b7",
"En attente \u00b7"
],
[
", no web.",
", sans web."
],
[
" min read",
" min de lecture"
],
[
"+ Follow ",
"+ Suivre "
],
[
" days ago",
" jours"
],
[
"papers \u00b7",
"articles \u00b7"
],
[
" \u00b7 due ",
" \u00b7 \u00e9ch\u00e9ance "
],
[
" \u00b7 Full",
" \u00b7 Complet"
],
[
"cost $",
"co\u00fbt $"
],
[
"Work: ",
"Travail : "
],
[
"\ud83d\udd12 paid",
"\ud83d\udd12 payant"
]
];
let VA_LANG=(()=>{ try{ return localStorage.getItem('va_lang')||'en'; }catch(e){ return 'en'; } })();
const VA_MONTHS={January:'janvier',February:'février',March:'mars',April:'avril',May:'mai',June:'juin',July:'juillet',August:'août',September:'septembre',October:'octobre',November:'novembre',December:'décembre'};
const VA_PAT=[
  [/^\$([\d.]+) left$/,'$1 $ restants'],
  [/^Good morning, (.*)$/,'Bonjour, $1'],[/^Good afternoon, (.*)$/,'Bon après-midi, $1'],[/^Good evening, (.*)$/,'Bonsoir, $1'],
  [/^(\d[\d,]*) papers?$/,'$1 articles'],[/^(\d+) d$/,'$1 j'],[/^(\d+) days?$/,'$1 jours'],[/^(\d+) min$/,'$1 min'],
  [/^(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})$/,(m,a,b,c)=>b+' '+VA_MONTHS[a]+' '+c],
  [/^(\d+) (?:full readings?)(.*)$/,'$1 lectures complètes$2'],
  [/^Updated (.*)$/,'Mis à jour $1'],[/^· updated (.+) ago$/,'· mis à jour il y a $1'],[/^Following (.+)$/,'Vous suivez $1'],[/^(\d+) done$/,'$1 terminé(s)'],[/^(\d+) pending$/,'$1 en attente'],[/^(\d+)\/(\d+) tasks$/,'$1/$2 tâches'],[/^(\d+) tasks?$/,'$1 tâche(s)'],[/^Digest of (.*)$/,'Synthèse du $1'],
];
const VA_SUB=[[/\b(\d+)d late\b/g,'$1 j de retard'],[/\b(\d+) days\b/g,'$1 jours'],[/\b(\d+) papers\b/g,'$1 articles'],[/\bCapacity\b/g,'Capacité']];
const VA_SKIP_TAGS=new Set(['SCRIPT','STYLE','TEXTAREA','CODE','PRE','CANVAS','svg','SVG','text','tspan','MATH']);
const VA_SKIP_SEL='.rd-md,.sk-res,.katex,.rd-ct,.rd-cs,.rd-t,.rd-au,.rd-refs,.no-tr,[contenteditable="true"],#memText';
function vaTrText(s){
  const m=String(s).match(/^(\s*)([\s\S]*?)(\s*)$/); const core=m[2].replace(/\s+/g,' ');
  if(!core||!/[A-Za-z]/.test(core)) return s;
  let t=VA_FR[core];
  if(t===undefined){ for(const [re,rep] of VA_PAT){ if(re.test(core)){ t=core.replace(re,rep); break; } } }
  if(t!==undefined) return m[1]+t+m[3];
  let x=m[2]; if(x.length>6){ for(const [en,fr] of VA_FRAG){ if(x.indexOf(en)>=0) x=x.split(en).join(fr); } for(const [re,rep] of VA_SUB) x=x.replace(re,rep); }
  return x===m[2]?s:m[1]+x+m[3];
}
function vaSkip(el){ for(let e=el;e&&e!==document.body;e=e.parentElement){ if(VA_SKIP_TAGS.has(e.tagName)) return true; } return !!(el&&el.closest&&el.closest(VA_SKIP_SEL)); }
function vaTrNode(n){
  if(n.nodeType===3){ const p=n.parentElement; if(!p||vaSkip(p)) return; const v=n.nodeValue, t=vaTrText(v); if(t!==v) n.nodeValue=t; return; }
  if(n.nodeType!==1||vaSkip(n)) return;
  for(const a of ['placeholder','title','aria-label']){ const v=n.getAttribute&&n.getAttribute(a); if(v){ const t=vaTrText(v); if(t!==v) n.setAttribute(a,t); } }
  const w=document.createTreeWalker(n,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
  let c; while((c=w.nextNode())){
    if(c.nodeType===3){ const p=c.parentElement; if(!p||vaSkip(p)) continue; const v=c.nodeValue, t=vaTrText(v); if(t!==v) c.nodeValue=t; }
    else for(const a of ['placeholder','title','aria-label']){ const v=c.getAttribute(a); if(v){ const t=vaTrText(v); if(t!==v) c.setAttribute(a,t); } }
  }
}
function vaLangToggle(){ try{ localStorage.setItem('va_lang',VA_LANG==='fr'?'en':'fr'); }catch(e){} location.reload(); }
(function(){
  const go=()=>{
    const b=document.getElementById('langbtn'); if(b){ b.textContent=VA_LANG==='fr'?'EN':'FR'; b.title=VA_LANG==='fr'?'Switch to English':'Passer en français'; }
    if(VA_LANG!=='fr') return;
    document.documentElement.lang='fr';
    const _tld=Date.prototype.toLocaleDateString; Date.prototype.toLocaleDateString=function(loc,opt){ return _tld.call(this,'fr-CA',opt); };
    vaTrNode(document.body); document.title=vaTrText(document.title);
    new MutationObserver(ms=>{ for(const m of ms){
      if(m.type==='characterData') vaTrNode(m.target);
      else if(m.type==='attributes') { const v=m.target.getAttribute(m.attributeName); if(v){ const t=vaTrText(v); if(t!==v) m.target.setAttribute(m.attributeName,t); } }
      else m.addedNodes.forEach(vaTrNode);
    } }).observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['placeholder','title','aria-label']});
  };
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',go); else go();
})();

function rdKatex(){ if(window.katex) return Promise.resolve(); if(window._rdKx) return window._rdKx;
  window._rdKx=new Promise(res=>{ const base='https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/';
    if(!document.querySelector('link[href*="katex.min.css"]')){ const l=document.createElement('link'); l.rel='stylesheet'; l.href=base+'katex.min.css'; document.head.appendChild(l); }
    const s=document.createElement('script'); s.src=base+'katex.min.js'; s.onload=res; s.onerror=res; document.head.appendChild(s); });
  return window._rdKx; }
const MX_MACROS={'\\Mon':'\\operatorname{Mon}','\\tot':'\\operatorname{tot}','\\NN':'\\mathcal{N}','\\Aff':'\\operatorname{Aff}^{+}(\\R)',
 '\\HH':'H','\\FFF':'\\mathcal{F}','\\Bl':'\\operatorname{Bl}','\\Pair':'\\operatorname{Pair}','\\aff':'\\mathfrak{aff}(\\R)','\\A':'\\mathcal{A}',
 '\\g':'\\mathfrak{g}','\\m':'\\mathfrak{m}','\\a':'\\mathfrak{a}','\\K':'\\mathcal{K}','\\L':'\\mathcal{L}','\\P':'P_\\infty','\\N':'\\mathbb{N}',
 '\\Z':'\\mathbb{Z}','\\b':'\\mathfrak{b}','\\Q':'\\mathbb{Q}','\\R':'\\mathbb{R}','\\G':'\\mathcal{G}','\\C':'\\mathcal{C}','\\calM':'\\mathcal{M}',
 '\\tto':'\\rightrightarrows','\\spn':'\\operatorname{span}','\\sss':'\\mathsf{s}','\\ccc':'\\mathsf{c}','\\ttt':'\\mathsf{t}','\\mtoo':'\\longmapsto',
 '\\mmm':'\\mathrm{m}','\\MC':'MC','\\HHH':'\\mathcal{H}','\\ad':'\\operatorname{ad}','\\E':'\\mathcal{E}','\\D':'\\mathcal{D}','\\T':'\\mathcal{T}',
 '\\h':'\\mathfrak{h}','\\Der':'\\operatorname{Der}','\\ppp':'\\operatorname{pr}','\\im':'\\operatorname{im}','\\defn':'\\textbf{#1}',
 '\\courant':'\\left\\llbracket #1 \\right\\rrbracket','\\Ad':'\\mathrm{Ad}','\\Alt':'\\operatorname{Alt}','\\wt':'\\operatorname{wt}',
 '\\Hom':'\\operatorname{Hom}','\\Ker':'\\operatorname{Ker}','\\Imm':'\\operatorname{Im}','\\id':'\\operatorname{id}','\\rank':'\\operatorname{rank}',
 '\\Pois':'\\operatorname{Pois}','\\sll':'/\\!\\!/_{#1}','\\sslash':'/\\!\\!/'};
const MX_SKIP_TAGS=new Set(['SCRIPT','STYLE','TEXTAREA','INPUT','SELECT','OPTION','CODE','PRE','KBD','SAMP','svg','SVG','MATH','TITLE','NOSCRIPT','IFRAME']);
const MX_SKIP_SEL='.katex,.katex-display,.mx,.no-math,[contenteditable="true"],[contenteditable=""],[contenteditable="plaintext-only"]';
let MX_Q=[], MX_T=0;
function mxPatch(){ if(!window.katex||katex._vaPatched) return; const o=katex.renderToString, r=katex.render;
  const opt=x=>Object.assign({throwOnError:false,strict:'ignore',trust:false},x||{},{macros:Object.assign({},MX_MACROS,(x&&x.macros)||{})});
  katex.renderToString=(s,x)=>o.call(katex,s,opt(x)); katex.render=(s,el,x)=>r.call(katex,s,el,opt(x)); katex._vaPatched=true; }
function mxSkip(el){ for(let e=el;e&&e!==document.body;e=e.parentElement){ if(MX_SKIP_TAGS.has(e.tagName)) return true; if(e.matches&&e.matches(MX_SKIP_SEL)) return true; } return false; }
function mxSplit(t){
  const out=[]; let i=0, last=0; const n=t.length;
  const push=(a,b,m,d)=>{ if(a>last) out.push(t.slice(last,a)); out.push({m:m,d:d,s:t.slice(a,b)}); i=b; last=b; };
  while(i<n){ const c=t[i];
    if(c==='\\'){ const nx=t[i+1];
      if(nx==='('||nx==='['){ const cl=nx==='('?'\\)':'\\]'; const j=t.indexOf(cl,i+2); if(j>i+2&&j-i<4000){ push(i,j+2,t.slice(i+2,j),nx==='['); continue; } }
      i+=2; continue; }
    if(c==='$'){
      if(t[i+1]==='$'){ const j=t.indexOf('$$',i+2); if(j>i+2&&j-i<4000){ push(i,j+2,t.slice(i+2,j),true); continue; } i+=2; continue; }
      const nx=t[i+1];
      if(nx&&!/\s/.test(nx)){ let j=i+1, found=-1;
        while(j<n&&j-i<600){ const cj=t[j];
          if(cj==='\\'){ j+=2; continue; }
          if(cj==='\n'&&t[j+1]==='\n') break;
          if(cj==='$'){ if(!/\s/.test(t[j-1])&&!/\d/.test(t[j+1]||'')&&t[j+1]!=='$'){ found=j; break; } if(/\s/.test(t[j-1])) break; }
          j++; }
        if(found>i+1){ push(i,found+1,t.slice(i+1,found),false); continue; } }
    }
    i++; }
  if(last<n) out.push(t.slice(last)); return out; }
function mxNode(tn){ const t=tn.nodeValue; if(!t) return; const parts=mxSplit(t); if(!parts.some(p=>typeof p!=='string')) return;
  const f=document.createDocumentFragment(); let ok=false;
  for(const p of parts){ if(typeof p==='string'){ f.appendChild(document.createTextNode(p)); continue; }
    const sp=document.createElement('span'); sp.className='mx'+(p.d?' mx-d':''); sp.setAttribute('data-tex',p.s);
    try{ katex.render(p.m,sp,{displayMode:p.d,throwOnError:true}); ok=true; }catch(e){ sp.textContent=p.s; sp.className='mx mx-err'; }
    f.appendChild(sp); }
  if(ok&&tn.parentNode) tn.parentNode.replaceChild(f,tn); }
function mxScan(root){ if(!window.katex||!root) return;
  if(root.nodeType===3){ if(root.parentElement&&!mxSkip(root.parentElement)&&/\$|\\[(\[]/.test(root.nodeValue||'')) mxNode(root); return; }
  if(root.nodeType!==1||mxSkip(root)) return;
  const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{acceptNode:x=>/\$|\\[(\[]/.test(x.nodeValue||'')?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_REJECT});
  const list=[]; let x; while((x=w.nextNode())) list.push(x);
  for(const tn of list){ if(tn.isConnected&&tn.parentElement&&!mxSkip(tn.parentElement)) mxNode(tn); } }
const MX_OBS=new MutationObserver(ms=>{ for(const m of ms){ if(m.type==='childList') m.addedNodes.forEach(n=>MX_Q.push(n)); else if(m.type==='characterData') MX_Q.push(m.target); }
  if(!MX_T) MX_T=setTimeout(()=>{ MX_T=0; const q=MX_Q; MX_Q=[]; for(const n of q){ if(n.isConnected) mxScan(n); } },30); });
(function(){ const st=document.createElement('style'); st.textContent=
 '.mx .katex{font-size:1.04em}.mx-d{display:block;overflow-x:auto;overflow-y:hidden;max-width:100%}.mx-err{color:inherit}'+
 '.mx .katex span:not(.hide-tail){overflow:visible!important;text-overflow:clip!important}.mx:not(.mx-d){display:inline;white-space:nowrap}';
 document.head.appendChild(st);
 const go=()=>{ mxPatch(); if(!window.katex) return; mxScan(document.body); MX_OBS.observe(document.body,{childList:true,subtree:true,characterData:true}); };
 const start=()=>{ if(typeof rdKatex==='function') rdKatex().then(go); };
 if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start(); })();

let PJ_ID=null, PJ_NOTE=null;
const _rProjectsPj=rProjects;
rProjects=function(el){ _rProjectsPj(el);
  el.querySelectorAll('.card.proj').forEach(c=>{
    c.querySelectorAll('.desc,.dom').forEach(x=>x.remove());
    c.querySelectorAll('.chip').forEach(x=>{ if(x.closest('.stats')) return; x.remove(); });
    const m=(c.getAttribute('onclick')||'').match(/x\.id==(\d+)/); if(m){ c.setAttribute('onclick','pjOpen('+m[1]+')'); c.dataset.pid=m[1]; }
  }); };
function pjLinkify(s){
  return String(s||'')
    .replace(/(^|[^\w\/\[\-.])(arxiv:\s?)(\d{4}\.\d{4,5}(?:v\d+)?)/gi,(m,a,b,id)=>a+'['+b+id+'](https://arxiv.org/abs/'+id+')')
    .replace(/(^|[\s(,;])((?:0[7-9]|[12]\d|30)(?:0[1-9]|1[0-2])\.\d{4,5}(?:v\d+)?)(?=$|[\s),;.:])/g,(m,a,id)=>a+'['+id+'](https://arxiv.org/abs/'+id+')')
    .replace(/(^|[^\w\/\[\-.])(doi:\s?)?(10\.\d{4,9}\/[^\s)\]>,;]+[^\s)\]>,;.])/gi,(m,a,b,d)=>a+'['+(b||'')+d+'](https://doi.org/'+d+')'); }
function pjClean(d){ const m=String(d||'').match(/\n\s*---\s*\n?\s*(Created by Architect[\s\S]*)$/); return {body:m?String(d).slice(0,m.index):String(d||''), foot:m?m[1].trim():''}; }
function pjRefs(html){ const box=document.createElement('div'); box.innerHTML=html; const seen=new Set(), out=[];
  box.querySelectorAll('a[href^="http"]').forEach(a=>{ const u=a.getAttribute('href'); if(seen.has(u)) return; seen.add(u); out.push({u, t:a.textContent.trim()||u}); }); return out; }
function pjOpen(id){ PJ_ID=+id; PJ_NOTE=null; pjPaint(); }
function pjClose(){ PJ_ID=null; PJ_NOTE=null; const w=document.getElementById('pjw'); if(w) w.remove(); document.removeEventListener('keydown',pjKey); }
function pjKey(e){ if(e.key==='Escape'&&!document.getElementById('ovl').classList.contains('show')&&getComputedStyle(document.getElementById('ovl')).display==='none') pjClose(); }
function pjNote(id){ PJ_NOTE=id; pjPaint(); }
function pjPaint(){
  const p=(S.projects||[]).find(x=>x.id==PJ_ID); if(!p){ pjClose(); return; }
  const pt=(S.tasks||[]).filter(t=>t.project_id==p.id), dn=pt.filter(t=>t.status==='Done').length, pc=pt.length?Math.round(100*dn/pt.length):0;
  const notes=(S.notes||[]).filter(n=>n.project_id==p.id);
  const d=p.deadline?daysTo(p.deadline):null;
  const {body,foot}=pjClean(p.description);
  let main, back='';
  if(PJ_NOTE){ const n=notes.find(x=>x.id==PJ_NOTE); back='<button class="btn ghost pj-back" onclick="pjNote(null)">← Project</button>';
    main=n?'<h2 class="pj-h2">'+esc(n.title)+'</h2><div class="pj-md">'+skMd(pjLinkify(n.content||''))+'</div>':'<div class="empty">Note not found</div>'; }
  else main=body.trim()?'<div class="pj-md">'+skMd(pjLinkify(body))+'</div>'+(foot?'<div class="pj-foot">'+esc(foot)+'</div>':''):'<div class="empty">No description yet — click “Edit” to write the motivation.</div>';
  const refs=pjRefs(main);
  const order=['In Progress','To Do','In Review','Done'];
  const tasks=pt.slice().sort((a,b)=>order.indexOf(a.status)-order.indexOf(b.status)||String(a.planned_date||'').localeCompare(String(b.planned_date||'')));
  const stc={'Done':'var(--teal)','In Progress':'var(--acc)','In Review':'var(--amber)','To Do':'var(--dim)'};
  const side='<div class="pj-box"><div class="pj-bt">References'+(refs.length?' <span>'+refs.length+'</span>':'')+'</div>'+
      (refs.length?refs.map(r=>'<a class="pj-ref" href="'+esc(r.u)+'" target="_blank" rel="noopener">'+esc(r.t)+' <i>↗</i><small>'+esc(r.u.replace(/^https?:\/\//,'').slice(0,60))+'</small></a>').join(''):'<div class="demo-note">No reference link in this text yet.</div>')+'</div>'+
    (notes.length?'<div class="pj-box"><div class="pj-bt">Notes <span>'+notes.length+'</span></div>'+notes.map(n=>'<div class="pj-li'+(n.id==PJ_NOTE?' on':'')+'" onclick="pjNote('+n.id+')"><span>'+esc(n.title)+'</span></div>').join('')+'</div>':'')+
    '<div class="pj-box"><div class="pj-bt">Tasks <span>'+dn+'/'+pt.length+'</span></div>'+
      (tasks.length?tasks.map(t=>'<div class="pj-li" onclick="editTask(S.tasks.find(x=>x.id=='+t.id+'))"><i style="background:'+(stc[t.status]||'var(--dim)')+'"></i><span'+(t.status==='Done'?' style="text-decoration:line-through;opacity:.6"':'')+'>'+esc(t.title)+'</span></div>').join(''):'<div class="demo-note">No task yet.</div>')+'</div>';
  let w=document.getElementById('pjw');
  if(!w){ w=document.createElement('div'); w.id='pjw'; w.onclick=e=>{ if(e.target===w) pjClose(); }; document.body.appendChild(w); document.addEventListener('keydown',pjKey); }
  const keep=w.querySelector('.pj-main'); const sc=keep?keep.scrollTop:0;
  w.innerHTML='<div class="pj-win"><div class="pj-head"><span class="dot" style="background:'+projColor(p.id)+';width:12px;height:12px"></span>'+
    '<div style="flex:1;min-width:0"><h1 class="pj-h1">'+esc(p.title)+'</h1><div class="pj-meta">'+
      (p.domain?'<span>'+esc(p.domain)+'</span>':'')+'<span class="chip" style="background:var(--panel3);color:var(--mut)">'+esc(p.status||'')+'</span>'+
      (p.deadline?'<span style="color:'+(d<0?'var(--red)':d<=14?'var(--amber)':'var(--mut)')+'">⏱ '+fmtdy(p.deadline)+'</span>':'')+
      '<span class="pj-prog"><span class="pbar"><i style="width:'+pc+'%"></i></span>'+pc+'% · '+dn+'/'+pt.length+' tasks</span></div></div>'+
    '<div class="pj-acts"><button class="btn ghost" onclick="PROJFILTER='+p.id+';pjClose();setView(\'board\')">Board →</button>'+
      '<button class="btn ghost" onclick="editProject(S.projects.find(x=>x.id=='+p.id+'))">Edit</button>'+
      '<button class="btn ghost pj-x" onclick="pjClose()" title="Close (Esc)">✕</button></div></div>'+
    '<div class="pj-body"><div class="pj-main">'+back+main+'</div><div class="pj-side">'+side+'</div></div></div>';
  const nm=w.querySelector('.pj-main'); if(nm&&!PJ_NOTE) nm.scrollTop=sc; }
const _renderPj=render;
render=function(){ const r=_renderPj.apply(this,arguments); try{ if(PJ_ID) pjPaint(); }catch(e){} return r; };
(function(){ const st=document.createElement('style'); st.textContent=
 '.proj h4{font-size:15.5px;line-height:1.35}'+
 '#pjw{position:fixed;inset:0;z-index:45;background:var(--ovlc,rgba(0,0,0,.35));backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px);display:flex;align-items:center;justify-content:center;padding:3vh 3vw}'+
 '.pj-win{width:min(1240px,100%);height:min(94vh,100%);background:var(--panel);border:1px solid var(--line2);border-radius:20px;box-shadow:var(--sh3);display:flex;flex-direction:column;overflow:hidden;color:var(--txt)}'+
 '.pj-head{display:flex;gap:14px;align-items:flex-start;padding:22px 26px 16px;border-bottom:1px solid var(--line)}.pj-head .dot{margin-top:11px}'+
 '.pj-h1{font-size:24px;line-height:1.3;margin:0 0 8px;font-weight:750;letter-spacing:-.01em}'+
 '.pj-meta{display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:13.5px;color:var(--mut)}.pj-prog{display:flex;gap:8px;align-items:center}.pj-prog .pbar{width:120px}'+
 '.pj-acts{display:flex;gap:6px;flex:none}.pj-x{font-size:15px;padding:6px 12px}'+
 '.pj-body{flex:1;display:grid;grid-template-columns:minmax(0,1fr) 340px;min-height:0}'+
 '.pj-main{overflow-y:auto;padding:24px 34px 40px}.pj-side{overflow-y:auto;padding:20px 20px 30px;border-left:1px solid var(--line);background:color-mix(in srgb,var(--panel2) 70%,transparent)}'+
 '.pj-md{font-size:16.5px;line-height:1.72;color:var(--txt);max-width:820px}.pj-md>div[style*="font-weight:700"]{font-size:20px!important;margin:22px 0 8px!important;color:var(--txt)}'+
 '.pj-md li{margin:4px 0}.pj-md a{color:var(--acc);text-decoration:underline;text-underline-offset:2px;font-weight:600}.pj-md .katex{font-size:1.06em}'+
 '.pj-h2{font-size:20px;margin:6px 0 14px}.pj-back{margin-bottom:14px}.pj-foot{margin-top:24px;padding-top:12px;border-top:1px solid var(--line);font-size:13px;color:var(--mut)}'+
 '.pj-box{margin-bottom:20px}.pj-bt{font-size:12.5px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin-bottom:8px}.pj-bt span{font-weight:700;color:var(--acc);margin-left:4px}'+
 '.pj-ref{display:block;padding:8px 10px;margin-bottom:6px;border-radius:10px;background:var(--panel);border:1px solid var(--line);color:var(--txt);text-decoration:none;font-size:14px;font-weight:600;line-height:1.35}'+
 '.pj-ref:hover{border-color:var(--acc)}.pj-ref i{font-style:normal;color:var(--acc)}.pj-ref small{display:block;font-weight:400;font-size:11.5px;color:var(--mut);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'+
 '.pj-li{display:flex;gap:8px;align-items:flex-start;padding:7px 8px;border-radius:8px;font-size:14px;line-height:1.4;cursor:pointer}.pj-li:hover,.pj-li.on{background:var(--accbg)}'+
 '.pj-li i{flex:none;width:8px;height:8px;border-radius:50%;margin-top:6px}'+
 '@media(max-width:900px){.pj-body{grid-template-columns:1fr}.pj-side{border-left:0;border-top:1px solid var(--line)}.pj-main{padding:18px}.pj-head{flex-wrap:wrap}}';
 document.head.appendChild(st); })();
Object.assign(VA_FR,{"References": "R\u00e9f\u00e9rences", "\u2190 Project": "\u2190 Projet", "No reference link in this text yet.": "Aucun lien de r\u00e9f\u00e9rence dans ce texte pour l\u2019instant.", "No task yet.": "Aucune t\u00e2che pour l\u2019instant."});

const Q_CATS=['Mathematics','Research','Science','Philosophy','Discipline','Writing','Motivation','Other'];
const Q_FALLBACK=[{text:'Mathematics is the art of giving the same name to different things.',author:'Henri Poincaré'},{text:'The important thing is not to stop questioning.',author:'Albert Einstein'},{text:'What I cannot create, I do not understand.',author:'Richard Feynman'}];
let DASH_Q=null, QM_Q='', QM_CAT='', QM_TIMER=null;
function qPool(){ const p=(S.quotes||[]).filter(q=>!+q.hidden&&q.text); return p.length?p:Q_FALLBACK; }
function qPick(used,maxLen){ const pool=qPool(); let c=pool.filter(q=>!used.has(q.id||q.text)&&(!maxLen||q.text.length<=maxLen)); if(!c.length) c=pool.filter(q=>!used.has(q.id||q.text)); if(!c.length) c=pool;
  const w=q=>+q.fav?4:1; let r=Math.random()*c.reduce((a,q)=>a+w(q),0); for(const q of c){ r-=w(q); if(r<=0) return q; } return c[c.length-1]; }
function qResolve(x){ if(x&&x.id){ const q=(S.quotes||[]).find(y=>y.id===x.id); if(q&&!+q.hidden) return q; } return x&&!x.id?x:null; }
function dashQuotes(){
  if(DASH_Q&&Date.now()-DASH_Q.t<30*60000){ const r=DASH_Q.q.map(qResolve); if(r.every(Boolean)) return r; }
  const used=new Set(), out=[];
  [220,100,0].forEach(m=>{ const q=qPick(used,m); used.add(q.id||q.text); out.push(q); });
  DASH_Q={t:Date.now(),q:out}; return out;
}
function qNext(i){ const cur=dashQuotes(), used=new Set(cur.map(q=>q.id||q.text)); DASH_Q.q[i]=qPick(used,[220,100,0][i]); render(); }
async function qFav(id){ const q=(S.quotes||[]).find(x=>x.id===id); if(!q) return; await mutate('PUT','quotes',id,{fav:+q.fav?0:1}); await load(); toast(+q.fav?'Removed from favourites':'Added to favourites — it will appear more often'); qmRender(); }
async function qHide(id,h){ await mutate('PUT','quotes',id,{hidden:h?1:0}); if(h&&DASH_Q) DASH_Q.q=DASH_Q.q.map(x=>x&&x.id===id?qPick(new Set(DASH_Q.q.map(y=>y.id||y.text)),0):x); await load(); toast(h?'Quote hidden — it will not be shown again':'Quote shown again'); qmRender(); }
function qActs(q,i){ return '<span class="vq-acts"><button title="Another quote" onclick="event.stopPropagation();qNext('+i+')">↻</button>'+(q.id?'<button title="Favourite" onclick="event.stopPropagation();qFav('+q.id+')">'+(+q.fav?'★':'☆')+'</button><button title="Do not show again" onclick="event.stopPropagation();qHide('+q.id+',1)">✕</button>':'')+'<button title="All quotes" onclick="event.stopPropagation();qManage()">☰</button></span>'; }
function editQuote(q){ q=q||{category:'Research'};
  modal(q.id?'Edit quote':'Add quote',[{k:'text',label:'Quote',type:'textarea',rows:4},{k:'author',label:'Author',half:true},{k:'category',label:'Category',type:'select',options:Q_CATS,def:'Research',half:true}],q,
    async o=>{ if(!String(o.text||'').trim()){ toast('The quote is empty'); return; } if(!q.id){ o.source='Mine'; o.fav=0; o.hidden=0; } await save('quotes',o,q.id); toast('Quote saved'); qmRender(); },
    q.id?()=>del('quotes',q.id).then(()=>{ toast('Quote deleted'); qmRender(); }):null); }
async function qCollect(){ try{ await api('GET','/api/quotes/collect'); }catch(e){ toast('VanoAtlas server unreachable'); return; }
  toast('Collecting quotes online (Wikiquote + ZenQuotes)…'); qmStatus(); clearInterval(QM_TIMER);
  QM_TIMER=setInterval(async()=>{ let s={}; try{ s=await api('GET','/api/quotes/status'); }catch(e){} qmStatus(s); if(!s.running){ clearInterval(QM_TIMER); await load(); qmRender(); toast(s.error?('No new quotes: '+s.error.slice(0,90)):(s.msg||'Done')); } },2500); }
async function qmStatus(s){ if(!s){ try{ s=await api('GET','/api/quotes/status'); }catch(e){ s={}; } } const e=document.getElementById('qmStat'); if(e) e.innerHTML=(s.running?'⏳ ':'')+esc(s.msg||'')+(s.last?' · last collection '+esc(String(s.last).replace('T',' ').slice(0,16)):'')+(s.error?' · <span style="color:var(--pink)">'+esc(s.error).slice(0,140)+'</span>':''); }
function qManage(){ let o=document.getElementById('qovl'); if(!o){ o=document.createElement('div'); o.id='qovl'; o.onclick=e=>{ if(e.target===o) qmClose(); }; document.body.appendChild(o); }
  o.innerHTML='<div class="qm-box"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><h3 style="margin:0">Quotes <span class="demo-note" id="qmCount"></span></h3><button class="btn ghost" onclick="qmClose()">✕</button></div>'+
   '<div class="demo-note" style="margin:4px 0 10px">Shown on the dashboard, changing at each visit (↻ to change now). ★ favourites appear more often. New quotes are collected online every week from Wikiquote (mathematicians, scientists, philosophers) and ZenQuotes.</div>'+
   '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center"><input id="qmQ" placeholder="Search a quote or an author…" value="'+esc(QM_Q)+'" oninput="QM_Q=this.value;qmRender()" style="flex:1;min-width:180px;padding:8px 11px;border:1px solid var(--line);border-radius:10px;background:var(--panel2);color:var(--txt)">'+
   '<select class="btn ghost" onchange="QM_CAT=this.value;qmRender()"><option value="">All categories</option><option value="★">★ Favourites</option><option value="hidden">Hidden</option>'+Q_CATS.map(c=>'<option'+(QM_CAT===c?' selected':'')+'>'+c+'</option>').join('')+'</select>'+
   '<button class="btn ghost" onclick="editQuote()">+ Add quote</button><button class="btn" onclick="qCollect()">Collect online now</button></div>'+
   '<div class="demo-note" id="qmStat" style="margin-top:8px"></div><div id="qmList" class="qm-list"></div></div>';
  o.classList.add('show'); qmRender(); qmStatus(); }
function qmClose(){ const o=document.getElementById('qovl'); if(o) o.classList.remove('show'); clearInterval(QM_TIMER); }
function qmRender(){ const L=document.getElementById('qmList'); if(!L) return; const all=S.quotes||[], q=QM_Q.trim().toLowerCase();
  const items=all.filter(x=>(QM_CAT==='hidden'?+x.hidden:!+x.hidden)&&(QM_CAT===''||QM_CAT==='hidden'||(QM_CAT==='★'?+x.fav:x.category===QM_CAT))&&(!q||(x.text+' '+x.author).toLowerCase().includes(q)))
    .sort((a,b)=>(+b.fav-+a.fav)||String(b.created_at||'').localeCompare(String(a.created_at||'')));
  const c=document.getElementById('qmCount'); if(c) c.textContent='· '+all.filter(x=>!+x.hidden).length+' active';
  L.innerHTML=items.slice(0,400).map(x=>'<div class="qm-row"><div style="flex:1;min-width:0"><div class="qm-t">“'+esc(x.text)+'”</div><div class="demo-note">— '+esc(x.author||'Unknown')+' · '+esc(x.category||'')+' · '+esc(x.source||'')+'</div></div>'+
    '<div class="qm-a"><button class="btn ghost" title="Favourite" onclick="qFav('+x.id+')">'+(+x.fav?'★':'☆')+'</button><button class="btn ghost" title="'+(+x.hidden?'Show again':'Hide')+'" onclick="qHide('+x.id+','+(+x.hidden?0:1)+')">'+(+x.hidden?'↺':'✕')+'</button><button class="btn ghost" title="Edit" onclick="editQuote(byId(\'quotes\','+x.id+'))">✎</button></div></div>').join('')||'<div class="empty">No quote here.</div>'; }
(function(){ const st=document.createElement('style'); st.textContent=
 '.vq-a{opacity:.8;font-style:normal;white-space:nowrap}.vq-acts{display:inline-flex;gap:2px;margin-left:6px;opacity:.35;transition:opacity .15s;vertical-align:middle}'+
 '.hero-copy p:hover .vq-acts,.quote-card:hover .vq-acts,.hero-date:hover .vq-acts{opacity:1}'+
 '.vq-acts button{background:transparent;border:0;color:inherit;cursor:pointer;font-size:13px;padding:1px 4px;border-radius:6px}.vq-acts button:hover{background:rgba(127,127,127,.18)}'+
 '.quote-card .vq-acts{position:absolute;top:14px;right:12px;margin:0}'+
 '#qovl{position:fixed;inset:0;z-index:48;display:none;align-items:center;justify-content:center;background:var(--ovlc,rgba(0,0,0,.4));backdrop-filter:blur(3px)}#qovl.show{display:flex}'+
 '.qm-box{width:min(820px,94vw);max-height:88vh;display:flex;flex-direction:column;background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:var(--sh2)}'+
 '.qm-list{margin-top:10px;overflow:auto;flex:1}.qm-row{display:flex;gap:10px;align-items:flex-start;padding:9px 4px;border-top:1px solid var(--line)}'+
 '.qm-t{font-size:13.5px;line-height:1.45}.qm-a{display:flex;gap:4px}.qm-a .btn{padding:3px 8px;font-size:12px}';
 document.head.appendChild(st); })();
{ const _qSetView=setView; setView=function(v){ if(v==='dash'&&VIEW!=='dash') DASH_Q=null; return _qSetView(v); }; }

let VL_SUM=null, VL_TAB=localStorage.getItem('m2_vltab')||'overview', VL_OPEN='', VL_Q='', VL_RES=null, VL_DEEP=false, VL_BUSY=false;
async function vlRefresh(rerender){
  try{ VL_SUM=await api('GET','/api/vanolib/summary'); }catch(e){ VL_SUM={available:false,error:'VanoAtlas server unreachable'}; }
  if(rerender&&(VIEW==='vanolib'||VIEW==='dash'||VIEW==='review')) render();
}
const vlApi=(n,p)=>api('POST','/vanolib/api/'+n,p||{});
function vlShortAuthors(a){ const p=String(a||'').split(',').map(s=>s.trim()).filter(Boolean); return p.length>2?p[0]+' et al.':p.join(', '); }
async function vlToBib(uid){
  const a=await vlApi('article',{uid}); if(!a||!a.uid){ toast('Paper not found in VanoLib'); return; }
  const aid=String(a.ext_id||'').replace(/v\d+$/,'');
  if((S.refs||[]).some(r=>aid&&r.arxiv_id===aid)){ toast('Already in Bibliography'); return; }
  await mutate('POST','refs',null,{title:a.title,authors:a.authors,year:a.year,arxiv_id:aid,url:a.link||('https://arxiv.org/abs/'+aid),abstract:a.abstract||'',status:a.progress>=97?'Read':a.progress>0?'Reading':'To read',tags:a.category||'',bibkey:makeBibKey(a.authors,a.year)});
  await load(); toast('Added to Bibliography');
}
async function vlToTask(uid){
  const a=await vlApi('article',{uid}); if(!a||!a.uid) return;
  const left=a.pages&&a.page?Math.max(15,Math.round((a.pages-a.page)*4)):60;
  await mutate('POST','tasks',null,{title:'Read: '+a.title,task_type:'Reading',status:'To Do',priority:'Normal',estimate_min:Math.min(240,left),tags:'vanolib',description:'VanoLib · '+(a.link||'')+'\nvanolib:'+uid});
  await load(); toast('Reading task created');
}
async function vlToggleRead(uid){ const r=await vlApi('toggle_read',{uid}); toast(r.to_read?'Added to VanoLib To Read':'Removed from To Read'); vlRefresh(true); if(VL_RES){ const it=VL_RES.find(x=>x.uid===uid); if(it){ it.to_read=r.to_read; render(); } } }
async function vlOpenPdf(uid){
  toast('Preparing PDF…'); const w=window.open('about:blank','_blank');
  const r=await vlApi('ensure_pdf',{uid});
  if(r&&r.ok){ const u='/vanolib/pdf/'+encodeURIComponent(uid); if(w) w.location=u; else location.href=u; }
  else { if(w) w.close(); toast((r&&r.error)||'PDF unavailable'); }
}
function vlOpenReader(uid){ VL_TAB='full'; VL_OPEN=uid||''; localStorage.setItem('m2_vltab','full'); setView('vanolib'); }
async function vlOpenByArxiv(aid,fallback){
  aid=String(aid||'').replace(/v\d+$/,'');
  let r={items:[]}; try{ r=await api('GET','/api/vanolib/search?q='+encodeURIComponent(aid)+'&limit=3'); }catch(e){}
  const it=(r.items||[]).find(x=>String(x.ext_id||'').replace(/v\d+$/,'')===aid);
  if(it) vlOpenReader(it.uid); else if(fallback) window.open(fallback,'_blank'); else toast('Not in VanoLib yet — it will appear after the next sync');
}
function vlRow(a,opts){
  opts=opts||{}; const p=+a.progress||0;
  return `<div class="v6-vlrow"><div style="flex:1;min-width:0;cursor:pointer" onclick="vlOpenReader('${esc(a.uid)}')">
    <div class="v6-vlt">${esc(a.title)}</div>
    <div class="demo-note">${esc(vlShortAuthors(a.authors))} · <span class="chip" style="background:var(--panel3);color:var(--mut)">${esc(a.category||'')}</span> ${esc(String(a.published||a.year||'').slice(0,10))}${a.ext_id?' · arXiv:'+esc(String(a.ext_id).replace(/v\d+$/,'')):''}${a.has_pdf?' · 📄 PDF':''}</div>
    ${p>0?`<div style="display:flex;gap:8px;align-items:center;margin-top:6px"><div class="planner-bar" style="flex:1;max-width:260px"><i style="width:${p}%"></i></div><span class="demo-note">${p}%${a.page&&a.pages?` · p. ${a.page}/${a.pages}`:''}</span></div>`:''}</div>
   <div class="v6-acts"><button class="btn pri" onclick="vlOpenReader('${esc(a.uid)}')">📖 ${p>0?'Continue':'Read'}</button><button class="btn ghost" title="Open the PDF in a new tab" onclick="vlOpenPdf('${esc(a.uid)}')">PDF ↗</button>
    <button class="btn ghost" onclick="vlToTask('${esc(a.uid)}')">→ Task</button>
    <button class="btn ghost" title="VanoLib To Read" onclick="vlToggleRead('${esc(a.uid)}')">${a.to_read?'★':'☆'}</button></div></div>`;
}
async function vlSearch(){
  const inp=document.getElementById('vlQ'); VL_Q=(inp&&inp.value||'').trim(); if(VL_Q.length<3){ VL_RES=null; render(); return; }
  VL_BUSY=true; render(); const t0=performance.now();
  try{ const r=await api('GET','/api/vanolib/search?q='+encodeURIComponent(VL_Q)+'&limit=40'+(VL_DEEP?'&deep=1':'')); VL_RES=r.items||[]; VL_RES.ms=Math.round(performance.now()-t0); }catch(e){ VL_RES=[]; }
  VL_BUSY=false; render();
}
function rVanolib(el){
  const tabs=`<div style="display:flex;gap:6px">${[['overview','Overview'],['full','Full library & reader']].map(([k,l])=>`<button class="btn ghost${VL_TAB===k?' on':''}" onclick="VL_TAB='${k}';localStorage.setItem('m2_vltab','${k}');${k==='overview'?"VL_OPEN='';vlRefresh(true);":''}render()">${l}</button>`).join('')}<button class="btn ghost" title="Open VanoLib in its own tab" onclick="window.open('/vanolib/','_blank')">↗</button></div>`;
  if(VL_TAB==='full'){
    const src='/vanolib/'+(VL_OPEN?'?open='+encodeURIComponent(VL_OPEN):'');
    const ex=el.querySelector('#vlFrame'); if(ex&&ex.dataset.src===src) return;
    el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">VanoLib</h2><div class="demo-note">Your arXiv library with the PDF reader, highlights and notes — same data as the standalone app.</div></div>${tabs}</div>
     <iframe id="vlFrame" class="v6-vlframe" src="${src}" data-src="${src}" allow="fullscreen; clipboard-write" title="VanoLib"></iframe>`;
    return;
  }
  const s=VL_SUM;
  if(!s){ el.innerHTML='<div class="empty">Loading VanoLib…</div>'; vlRefresh(true); return; }
  if(!s.available){ el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">VanoLib</h2></div>${tabs}</div><div class="panel"><div class="empty">${esc(s.error||'VanoLib unavailable')}<br><span class="demo-note">VanoAtlas looks for VanoLib in <code>${esc(s.dir||'VanoLib')}</code>.</span><br><button class="btn ghost" style="margin-top:10px" onclick="VL_SUM=null;vlRefresh(true)">Retry</button></div></div>`; return; }
  const wk=s.week||[], mx=Math.max(1,...wk.map(d=>d.pages));
  const syncTxt=s.sync&&s.sync.running?'Syncing arXiv… '+esc(s.sync.msg||''):(s.last_sync?'Synced '+fmtdy(s.last_sync.slice(0,10)):'Never synced');
  el.innerHTML=`<div class="chart-head"><div><h2 style="font-size:19px">VanoLib</h2><div class="demo-note">${Number(s.total).toLocaleString('en-US')} arXiv papers on your Mac · ${syncTxt} · <a href="#" onclick="event.preventDefault();vlApi('sync',{force:true}).then(()=>{toast('VanoLib sync started');setTimeout(()=>vlRefresh(true),3000)})">sync now</a></div></div>${tabs}</div>
   <div class="kpis">${kpi(s.in_progress,'Reading now','var(--acc2)')}${kpi(s.to_read,'In To Read','var(--amber)')}${kpi(s.pages_week,'Pages read this week','var(--green)')}${kpi(s.pdfs,'PDFs downloaded','var(--teal)')}${kpi(Number(s.new_week).toLocaleString('en-US'),'New papers this week','var(--pink)')}</div>
   <div class="panel" style="margin-bottom:14px"><div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
     <input id="vlQ" style="flex:1;min-width:260px" value="${esc(VL_Q)}" placeholder="Search ${Number(s.total).toLocaleString('en-US')} papers — title, author or arXiv id (Enter)" onkeydown="if(event.key==='Enter')vlSearch()">
     <label class="demo-note" style="display:flex;gap:6px;align-items:center"><input type="checkbox" ${VL_DEEP?'checked':''} onchange="VL_DEEP=this.checked"> include abstracts (slower)</label>
     <button class="btn pri" onclick="vlSearch()">${VL_BUSY?'Searching…':'Search'}</button>${VL_RES?`<button class="btn ghost" onclick="VL_RES=null;VL_Q='';render()">Clear</button>`:''}</div>
     ${VL_RES?`<div class="demo-note" style="margin:10px 0 2px">${VL_RES.length} result${VL_RES.length===1?'':'s'}${VL_RES.ms!=null?' · '+VL_RES.ms+' ms':''}</div>${VL_RES.map(a=>vlRow(a)).join('')||'<div class="empty" style="padding:18px">No match.</div>'}`:''}</div>
   <div class="v6-res">
    <div><div class="panel" style="margin-bottom:14px"><div style="font-weight:650;font-size:13px;margin-bottom:4px">Continue reading</div>${(s.continue||[]).map(a=>vlRow(a)).join('')||'<div class="empty" style="padding:18px">Nothing in progress.</div>'}</div>
     <div class="panel"><div style="font-weight:650;font-size:13px;margin-bottom:4px">To Read queue</div>${(s.queue||[]).map(a=>vlRow(a)).join('')||'<div class="empty" style="padding:18px">Queue empty — star papers with ☆.</div>'}</div></div>
    <div><div class="panel" style="margin-bottom:14px"><div style="font-weight:650;font-size:13px;margin-bottom:10px">Pages read · last 7 days</div><div class="v6-vlweek">${wk.map(d=>`<div><i style="height:${Math.round(d.pages/mx*100)}%" title="${d.pages} pages"></i><span>${esc(d.d)}</span></div>`).join('')}</div><div class="demo-note" style="margin-top:6px">${s.pages_week} pages · ${s.highlights} highlights saved</div></div>
     <div class="panel"><div style="font-weight:650;font-size:13px;margin-bottom:4px">Latest in your pinned categories</div>${(s.feed||[]).slice(0,10).map(a=>vlRow(a)).join('')||'<div class="empty" style="padding:18px">—</div>'}</div></div>
   </div>`;
}
let VL_PAL_T=null;
function vlPaletteSearch(q){
  clearTimeout(VL_PAL_T); if(!VL_SUM||!VL_SUM.available||q.length<3||/^[+>]/.test(q)) return;
  VL_PAL_T=setTimeout(async()=>{
    let r; try{ r=await api('GET','/api/vanolib/search?q='+encodeURIComponent(q)+'&limit=6'); }catch(e){ return; }
    const inp=document.getElementById('palin'), R=document.getElementById('palres'); if(!inp||inp.value!==q||!R||!(r.items||[]).length) return;
    const base=PAL.items.length; R.querySelectorAll('.empty').forEach(e=>e.remove());
    R.insertAdjacentHTML('beforeend','<div class="pal-head">VanoLib · '+Number(VL_SUM.total).toLocaleString('en-US')+' arXiv papers</div>'+r.items.map((a,i)=>'<div class="pal-row" data-i="'+(base+i)+'"><span class="pal-ty">paper</span><span class="pal-main">'+fuzzyMark(q,a.title)+'<small>'+esc(vlShortAuthors(a.authors))+' · '+esc(a.category||'')+' · '+esc(a.year||'')+(a.progress?' · '+a.progress+'% read':'')+'</small></span></div>').join(''));
    r.items.forEach(a=>PAL.items.push(()=>vlOpenReader(a.uid))); palMark();
  },200);
}
setTimeout(()=>vlRefresh(true),1500);
setInterval(()=>{ if(!document.hidden) vlRefresh(VIEW==='vanolib'&&VL_TAB==='overview'); },300000);

document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>setView(b.dataset.v));
document.getElementById('search').addEventListener('input',e=>{ Q=e.target.value.toLowerCase(); render(); });
document.addEventListener('keydown',e=>{
  const tag=document.activeElement.tagName;
  if(e.key==='Escape'){ if(FOCUS.running){document.body.classList.remove('zen-shell');} else {document.getElementById('focusovl').classList.remove('show');document.body.classList.remove('zen-shell');} document.getElementById('ovl').classList.remove('show'); document.activeElement.blur(); return; }
  if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT') return;
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openCommandCenter();}
  else if(e.key==='/'){ e.preventDefault(); document.getElementById('search').focus(); }
  else if(e.key==='n'){ e.preventDefault(); quickNew(); }
  else if(e.key==='t'){ e.preventDefault(); toggleTheme(); }
  else if(e.key>='1'&&e.key<='8'){ const v=['dash','board','projects','agenda','daily','analytics','matrix','notes'][+e.key-1]; if(v) setView(v); }
});

load();

(function(){
  const P={
    printemps:{shape:'petal',colors:['#f4a6c0','#f7c4d6','#e98fb0','#c5e6b8'],vy:26,sway:34,alpha:.85},
    ete:{shape:'glow',colors:['#ffd25a','#ffb347','#fff0a0'],vy:-14,sway:22,alpha:.75},
    automne:{shape:'leaf',colors:['#c8501e','#e08a1e','#a63a14','#d9a21b'],vy:34,sway:48,alpha:.85},
    hiver:{shape:'flake',colors:['#ffffff','#dbe9ff','#bcd4f5'],vy:22,sway:20,alpha:.9}
  };
  const cv=document.getElementById('seasonFx'); if(!cv) return;
  const ctx=cv.getContext('2d'), reduce=matchMedia('(prefers-reduced-motion: reduce)');
  let theme=null, W=0, H=0, parts=[], running=false, last=0;
  const rnd=(a,b)=>a+Math.random()*(b-a);
  function make(init){ const z=rnd(.35,1); return {x:rnd(0,W),y:init?rnd(0,H):(P[theme].vy<0?H+20:-20),z,size:rnd(7,15)*z,rot:rnd(0,6.28),vr:rnd(-.6,.6),ph:rnd(0,6.28),fr:rnd(.25,.6),col:Math.floor(rnd(0,4))}; }
  function resize(){ const d=Math.min(devicePixelRatio||1,2); W=innerWidth; H=innerHeight; cv.width=W*d; cv.height=H*d; ctx.setTransform(d,0,0,d,0,0);
    const n=Math.max(28,Math.min(80,Math.round(W*H/20000))); while(parts.length<n) parts.push(make(true)); parts.length=n; }
  function draw(p,T,col){ const s=p.size; ctx.fillStyle=col;
    if(T.shape==='petal'){ ctx.beginPath(); ctx.ellipse(0,0,s*.55,s,0,0,6.2832); ctx.fill(); }
    else if(T.shape==='leaf'){ ctx.beginPath(); ctx.moveTo(0,-s); ctx.quadraticCurveTo(s*.9,-s*.2,0,s); ctx.quadraticCurveTo(-s*.9,-s*.2,0,-s); ctx.fill(); }
    else if(T.shape==='glow'){ const g=ctx.createRadialGradient(0,0,0,0,0,s*1.6); g.addColorStop(0,col); g.addColorStop(1,'rgba(255,210,90,0)'); ctx.fillStyle=g; ctx.beginPath(); ctx.arc(0,0,s*1.6,0,6.2832); ctx.fill(); }
    else { ctx.beginPath(); ctx.arc(0,0,s*.38,0,6.2832); ctx.fill(); } }
  function frame(now){ if(!running||!theme) return; const dt=Math.min((now-last)/1000,.05); last=now; const k=reduce.matches?.2:1, T=P[theme];
    ctx.clearRect(0,0,W,H);
    for(const p of parts){ p.ph+=p.fr*dt*k; p.rot+=p.vr*dt*k; p.y+=T.vy*p.z*dt*k; p.x+=Math.sin(p.ph)*T.sway*p.z*dt*k;
      if(T.vy>=0?p.y>H+30:p.y<-30) Object.assign(p,make(false)); if(p.x<-30)p.x=W+30; else if(p.x>W+30)p.x=-30;
      ctx.save(); ctx.translate(p.x,p.y); ctx.rotate(T.shape==='glow'?0:p.rot);
      ctx.globalAlpha=.6*T.alpha*(.35+.65*p.z)*(T.shape==='glow'?.55+.45*Math.sin(p.ph*2):1);
      draw(p,T,T.colors[p.col%T.colors.length]); ctx.restore(); }
    requestAnimationFrame(frame); }
  function start(){ if(!running&&theme&&!document.hidden){ running=true; last=performance.now(); requestAnimationFrame(frame); } }
  function sync(){ const t=document.documentElement.dataset.theme; const on=!!P[t];
    document.documentElement.classList.toggle('season-on',on);
    if(on){ if(t!==theme){ theme=t; parts=[]; resize(); } start(); } else { theme=null; running=false; ctx.clearRect(0,0,W,H); } }
  document.addEventListener('visibilitychange',()=>{ if(document.hidden) running=false; else start(); });
  addEventListener('resize',()=>{ if(theme) resize(); });
  new MutationObserver(sync).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  sync();
})();

(function(){
  function vars(){ const cs=getComputedStyle(document.documentElement), g=k=>cs.getPropertyValue(k).trim();
    const txt=g('--txt'), m=(txt.match(/^#([0-9a-f]{6})$/i)||[])[1]; let dark=null;
    if(m){ const n=parseInt(m,16); dark=(((n>>16)&255)*.299+((n>>8)&255)*.587+(n&255)*.114)>150; }
    const msg={type:'vanoatlas-theme',theme:document.documentElement.dataset.theme||'',vars:{bg:g('--app-bg')||g('--bg'),panel:g('--panel'),panel2:g('--panel2'),panel3:g('--panel3'),line:g('--line'),txt:txt,mut:g('--mut'),acc:g('--acc'),season:g('--season-bg')}};
    if(dark!==null) msg.dark=dark; return msg; }
  function send(){ const msg=vars(); document.querySelectorAll('iframe').forEach(f=>{ try{ const pt=(typeof EMB!=='undefined'&&EMB.vanoleaf&&EMB.vanoleaf.port)||5500; if(new RegExp(':'+pt+'\\b').test(f.src||'')) f.contentWindow.postMessage(msg,'*'); }catch(e){} }); }
  window.vaSendThemeToApps=send;
  new MutationObserver(()=>setTimeout(send,60)).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','style']});
  document.addEventListener('load',e=>{ if(e.target&&e.target.tagName==='IFRAME') setTimeout(send,150); },true);
  window.addEventListener('message',e=>{ if(e.data&&e.data.type==='vanoleaf-ready') send(); });
})();

</script>
</body>
</html>
'''


SW_JS = r'''
const C='vanoatlas-v714-20261009';
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(['/','/manifest.json','/icon-192.png','/icon-512.png'])));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==C).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(e.request.method==='GET'&&(u.pathname==='/'||u.pathname==='/index.html'||u.pathname==='/manifest.json')){
    e.respondWith(fetch(e.request.url,{cache:'no-cache',credentials:'same-origin'}).then(r=>{const cl=r.clone();caches.open(C).then(c=>c.put(e.request,cl));return r;}).catch(()=>caches.match(u.pathname==='/index.html'?'/':e.request)));
  }
});
'''

_V_GLYPH = ["10001", "10001", "10001", "10001", "01010", "01010", "00100"]


def _png(width, height, rgba_bytes):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(rgba_bytes[y * stride:(y + 1) * stride])
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def make_icon(size):
    bg = (0x1F, 0x4E, 0x37, 255)
    fg = (0xFB, 0xF7, 0xEF, 255)
    px = [bg] * (size * size)
    gh, gw = len(_V_GLYPH), len(_V_GLYPH[0])
    box_h = int(size * 0.56)
    box_w = int(box_h * gw / gh)
    ox, oy = (size - box_w) // 2, (size - box_h) // 2
    for gy in range(box_h):
        sy = min(gy * gh // box_h, gh - 1)
        for gx in range(box_w):
            sx = min(gx * gw // box_w, gw - 1)
            if _V_GLYPH[sy][sx] == "1":
                px[(oy + gy) * size + (ox + gx)] = fg
    buf = bytearray()
    for p in px:
        buf.extend(p)
    return _png(size, size, bytes(buf))


ICON192 = make_icon(192)
ICON512 = make_icon(512)

MANIFEST = json.dumps({
    "name": "VanoAtlas", "short_name": "VanoAtlas",
    "start_url": "/", "display": "standalone",
    "background_color": "#FBF7EF", "theme_color": "#1F4E37",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
})



import re as _re
import ssl as _ssl
import urllib.request as _urlreq
import urllib.parse as _urlparse
import xml.etree.ElementTree as _ET

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"
_UA = "VanoAtlas/6 (personal research tracker; mailto:local)"


def _http_get(url, accept=None, timeout=20):
    headers = {"User-Agent": _UA}
    if accept:
        headers["Accept"] = accept
    contexts = [None]
    try:
        import certifi
        contexts.append(_ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    last = None
    for ctx in contexts:
        try:
            req = _urlreq.Request(url, headers=headers)
            with _urlreq.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read()
        except Exception as e:
            last = e
    if url.startswith("https://"):
        try:
            req = _urlreq.Request("http://" + url[8:], headers=headers)
            with _urlreq.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
    raise last or RuntimeError("network error")


def _split_list(s):
    return [x.strip() for x in _re.split(r"[,\n;]", s or "") if x.strip()]


def _clean_ws(s):
    return _re.sub(r"\s+", " ", (s or "")).strip()


def _arxiv_id_from_url(u):
    m = _re.search(r"arxiv\.org/abs/([^\s?#]+)", u or "")
    aid = m.group(1) if m else (u or "")
    return _re.sub(r"v\d+$", "", aid)


def parse_arxiv_atom(xml_bytes):
    root = _ET.fromstring(xml_bytes)
    out = []
    for e in root.findall(_ATOM + "entry"):
        aid = _arxiv_id_from_url(e.findtext(_ATOM + "id", ""))
        if not aid:
            continue
        cats = [c.get("term") for c in e.findall(_ATOM + "category") if c.get("term")]
        prim = e.find(_ARXIV_NS + "primary_category")
        if prim is not None and prim.get("term") and prim.get("term") in cats:
            cats.remove(prim.get("term"))
            cats.insert(0, prim.get("term"))
        doi = e.findtext(_ARXIV_NS + "doi", "") or ""
        jref = e.findtext(_ARXIV_NS + "journal_ref", "") or ""
        out.append({
            "arxiv_id": aid,
            "title": _clean_ws(e.findtext(_ATOM + "title", "")),
            "authors": ", ".join(_clean_ws(a.findtext(_ATOM + "name", "")) for a in e.findall(_ATOM + "author")),
            "abstract": _clean_ws(e.findtext(_ATOM + "summary", "")),
            "cats": ", ".join(cats),
            "published": (e.findtext(_ATOM + "published", "") or "")[:10],
            "doi": doi.strip(), "journal_ref": _clean_ws(jref),
        })
    return out


def _arxiv_api(search_query, max_results=40):
    q = _urlparse.urlencode({"search_query": search_query, "start": 0,
                             "max_results": max_results, "sortBy": "submittedDate",
                             "sortOrder": "descending"})
    return parse_arxiv_atom(_http_get("https://export.arxiv.org/api/query?" + q))


def _radar_conf():
    s = get_settings()
    return (_split_list(s.get("radar_keywords")), _split_list(s.get("radar_categories")),
            _split_list(s.get("radar_authors")))


def radar_score(p, kws, cats, authors):
    title, abstract = p["title"].lower(), p["abstract"].lower()
    score, why = 0, []
    hit_kw = []
    for k in kws:
        kl = k.lower()
        if kl in title:
            score += 30; hit_kw.append(k)
        elif kl in abstract:
            score += 14; hit_kw.append(k)
    if hit_kw:
        why.append("keywords: " + ", ".join(hit_kw[:4]))
    au = p["authors"].lower()
    hit_au = [a for a in authors if a.lower() in au]
    if hit_au:
        score += 45; why.append("followed author: " + ", ".join(hit_au[:3]))
    pc = [c.strip() for c in p["cats"].split(",") if c.strip()]
    if cats and pc and pc[0] in cats:
        score += 12; why.append("primary " + pc[0])
    elif cats and any(c in cats for c in pc):
        score += 6; why.append("cross-list " + ", ".join(c for c in pc if c in cats))
    try:
        age = (datetime.now() - datetime.fromisoformat(p["published"])).days
        score += max(0, 8 - age)
    except Exception:
        pass
    return min(99, score), " · ".join(why) or "matched your query"


def radar_fetch(notify=False):
    kws, cats, authors = _radar_conf()
    if not (kws or authors or cats):
        return {"ok": False, "error": "Add keywords, categories or authors in Radar settings first."}
    parts = []
    if kws:
        parts.append("(" + " OR ".join(f'all:"{k}"' for k in kws[:12]) + ")")
    q_main = " OR ".join(parts) if parts else ""
    if cats and q_main:
        q_main = f"({q_main}) AND (" + " OR ".join(f"cat:{c}" for c in cats[:8]) + ")"
    elif cats and not authors:
        q_main = " OR ".join(f"cat:{c}" for c in cats[:8])
    papers = {}
    errors = []
    queries = []
    if q_main:
        queries.append(q_main)
    if authors:
        queries.append(" OR ".join('au:"%s"' % a for a in authors[:10]))
    for i, q in enumerate(queries):
        if i:
            time.sleep(3)
        try:
            for p in _arxiv_api(q):
                papers[p["arxiv_id"]] = p
        except Exception as e:
            errors.append(str(e))
    local = 0
    try:
        for p in vanolib_recent_for_radar(14, cats or None):
            if p["arxiv_id"] in papers:
                continue
            sc, why = radar_score(p, kws, cats, authors)
            if "keywords" in why or "author" in why:
                papers[p["arxiv_id"]] = p
                local += 1
    except Exception:
        pass
    if not papers and errors:
        return {"ok": False, "error": "arXiv unreachable: " + errors[0]}
    conn = db()
    known = {r["arxiv_id"] for r in conn.execute("SELECT arxiv_id FROM radar_items").fetchall()}
    now_iso = datetime.now().isoformat(timespec="seconds")
    new, hot = 0, []
    for p in papers.values():
        if p["arxiv_id"] in known:
            continue
        sc, why = radar_score(p, kws, cats, authors)
        conn.execute("INSERT INTO radar_items(arxiv_id,title,authors,abstract,cats,published,score,why,state,doi,journal_ref,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (p["arxiv_id"], p["title"], p["authors"], p["abstract"], p["cats"], p["published"],
                      sc, why, "new", p["doi"], p["journal_ref"], now_iso))
        new += 1
        if sc >= 90:
            hot.append(p["title"])
    conn.execute("INSERT INTO app_settings(key,value) VALUES('radar_last',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now_iso,))
    conn.commit()
    conn.close()
    if notify and hot:
        try:
            mac_notify("VanoAtlas · arXiv Radar", f"{len(hot)} highly relevant paper(s): {hot[0][:90]}")
        except Exception:
            pass
    return {"ok": True, "new": new, "fetched": len(papers), "hot": len(hot), "from_vanolib": local,
            "arxiv_error": errors[0] if errors else ""}


def _radar_loop():
    time.sleep(90)
    while True:
        try:
            s = get_settings()
            last = s.get("radar_last") or ""
            due = True
            if last:
                try:
                    due = (datetime.now() - datetime.fromisoformat(last)).total_seconds() > 12 * 3600
                except Exception:
                    due = True
            if s.get("radar_auto") == "1" and due:
                radar_fetch(notify=True)
        except Exception:
            pass
        time.sleep(1800)


def _bib_escape(s):
    return (s or "").replace("{", "").replace("}", "")


def _last_name(author):
    a = (author or "").strip()
    if "," in a:
        return a.split(",")[0].strip()
    return a.split()[-1] if a.split() else "anon"


def make_bibkey(authors, year, existing=()):
    names = [x for x in _re.split(r",| and ", authors or "") if x.strip()]
    if len(names) >= 3:
        base = "".join(_last_name(n)[0] for n in names[:5] if _last_name(n))
    else:
        base = "".join(_last_name(n) for n in names) or "Ref"
    base = _re.sub(r"[^A-Za-z]", "", base) or "Ref"
    yy = str(year or "")[-2:]
    key = base + yy
    existing = set(existing)
    if key in existing:
        for c in "abcdefghijklmnopqrstuvwxyz":
            if key + c not in existing:
                return key + c
    return key


def ref_to_bibtex(r):
    authors = " and ".join(a.strip() for a in (r.get("authors") or "").split(",") if a.strip())
    if r.get("bibtex"):
        return r["bibtex"].strip()
    typ = "article" if r.get("venue") else "misc"
    f = [f"  author = {{{_bib_escape(authors)}}}", f"  title = {{{{{_bib_escape(r.get('title'))}}}}}"]
    if r.get("year"):
        f.append(f"  year = {{{r['year']}}}")
    if r.get("venue"):
        f.append(f"  journal = {{{_bib_escape(r['venue'])}}}")
    if r.get("doi"):
        f.append(f"  doi = {{{r['doi']}}}")
    if r.get("arxiv_id"):
        f += [f"  eprint = {{{r['arxiv_id']}}}", "  archivePrefix = {arXiv}"]
    if r.get("url"):
        f.append(f"  url = {{{r['url']}}}")
    return f"@{typ}{{{r.get('bibkey') or 'ref'},\n" + ",\n".join(f) + "\n}"


def _existing_keys():
    conn = db()
    keys = [r["bibkey"] for r in conn.execute("SELECT bibkey FROM refs").fetchall()]
    conn.close()
    return keys


def refs_lookup(ident):
    ident = (ident or "").strip()
    if not ident:
        return {"ok": False, "error": "Empty identifier"}
    m_doi = _re.search(r"(10\.\d{4,9}/[^\s\"<>]+)", ident)
    m_ax = _re.search(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", ident)
    try:
        if m_doi and "arxiv" not in ident.lower():
            doi = m_doi.group(1).rstrip(".")
            data = json.loads(_http_get("https://api.crossref.org/works/" + _urlparse.quote(doi)))["message"]
            authors = ", ".join(
                (a.get("given", "") + " " + a.get("family", "")).strip() for a in data.get("author", []))
            year = ""
            for k in ("published-print", "published-online", "issued"):
                dp = (data.get(k) or {}).get("date-parts")
                if dp and dp[0]:
                    year = str(dp[0][0]); break
            out = {"title": _clean_ws((data.get("title") or [""])[0]), "authors": authors, "year": year,
                   "venue": _clean_ws((data.get("container-title") or [""])[0]), "doi": doi,
                   "url": data.get("URL", ""), "arxiv_id": "", "abstract": _re.sub(r"<[^>]+>", "", data.get("abstract", "") or "")}
        elif m_ax:
            aid = m_ax.group(1)
            q = _urlparse.urlencode({"id_list": aid})
            res = parse_arxiv_atom(_http_get("https://export.arxiv.org/api/query?" + q))
            if not res:
                return {"ok": False, "error": "arXiv id not found"}
            p = res[0]
            out = {"title": p["title"], "authors": p["authors"], "year": p["published"][:4],
                   "venue": p["journal_ref"], "doi": p["doi"], "arxiv_id": p["arxiv_id"],
                   "url": "https://arxiv.org/abs/" + p["arxiv_id"], "abstract": p["abstract"]}
        else:
            return {"ok": False, "error": "Not a DOI or arXiv id"}
    except Exception as e:
        return {"ok": False, "error": f"Lookup failed: {e}"}
    out["bibkey"] = make_bibkey(out["authors"], out["year"], _existing_keys())
    return {"ok": True, "ref": out}


_ACC = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "c": "\u0327", "v": "\u030c", "=": "\u0304", ".": "\u0307", "u": "\u0306", "H": "\u030b"}


def _latex_to_unicode(s):
    import unicodedata
    s = s or ""
    s = _re.sub(r"\\([\"'`^~=.]|[cvuH](?=[\s{]))\s*\{?\\?([A-Za-z])\}?",
                lambda m: unicodedata.normalize("NFC", m.group(2) + _ACC[m.group(1)]), s)
    for a, b in ((r"\\ss\b", "ß"), (r"\\o\b", "ø"), (r"\\O\b", "Ø"), (r"\\aa\b", "å"), (r"\\ae\b", "æ"), (r"\\&", "&"), (r"--", "–"), (r"(?<!\\)~", " ")):
        s = _re.sub(a, b, s)
    return _clean_ws(s.replace("{", "").replace("}", ""))


def _bib_author_list(raw):
    out = []
    for a in _re.split(r"\s+and\s+", raw or ""):
        a = _latex_to_unicode(a)
        if not a:
            continue
        if "," in a:
            last, first = a.split(",", 1)
            a = (first.strip() + " " + last.strip()).strip()
        out.append(a)
    return ", ".join(out)


def _bib_fields(body):
    fields, i, n = {}, 0, len(body)
    while i < n:
        m = _re.compile(r"\s*,?\s*([A-Za-z\-_]+)\s*=\s*").match(body, i)
        if not m:
            break
        name, i = m.group(1).lower(), m.end()
        if i < n and body[i] == "{":
            depth, j = 0, i
            while j < n:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            val, i = body[i + 1:j], j + 1
        elif i < n and body[i] == '"':
            j = body.find('"', i + 1)
            j = n if j < 0 else j
            val, i = body[i + 1:j], j + 1
        else:
            m2 = _re.compile(r"[^,}\s]+").match(body, i)
            val = m2.group(0) if m2 else ""
            i = m2.end() if m2 else i + 1
        fields[name] = val
    return fields


def parse_bibtex(text):
    out = []
    for m in _re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text or ""):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        start, depth, j = m.end(), 1, m.end()
        while j < len(text) and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        f = _bib_fields(text[start:j - 1])
        U = lambda k: _latex_to_unicode(f.get(k, ""))
        out.append({"bibkey": m.group(2), "title": U("title"), "authors": _bib_author_list(f.get("author", "")),
                    "year": U("year"), "venue": U("journal") or U("booktitle") or U("publisher"),
                    "doi": U("doi"), "arxiv_id": U("eprint"), "url": f.get("url", "").strip(), "abstract": U("abstract"),
                    "bibtex": text[m.start():j].strip()})
    return out


def refs_import(text):
    items = parse_bibtex(text)
    keys = set(_existing_keys())
    added = skipped = 0
    for it in items:
        if it["bibkey"] in keys:
            skipped += 1
            continue
        it["status"] = "To read"
        db_insert("refs", it)
        keys.add(it["bibkey"])
        added += 1
    return {"ok": True, "added": added, "skipped": skipped, "found": len(items)}


def refs_export():
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM refs ORDER BY bibkey").fetchall()]
    conn.close()
    return "\n\n".join(ref_to_bibtex(r) for r in rows) + "\n"


VANOLIB_DIR = Path(os.environ.get("VANOLIB_DIR") or (Path(__file__).resolve().parent.parent / "VanoLib")).expanduser()
_VL = None
_VL_ERR = ""
_VL_TRIED = 0.0
_VL_LOCK = threading.Lock()
VANOLIB_API = {"overview", "articles", "article", "desk", "toggle_read", "bibtex", "get_reader",
               "save_state", "save_highlights", "ensure_pdf", "sync", "sync_status", "settings",
               "open_url", "fullscreen", "import_pdf", "update_mine", "delete_mine"}


def vanolib():
    global _VL, _VL_ERR, _VL_TRIED
    if _VL is not None:
        return _VL
    with _VL_LOCK:
        if _VL is not None:
            return _VL
        if time.time() - _VL_TRIED < 30:
            return None
        _VL_TRIED = time.time()
        lib_py = VANOLIB_DIR / "app" / "library.py"
        if not lib_py.exists():
            _VL_ERR = f"VanoLib not found in {VANOLIB_DIR}"
            return None
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("vanolib_library", str(lib_py))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _VL = mod.Library()
            _VL_ERR = ""
            try:
                _VL.start_sync()
            except Exception:
                pass
        except Exception as e:
            _VL_ERR = f"VanoLib could not be loaded: {e}"
            _VL = None
        return _VL


def _vl_fresh(L):
    try:
        with L.lock:
            L.store = L._load_store()
    except Exception:
        pass


def vanolib_call(name, params):
    L = vanolib()
    if L is None:
        return {"error": _VL_ERR or "VanoLib unavailable"}
    if name not in VANOLIB_API:
        return {"error": "unknown api"}
    _vl_fresh(L)
    params = dict(params or {})
    if name == "fullscreen":
        return {"ok": False}
    if name == "sync":
        return L.start_sync(force=bool(params.get("force")))
    if name == "sync_status":
        return L.sync
    if name == "settings":
        return L.set_settings(**params) if params else L.get_settings()
    return getattr(L, name)(**params)


_VL_COUNTS = {"t": 0, "total": 0, "pdfs": 0}


def vanolib_summary():
    L = vanolib()
    if L is None:
        return {"available": False, "error": _VL_ERR or "VanoLib unavailable", "dir": str(VANOLIB_DIR)}
    _vl_fresh(L)
    d = L.desk()
    c = L._db()
    try:
        if time.time() - _VL_COUNTS["t"] > 600:
            _VL_COUNTS.update(t=time.time(), total=c.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
                              pdfs=c.execute("SELECT COUNT(*) FROM articles WHERE local_pdf_path IS NOT NULL AND local_pdf_path!=''").fetchone()[0])
        total, pdfs = _VL_COUNTS["total"], _VL_COUNTS["pdfs"]
        to_read = c.execute("SELECT COUNT(*) FROM to_read").fetchone()[0]
    finally:
        c.close()
    st = L.store.get("state", {})
    in_prog = sum(1 for s in st.values() if 0 < s.get("ratio", 0) < 0.97)
    last = L.last_sync()
    log = L.store.get("log", {})
    return {"available": True, "dir": str(VANOLIB_DIR), "total": total, "to_read": to_read, "pdfs": pdfs,
            "in_progress": in_prog, "continue": d["continue"], "queue": d["queue"], "feed": d["feed"][:20],
            "new_week": d["new_week"], "week": d["week"], "pages_week": d["pages_week"],
            "log": {k: v for k, v in log.items() if k >= (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")},
            "highlights": sum(len(v) for v in L.store.get("highlights", {}).values()),
            "last_sync": last.isoformat() if last else None, "sync": L.sync}


def vanolib_search(q, limit=8, deep=False):
    L = vanolib()
    q = (q or "").strip()
    if L is None or len(q) < 3:
        return {"items": []}
    words = q.split()[:5]
    fields = ["title", "authors", "ext_id"] + (["abstract"] if deep else [])
    where, args = [], []
    for w in words:
        where.append("(" + " OR ".join(f"{f} LIKE ?" for f in fields) + ")")
        args += [f"%{w}%"] * len(fields)
    c = L._db()
    try:
        rows = c.execute(f"SELECT uid,title,authors,category,published,ext_id,local_pdf_path FROM articles "
                         f"WHERE {' AND '.join(where)} ORDER BY published DESC LIMIT ?", args + [int(limit)]).fetchall()
        tr = {r[0] for r in c.execute("SELECT uid FROM to_read")}
    finally:
        c.close()
    st = L.store.get("state", {})
    return {"items": [{"uid": r[0], "title": r[1], "authors": (r[2] or "").replace("; ", ", "), "category": r[3],
                       "year": (r[4] or "")[:4], "published": (r[4] or "")[:10], "ext_id": r[5],
                       "has_pdf": bool(r[6]), "to_read": r[0] in tr,
                       "progress": round(100 * st.get(r[0], {}).get("ratio", 0))} for r in rows]}


def vanolib_recent_for_radar(days=14, cats=None, limit=4000):
    L = vanolib()
    if L is None:
        return []
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    c = L._db()
    try:
        if cats:
            rows = c.execute("SELECT ext_id,title,authors,abstract,category,published FROM articles WHERE published>=? "
                             f"AND category IN ({','.join('?' * len(cats))}) ORDER BY published DESC LIMIT ?",
                             [since] + list(cats) + [limit]).fetchall()
        else:
            rows = c.execute("SELECT ext_id,title,authors,abstract,category,published FROM articles WHERE published>=? "
                             "ORDER BY published DESC LIMIT ?", (since, limit)).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        aid = _re.sub(r"v\d+$", "", r[0] or "")
        if not aid:
            continue
        out.append({"arxiv_id": aid, "title": _clean_ws(r[1]), "authors": (r[2] or "").replace("; ", ", "),
                    "abstract": _clean_ws(r[3]), "cats": r[4] or "", "published": (r[5] or "")[:10],
                    "doi": "", "journal_ref": ""})
    return out


def vanolib_ui_html():
    f = VANOLIB_DIR / "app" / "ui" / "index.html"
    if not f.exists():
        return None
    h = f.read_text(encoding="utf-8")
    h = (h.replace("fetch('/api/'", "fetch('/vanolib/api/'")
          .replace("url:'/pdf/'", "url:'/vanolib/pdf/'")
          .replace("PDFJS_LOCAL='/vendor/pdfjs/'", "PDFJS_LOCAL='/vanolib/vendor/pdfjs/'"))
    h = h.replace("  function open(a){if(!a)return;cur=a;$('#C-home')",
                  "  window.__vaOpen=a=>{show('C');open(a)};\n  function open(a){if(!a)return;cur=a;$('#C-home')", 1)
    hook = ("<script>(async()=>{const u=new URLSearchParams(location.search).get('open');if(!u)return;"
            "const a=await api('article',{uid:u});if(!a||!a.uid)return;"
            "const go=()=>{if(typeof CUR!=='undefined'&&CUR&&window.__vaOpen)window.__vaOpen(a);else setTimeout(go,120)};go()})();</script>\n</body>")
    i = h.rfind("</body>")
    if i >= 0:
        h = h[:i] + hook + h[i + len("</body>"):]
    return h


APPS_DIR = Path(__file__).resolve().parent.parent
EMBED_APPS = {
    "vanonote": {"dir": APPS_DIR / "VanoNote", "script": "vanonote.py", "port": 8766,
                 "ping": "/api/ping", "venv": ".venv", "env": {"VANONOTE_SILENT": "1"}},
    "vanoleaf": {"dir": APPS_DIR / "VanoLeaf", "script": "app.py", "port": 5500,
                 "ping": "/", "venv": ".venv", "env": {"VANOLEAF_SILENT": "1"}},
}
_APP_LOCK = threading.Lock()


def _app_up(port, ping):
    import urllib.request, urllib.error
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}{ping}", timeout=1.5)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def ensure_app(name):
    a = EMBED_APPS.get(name)
    if not a:
        return {"ok": False, "error": "unknown app"}
    with _APP_LOCK:
        if _app_up(a["port"], a["ping"]):
            return {"ok": True, "port": a["port"]}
        d = a["dir"]
        script = d / a["script"]
        if not script.exists():
            return {"ok": False, "error": f"{script} not found"}
        py = d / a["venv"] / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python3")
        py = str(py) if py.exists() else (sys.executable or shutil.which("python3") or "python3")
        logs = d / "logs"
        logs.mkdir(exist_ok=True)
        env = dict(os.environ, **a["env"])
        subprocess.Popen([py, str(script)], cwd=str(d), env=env, stdin=subprocess.DEVNULL,
                         stdout=open(logs / f"{name}.log", "a"), stderr=open(logs / f"{name}.err.log", "a"),
                         start_new_session=True)
        for _ in range(80):
            time.sleep(0.25)
            if _app_up(a["port"], a["ping"]):
                return {"ok": True, "port": a["port"]}
        return {"ok": False, "error": f"{name} did not start — see {logs / (name + '.err.log')}"}

def restart_app(name):
    a = EMBED_APPS.get(name)
    if not a:
        return {"ok": False, "error": "unknown app"}
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{a['port']}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=10).stdout
        for pid in out.split():
            if int(pid) != os.getpid():
                os.kill(int(pid), signal.SIGTERM)
    except Exception as e:
        return {"ok": False, "error": f"stop: {str(e)[:150]}"}
    for _ in range(40):
        time.sleep(0.25)
        if not _app_up(a["port"], a["ping"]):
            break
    return ensure_app(name)


_APP_MTIME = {}


def _app_code_watch():
    time.sleep(30)
    while True:
        for name, a in EMBED_APPS.items():
            try:
                files = [a["dir"] / a["script"]] + [a["dir"] / "index.html"]
                m = max(f.stat().st_mtime for f in files if f.exists())
                old = _APP_MTIME.get(name)
                _APP_MTIME[name] = m
                if old is not None and m != old and time.time() - m > 5 and _app_up(a["port"], a["ping"]):
                    restart_app(name)
            except Exception:
                pass
        time.sleep(60)


QUOTES_SEED = [
    ("Mathematics is the art of giving the same name to different things.", "Henri Poincaré", "Mathematics"),
    ("It is by logic that we prove, but by intuition that we discover.", "Henri Poincaré", "Mathematics"),
    ("Science is built up of facts, as a house is built of stones; but an accumulation of facts is no more a science than a heap of stones is a house.", "Henri Poincaré", "Science"),
    ("We must know. We will know.", "David Hilbert", "Mathematics"),
    ("The art of doing mathematics consists in finding that special case which contains all the germs of generality.", "David Hilbert", "Mathematics"),
    ("The essence of mathematics lies in its freedom.", "Georg Cantor", "Mathematics"),
    ("In mathematics the art of proposing a question must be held of higher value than solving it.", "Georg Cantor", "Research"),
    ("A mathematician who is not also something of a poet will never be a perfect mathematician.", "Karl Weierstrass", "Mathematics"),
    ("Young man, in mathematics you don't understand things. You just get used to them.", "John von Neumann", "Mathematics"),
    ("Mathematics is the queen of the sciences.", "Carl Friedrich Gauss", "Mathematics"),
    ("The mathematician's patterns, like the painter's or the poet's, must be beautiful.", "G. H. Hardy", "Mathematics"),
    ("Beauty is the first test: there is no permanent place in the world for ugly mathematics.", "G. H. Hardy", "Mathematics"),
    ("Pure mathematics is, in its way, the poetry of logical ideas.", "Albert Einstein", "Mathematics"),
    ("Do not worry about your difficulties in mathematics. I can assure you mine are still greater.", "Albert Einstein", "Mathematics"),
    ("The important thing is not to stop questioning.", "Albert Einstein", "Research"),
    ("If I have seen further it is by standing on the shoulders of Giants.", "Isaac Newton", "Research"),
    ("God made the integers, all else is the work of man.", "Leopold Kronecker", "Mathematics"),
    ("Mathematics, rightly viewed, possesses not only truth, but supreme beauty.", "Bertrand Russell", "Mathematics"),
    ("Mathematics is the music of reason.", "James Joseph Sylvester", "Mathematics"),
    ("Algebra is the offer made by the devil to the mathematician.", "Michael Atiyah", "Mathematics"),
    ("Mathematics is not about numbers, equations, computations, or algorithms: it is about understanding.", "William Thurston", "Mathematics"),
    ("The only way to learn mathematics is to do mathematics.", "Paul Halmos", "Mathematics"),
    ("A good stack of examples, as large as possible, is indispensable for a thorough understanding of any concept.", "Paul Halmos", "Research"),
    ("It appears to me that if one wants to make progress in mathematics, one should study the masters and not the pupils.", "Niels Henrik Abel", "Research"),
    ("Proofs really aren't there to convince you that something is true — they're there to show you why it is true.", "Andrew Gleason", "Mathematics"),
    ("What I cannot create, I do not understand.", "Richard Feynman", "Research"),
    ("The first principle is that you must not fool yourself — and you are the easiest person to fool.", "Richard Feynman", "Research"),
    ("Research is to see what everybody else has seen, and to think what nobody else has thought.", "Albert Szent-Györgyi", "Research"),
    ("In the fields of observation chance favours only the prepared mind.", "Louis Pasteur", "Research"),
    ("Science is a way of thinking much more than it is a body of knowledge.", "Carl Sagan", "Science"),
    ("The opposite of a correct statement is a false statement. But the opposite of a profound truth may well be another profound truth.", "Niels Bohr", "Science"),
    ("An expert is a man who has made all the mistakes which can be made in a very narrow field.", "Niels Bohr", "Research"),
    ("The seeker after the truth is not one who studies the writings of the ancients and puts his trust in them, but rather the one who suspects his faith in them.", "Ibn al-Haytham", "Research"),
    ("Divide each of the difficulties under examination into as many parts as possible, and as might be necessary for its adequate solution.", "René Descartes", "Research"),
    ("What is well conceived is clearly said, and the words to say it come easily.", "Nicolas Boileau", "Writing"),
    ("Wonder is the feeling of a philosopher, and philosophy begins in wonder.", "Plato", "Philosophy"),
    ("The unexamined life is not worth living.", "Socrates", "Philosophy"),
    ("Whereof one cannot speak, thereof one must be silent.", "Ludwig Wittgenstein", "Philosophy"),
    ("He who has a why to live can bear almost any how.", "Friedrich Nietzsche", "Philosophy"),
    ("Waste no more time arguing what a good man should be. Be one.", "Marcus Aurelius", "Discipline"),
    ("The impediment to action advances action. What stands in the way becomes the way.", "Marcus Aurelius", "Discipline"),
    ("It is not that we have a short time to live, but that we waste a lot of it.", "Seneca", "Discipline"),
    ("We are what we repeatedly do. Excellence, then, is not an act, but a habit.", "Will Durant", "Discipline"),
    ("A journey of a thousand miles begins with a single step.", "Laozi", "Discipline"),
    ("Genius is one percent inspiration and ninety-nine percent perspiration.", "Thomas Edison", "Discipline"),
    ("Nothing in life is to be feared, it is only to be understood.", "Marie Curie", "Science"),
    ("Knowledge is power.", "Francis Bacon", "Philosophy"),
    ("To those who do not know mathematics it is difficult to get across a real feeling as to the beauty, the deepest beauty, of nature.", "Richard Feynman", "Science"),
]
QUOTES_PEOPLE = [
    ("Henri Poincaré", "Mathematics"), ("David Hilbert", "Mathematics"), ("Carl Friedrich Gauss", "Mathematics"),
    ("Leonhard Euler", "Mathematics"), ("Georg Cantor", "Mathematics"), ("G. H. Hardy", "Mathematics"),
    ("Srinivasa Ramanujan", "Mathematics"), ("Emmy Noether", "Mathematics"), ("Hermann Weyl", "Mathematics"),
    ("John von Neumann", "Mathematics"), ("Paul Erdős", "Mathematics"), ("Alexander Grothendieck", "Mathematics"),
    ("Michael Atiyah", "Mathematics"), ("William Thurston", "Mathematics"), ("Paul Halmos", "Mathematics"),
    ("Bernhard Riemann", "Mathematics"), ("Évariste Galois", "Mathematics"), ("Sofia Kovalevskaya", "Mathematics"),
    ("Blaise Pascal", "Mathematics"), ("Pierre-Simon Laplace", "Mathematics"), ("Alan Turing", "Mathematics"),
    ("Kurt Gödel", "Mathematics"), ("George Pólya", "Research"), ("Terence Tao", "Mathematics"),
    ("Isaac Newton", "Science"), ("Albert Einstein", "Science"), ("Richard Feynman", "Science"),
    ("Marie Curie", "Science"), ("Niels Bohr", "Science"), ("Paul Dirac", "Science"), ("Max Planck", "Science"),
    ("Galileo Galilei", "Science"), ("Carl Sagan", "Science"), ("Louis Pasteur", "Science"),
    ("Ibn al-Haytham", "Science"), ("Bertrand Russell", "Philosophy"), ("Ludwig Wittgenstein", "Philosophy"),
    ("René Descartes", "Philosophy"), ("Plato", "Philosophy"), ("Aristotle", "Philosophy"),
    ("Friedrich Nietzsche", "Philosophy"), ("Baruch Spinoza", "Philosophy"), ("Immanuel Kant", "Philosophy"),
    ("Marcus Aurelius", "Discipline"), ("Seneca the Younger", "Discipline"), ("Epictetus", "Discipline"),
    ("Michel de Montaigne", "Philosophy"), ("Ibn Khaldun", "Philosophy"), ("Rumi", "Philosophy"),
]
QUOTES_STATE = {"running": False, "msg": "Idle", "error": "", "last": "", "added": 0}
_Q_EN = {"the", "is", "of", "and", "to", "a", "in", "that", "we", "not", "it", "be", "are", "one", "what", "you"}


def _q_norm(t):
    return _re.sub(r"[^a-z0-9]+", "", str(t).lower())[:160]


def _q_setting(k, v=None):
    conn = db()
    if v is None:
        r = conn.execute("SELECT value FROM app_settings WHERE key=?", (k,)).fetchone()
        conn.close()
        return r["value"] if r else None
    conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (k, str(v)))
    conn.commit()
    conn.close()


def quotes_add(items):
    conn = db()
    have = {_q_norm(r["text"]) for r in conn.execute("SELECT text FROM quotes")}
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for text, author, src, cat in items:
        text = " ".join(str(text or "").split()).strip().strip('"“”').strip()
        k = _q_norm(text)
        if len(k) < 12 or k in have:
            continue
        have.add(k)
        conn.execute("INSERT INTO quotes(text,author,source,category,fav,hidden,created_at) VALUES(?,?,?,?,0,0,?)",
                     (text, (author or "Unknown").strip(), src, cat, now))
        n += 1
    conn.commit()
    conn.close()
    return n


def quotes_seed():
    if _q_setting("quotes_seeded"):
        return
    quotes_add([(t, a, "VanoAtlas", c) for t, a, c in QUOTES_SEED])
    _q_setting("quotes_seeded", datetime.now().isoformat(timespec="seconds"))


def _wq_clean(s):
    import html as _html
    s = _re.sub(r"<ref[^>]*/>", "", s)
    s = _re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=_re.S)
    for _ in range(5):
        s = _re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = _re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = _re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)
    s = _re.sub(r"\[https?://\S+\]", "", s)
    s = _re.sub(r"<br\s*/?>", " ", s)
    s = _re.sub(r"<[^>]+>", "", s)
    s = s.replace("'''", "").replace("''", "")
    s = _html.unescape(s)
    return " ".join(s.split()).strip(" *:#").strip('"“”').strip()


def _wq_ok(t):
    if not (30 <= len(t) <= 260) or _re.search(r"[{}=|<>\[\]]|https?:|\bp\.\s*\d", t):
        return False
    if not (t[0].isalpha() or t[0] in "'‘"):
        return False
    words = set(_re.findall(r"[a-z]+", t.lower()))
    return len(words & _Q_EN) >= 2


def wikiquote_quotes(page):
    url = ("https://en.wikiquote.org/w/api.php?action=parse&format=json&prop=wikitext&redirects=1&page="
           + _urlparse.quote(page.replace(" ", "_")))
    w = json.loads(_http_get(url, timeout=25))["parse"]["wikitext"]["*"]
    stop = _re.search(r"^==\s*(Quotes about|About\b|Disputed|Misattributed|Attributed|External links|See also|Sources|References)",
                      w, _re.M | _re.I)
    if stop:
        w = w[:stop.start()]
    out = []
    for line in w.splitlines():
        if not line.startswith("*") or line.startswith("**") or line.startswith("*:"):
            continue
        t = _wq_clean(line[1:])
        if _wq_ok(t):
            out.append(t)
    return out


def quotes_collect(n_pages=8):
    import random as _random
    if QUOTES_STATE["running"]:
        return QUOTES_STATE
    QUOTES_STATE.update(running=True, msg="Collecting quotes…", error="")
    added, errs = 0, []
    try:
        for name, cat in _random.sample(QUOTES_PEOPLE, min(n_pages, len(QUOTES_PEOPLE))):
            QUOTES_STATE["msg"] = f"Wikiquote · {name}"
            try:
                qs = wikiquote_quotes(name)
                _random.shuffle(qs)
                added += quotes_add([(t, name.replace("Seneca the Younger", "Seneca"), "Wikiquote", cat) for t in qs[:10]])
            except Exception as e:
                errs.append(f"{name}: {str(e)[:80]}")
            time.sleep(1)
        QUOTES_STATE["msg"] = "ZenQuotes"
        try:
            d = json.loads(_http_get("https://zenquotes.io/api/quotes", timeout=20))
            added += quotes_add([(x.get("q", ""), x.get("a", "") or "Unknown", "ZenQuotes", "Motivation") for x in d
                                 if isinstance(x, dict) and x.get("q") and "zenquotes" not in (x.get("q", "") + x.get("a", "")).lower()
                                 and 20 <= len(x.get("q", "")) <= 260])
        except Exception as e:
            errs.append("ZenQuotes: " + str(e)[:80])
        now = datetime.now().isoformat(timespec="seconds")
        if added or not errs:
            _q_setting("quotes_last_collect", now)
        QUOTES_STATE.update(msg=f"{added} new quote{'s' if added != 1 else ''} collected", last=now, added=added,
                            error=("; ".join(errs[:3]) if errs and not added else ""))
    except Exception as e:
        QUOTES_STATE.update(msg="Collection failed", error=str(e)[:200])
    finally:
        QUOTES_STATE["running"] = False
    return QUOTES_STATE


def _quotes_loop():
    time.sleep(45)
    while True:
        try:
            last = _q_setting("quotes_last_collect")
            if not last or datetime.fromisoformat(last) < datetime.now() - timedelta(days=7):
                quotes_collect()
        except Exception as e:
            QUOTES_STATE["error"] = str(e)[:200]
        time.sleep(6 * 3600)






def _meta(k, v=None):
    k = "meta_" + k
    c = db()
    try:
        if v is None:
            r = c.execute("SELECT value FROM app_settings WHERE key=?", (k,)).fetchone()
            return r["value"] if r else None
        c.execute("INSERT INTO app_settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        c.commit()
    finally:
        c.close()


def collector_run(force=False):
    L = vanolib()
    if L is not None and not L.sync.get("running"):
        L.start_sync(force=force)


PLANNER_DEFAULT = {"wake": "05:45", "leave": "06:48", "am_start": "08:00", "am_end": "12:00",
                   "pm_start": "13:00", "pm_end": "17:00", "meet_day": 3, "meet_start": "15:30",
                   "meet_end": "16:30", "meet_title": "", "weekend": True}
PLAN_STATE = {"running": False, "msg": "Idle", "error": ""}


def planner_cfg():
    d = dict(PLANNER_DEFAULT)
    try:
        d.update(json.loads(_meta("planner_cfg") or "{}"))
    except Exception:
        pass
    return d


def planner_save(**kw):
    d = planner_cfg()
    for k, v in kw.items():
        if k in PLANNER_DEFAULT:
            d[k] = int(v) if k == "meet_day" else (str(v) in ("1", "true", "True") if k == "weekend" else str(v))
    _meta("planner_cfg", json.dumps(d))
    planner_ensure_meeting(force=True)
    return d


def _hm(s):
    h, m = (s or "0:0").split(":")[:2]
    return int(h) * 60 + int(m)


def _fmt(mins):
    return f"{mins // 60:02d}:{mins % 60:02d}"


def planner_ensure_meeting(force=False):
    cfg = planner_cfg()
    c = db()
    try:
        title = (cfg.get("meet_title") or "").strip()
        if not title:
            return
        row = c.execute("SELECT id FROM meetings WHERE title=? AND recur_freq='weekly'", (title,)).fetchone()
        today = datetime.now().date()
        first = today + timedelta(days=(cfg["meet_day"] - today.weekday()) % 7)
        data = {"title": title, "meeting_date": first.isoformat(), "meeting_type": "Advisory Meeting",
                "start_time": cfg["meet_start"], "end_time": cfg["meet_end"], "meeting_time": cfg["meet_start"],
                "attendees": "", "location": "",
                "recur_freq": "weekly", "recur_until": (today + timedelta(days=365)).isoformat(),
                "description": "Weekly meeting (added by the Day Planner)."}
        if row is None:
            c.close()
            db_insert("meetings", data)
            return
        if force:
            c.execute("UPDATE meetings SET start_time=?, end_time=?, meeting_time=?, meeting_date=? WHERE id=?",
                      (cfg["meet_start"], cfg["meet_end"], cfg["meet_start"], first.isoformat(), row["id"]))
            c.commit()
    finally:
        try:
            c.close()
        except Exception:
            pass


def _plan_candidates(day):
    c = db()
    rows = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE status!='Done'")]
    c.close()
    di = day.isoformat()
    def key(t):
        dl = t.get("deadline") or "9999-12-31"
        return (0 if (t.get("planned_date") or "") == di else 1,
                0 if dl <= (day + timedelta(days=3)).isoformat() else 1,
                0 if t.get("status") == "In Progress" else 1,
                {"High": 0, "Normal": 1, "Low": 2}.get(t.get("priority"), 1), dl)
    return sorted(rows, key=key)


def planner_build(day=None, replace=False):
    PLAN_STATE.update(running=True, msg="Planning the day…", error="")
    try:
        if True:
            day = day or datetime.now().date()
            di = day.isoformat()
            cfg = planner_cfg()
            mark = "plan_ids_" + di
            old = json.loads(_meta(mark) or "[]")
            c = db()
            if old and not replace:
                c.close()
                PLAN_STATE["msg"] = "Today is already planned"
                return
            if old:
                c.execute(f"DELETE FROM daily_tasks WHERE done=0 AND id IN ({','.join('?' * len(old))})", old)
                c.commit()
            c.close()
            wd = day.weekday()
            items = []
            add = lambda s, e, title, cat: items.append((s, e, title, cat))
            wake, leave = _hm(cfg["wake"]), _hm(cfg["leave"])
            add(wake, wake + 30, "Wake up · water, stretch", "Health")
            add(wake + 30, leave - 10, "Breakfast & get ready", "Personal")
            if wd < 5:
                add(leave, leave, "Leave home", "Personal")
            if wd < 5:
                am0, am1, pm0, pm1 = _hm(cfg["am_start"]), _hm(cfg["am_end"]), _hm(cfg["pm_start"]), _hm(cfg["pm_end"])
                add(am0 - 30, am0, "Plan the day: priorities and inbox", "Research")
                tasks = _plan_candidates(day)
                blocks = [(am0, am0 + 120, "Deep work"), (am0 + 135, am1, "Deep work"), (pm0, pm0 + 90, "Work")]
                is_meet = wd == int(cfg["meet_day"])
                ms, me = _hm(cfg["meet_start"]), _hm(cfg["meet_end"])
                if is_meet:
                    if pm0 + 90 < ms - 60:
                        blocks.append((pm0 + 90, ms - 60, "Work"))
                    add(ms - 60, ms, "Prepare the meeting: progress since last week, open questions, next steps", "Research")
                    add(ms, me, cfg["meet_title"], "Research")
                    add(me, me + 20, "Write the meeting notes and the action items", "Research")
                    if me + 20 < pm1 - 30:
                        blocks.append((me + 20, pm1 - 30, "Work"))
                else:
                    blocks.append((pm0 + 90, pm1 - 30, "Work"))
                extra = []
                for i, (s, e, label) in enumerate(blocks):
                    if s >= e:
                        continue
                    if i < len(tasks):
                        tk = tasks[i]
                        extra_t = f" · due {tk['deadline']}" if tk.get("deadline") else ""
                        add(s, e, f"{label}: {tk['title']}{extra_t}", "Research")
                    elif extra:
                        add(s, e, extra.pop(0), "Research")
                    else:
                        add(s, e, f"{label}: focused research", "Research")
                    if i == 0:
                        add(e, e + 15, "Break · walk, water", "Health")
                add(am1, pm0, "Lunch break", "Personal")
                for x in extra[:1]:
                    add(pm1 - 60, pm1 - 30, x, "Research")
                wrap = max(pm1 - 30, me + 20) if is_meet else pm1 - 30
                if wrap < pm1:
                    add(wrap, pm1, "Wrap-up: mark what is done, plan tomorrow", "Research")
                add(pm1 + 60, pm1 + 120, "Sport or walk", "Sport")
            elif cfg.get("weekend"):
                add(_hm("09:30"), _hm("11:00"), "Light research: read one paper", "Research")
                add(_hm("11:00"), _hm("12:00"), "Weekly review: what moved forward, priorities for next week", "Research")
                add(_hm("16:00"), _hm("17:30"), "Sport, family and friends", "Social")
            ids = []
            for s, e, title, cat in sorted(items):
                ids.append(db_insert("daily_tasks", {"title": title, "category": cat, "done": 0, "task_date": di,
                                                     "task_time": _fmt(s), "task_time_end": _fmt(e) if e > s else ""}))
            _meta(mark, json.dumps(ids))
            PLAN_STATE["msg"] = f"Day planned: {len(ids)} entries"
    except Exception as e:
        PLAN_STATE.update(error=str(e)[:300], msg="Error")
    finally:
        PLAN_STATE["running"] = False


def _vanolib_weekly_loop():
    time.sleep(60)
    while True:
        try:
            L = vanolib()
            if L is not None and L.needs_sync() and not L.sync.get("running"):
                collector_run()
        except Exception:
            pass
        time.sleep(3600)


_PAGE = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _gzip_ok(self, body):
        return len(body) > 1400 and "gzip" in (self.headers.get("Accept-Encoding") or "")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        gz = self._gzip_ok(body)
        if gz:
            import gzip as _gz
            body = _gz.compress(body, 5)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _page(self):
        if not _PAGE.get("etag"):
            import gzip as _gz
            raw = HTML.encode()
            _PAGE.update(raw=raw, gz=_gz.compress(raw, 6), etag='"' + _hashlib.md5(raw).hexdigest()[:16] + '"')
        if self.headers.get("If-None-Match") == _PAGE["etag"]:
            self.send_response(304)
            self.send_header("ETag", _PAGE["etag"])
            self.end_headers()
            return
        gz = "gzip" in (self.headers.get("Accept-Encoding") or "")
        body = _PAGE["gz"] if gz else _PAGE["raw"]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", _PAGE["etag"])
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def _route(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "api" and parts[1] in TABLES:
            return parts[1], (int(parts[2]) if len(parts) > 2 else None)
        return None, None

    def _raw(self, body, ctype, no_cache=False):
        self._raw_bytes(body.encode(), ctype, no_cache=no_cache)

    def _raw_bytes(self, body, ctype, no_cache=False):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if no_cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, fp, ctype=None, cache=False):
        data = Path(fp).read_bytes()
        ctype = ctype or mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            try:
                a = int(a or 0)
                b = int(b) if b else len(data) - 1
            except ValueError:
                a, b = 0, len(data) - 1
            chunk = data[a:b + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{a + len(chunk) - 1}/{len(data)}")
        else:
            chunk = data
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Cache-Control", "public, max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(chunk)

    def _vanolib_get(self, path, query):
        from urllib.parse import parse_qs, unquote
        if path in ("/vanolib", "/vanolib/", "/vanolib/index.html"):
            if path == "/vanolib":
                self.send_response(302)
                self.send_header("Location", "/vanolib/")
                self.end_headers()
                return
            vanolib()
            h = vanolib_ui_html()
            if h is None:
                self._raw("<p style='font-family:system-ui;padding:40px'>VanoLib not found in "
                          + str(VANOLIB_DIR) + "</p>", "text/html; charset=utf-8", no_cache=True)
            else:
                self._raw(h, "text/html; charset=utf-8", no_cache=True)
            return
        if path.startswith("/vanolib/api/"):
            q = {k: v[0] for k, v in parse_qs(query).items()}
            self._json(vanolib_call(path[len("/vanolib/api/"):], q))
            return
        if path.startswith("/vanolib/pdf/"):
            L = vanolib()
            f = L.pdf_path(unquote(path[len("/vanolib/pdf/"):])) if L else None
            if f:
                self._send_file(f, "application/pdf")
            else:
                self._json({"error": "PDF not downloaded"}, 404)
            return
        ui = (VANOLIB_DIR / "app" / "ui").resolve()
        f = (ui / unquote(path[len("/vanolib/"):])).resolve()
        if ui in f.parents and f.is_file():
            self._send_file(f, cache=True)
        else:
            self._json({"error": "not found"}, 404)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path.startswith("/api/planner/"):
            from urllib.parse import parse_qs as _pq
            q = {k: v[0] for k, v in _pq(u.query, keep_blank_values=True).items()}
            try:
                if path == "/api/planner/replan":
                    planner_build(replace=True)
                    self._json({"ok": True, **PLAN_STATE})
                elif path == "/api/planner/config":
                    self._json(planner_cfg())
                elif path == "/api/planner/save":
                    self._json(planner_save(**q))
                else:
                    self._json({"error": "unknown"}, 404)
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        if path.startswith("/api/apps/") and path.endswith("/restart"):
            try:
                self._json(restart_app(path.split("/")[3]))
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return
        if path.startswith("/api/apps/") and path.endswith("/start"):
            try:
                self._json(ensure_app(path.split("/")[3]))
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return
        if path == "/vanolib" or path.startswith("/vanolib/"):
            try:
                self._vanolib_get(path, u.query)
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        if path == "/api/vanolib/summary":
            try:
                self._json(vanolib_summary())
            except Exception as e:
                self._json({"available": False, "error": str(e)})
            return
        if path == "/api/vanolib/search":
            from urllib.parse import parse_qs
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                self._json(vanolib_search(q.get("q", ""), min(50, int(q.get("limit", 8))), q.get("deep") == "1"))
            except Exception as e:
                self._json({"items": [], "error": str(e)})
            return
        if path == "/" or path == "/index.html":
            self._page()
        elif path == "/sw.js":
            self._raw(SW_JS, "application/javascript", no_cache=True)
        elif path == "/manifest.json":
            self._raw(MANIFEST, "application/manifest+json")
        elif path == "/icon-192.png":
            self._raw_bytes(ICON192, "image/png")
        elif path == "/icon-512.png":
            self._raw_bytes(ICON512, "image/png")
        elif path == "/api/quotes/collect":
            if not QUOTES_STATE["running"]:
                QUOTES_STATE["running"] = True
                QUOTES_STATE["msg"] = "Starting…"
                def _go():
                    QUOTES_STATE["running"] = False
                    quotes_collect()
                threading.Thread(target=_go, daemon=True).start()
            self._json(dict(QUOTES_STATE))
        elif path == "/api/quotes/status":
            self._json(dict(QUOTES_STATE))
        elif path == "/api/ping":
            self._json({"ok": True, "version": APP_VERSION})
        elif path == "/api/all":
            self._json(db_all())
        elif path == "/api/icloud/status":
            self._json(icloud_status())
        elif path == "/api/settings":
            self._json(get_settings())
        elif path == "/api/data/health":
            self._json(data_health())
        elif path == "/api/refs/export.bib":
            body = refs_export().encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-bibtex; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="references.bib"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/api/notes/") and path.endswith("/pdf"):
            self._serve_note_pdf(path.split("/")[3])
        elif path.startswith("/assets/"):
            self._serve_asset(path[len("/assets/"):])
        else:
            self._json({"error": "not found"}, 404)

    def _serve_asset(self, name):
        safe = Path(name).name
        fp = ASSETS_DIR / safe
        if not safe or not fp.is_file() or ASSETS_DIR not in fp.resolve().parents:
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
        data = fp.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.end_headers()
        self.wfile.write(data)

    def _serve_note_pdf(self, rid):
        conn = db()
        row = conn.execute("SELECT * FROM notes WHERE id=?", (rid,)).fetchone()
        conn.close()
        if not row:
            self._json({"error": "note not found"}, 404)
            return
        tex_source, prefer_engine = wrap_note_as_latex(row["title"], row["content"])
        pdf_path, error = compile_latex_to_pdf(tex_source, base_name=f"note_{rid}",
                                                prefer_engine=prefer_engine)
        if error:
            self._json({"error": error}, 500)
            return
        data = pdf_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'inline; filename="{pdf_path.name}"')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/vanolib/api/"):
            _VL_COUNTS["t"] = 0
            try:
                self._json(vanolib_call(path[len("/vanolib/api/"):], self._body()))
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        if path == "/api/settings":
            self._json(set_settings(self._body()))
            return
        if path == "/api/data/cleanup":
            self._json(safe_data_cleanup())
            return
        if path == "/api/radar/fetch":
            self._json(radar_fetch())
            return
        if path == "/api/refs/lookup":
            self._json(refs_lookup(self._body().get("id", "")))
            return
        if path == "/api/refs/import":
            self._json(refs_import(self._body().get("bibtex", "")))
            return
        if path == "/api/notify/test":
            mac_notify("VanoAtlas", "Test notification — notifications are working.")
            self._json({"ok": True})
            return
        if path == "/api/gcal/sync":
            self._json(gcal_sync())
            return
        if path == "/api/drive/backup":
            self._json(drive_backup())
            return
        if path == "/api/icloud/sync":
            self._json(icloud_sync())
            return
        if path == "/api/icloud/save_creds":
            body = self._body()
            email = (body.get("email") or "").strip()
            app_password = (body.get("app_password") or "").strip()
            if not email or not app_password:
                self._json({"ok": False, "error": "Apple ID and app-specific password are both required"})
                return
            _save_icloud_creds(email, app_password)
            self._json({"ok": True})
            return
        if path == "/api/icloud/disconnect":
            _icloud_disconnect()
            self._json({"ok": True})
            return
        table, _ = self._route()
        if table:
            rid = db_insert(table, self._body())
            self._json({"ok": True, "id": rid})
        else:
            self._json({"error": "not found"}, 404)

    def do_PUT(self):
        table, rid = self._route()
        if table and rid:
            db_update(table, rid, self._body())
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        table, rid = self._route()
        if table and rid:
            db_delete(table, rid)
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def is_our_server(port):
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return json.loads(r.read()).get("ok") is True
    except Exception:
        return False


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None



def stop_existing_vanoatlas(port):
    if not is_our_server(port):
        return False
    try:
        out = subprocess.check_output(["lsof", "-ti", f":{port}"], text=True, stderr=subprocess.DEVNULL)
        pids = {int(x.strip()) for x in out.splitlines() if x.strip().isdigit()}
        for pid in pids:
            if pid != os.getpid():
                os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 2.5
        while time.time() < deadline and port_busy(port):
            time.sleep(0.1)
        return not port_busy(port)
    except Exception:
        return False



def main():
    silent = os.environ.get("VANOATLAS_SILENT") == "1"
    _v6_marker = DB_DIR / ".v6_backup_done"
    if DB_PATH.exists() and not _v6_marker.exists():
        print("VanoAtlas v6: backup ->", _backup_db("before_v6"))
        try:
            _v6_marker.write_text(datetime.now().isoformat())
        except OSError:
            pass
    init_db()
    try:
        quotes_seed()
    except Exception as e:
        print('quotes seed:', e)
    cleanup_on_upgrade()
    port = PORT
    url = f"http://localhost:{port}"

    if port_busy(port):
        if is_our_server(port):
            print(f"Replacing an older VanoAtlas instance on port {port}…")
            if not stop_existing_vanoatlas(port):
                print("Could not stop the existing VanoAtlas automatically.")
                print(f"Run: lsof -ti:{port} | xargs kill -9")
                return
        else:
            print(f"Port {port} is in use by another program (not VanoAtlas).")
            print(f"Close whatever is using port {port}, then run this again.")
            print(f"  Find it with:  lsof -i :{port}")
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    if not silent:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    threading.Thread(target=_auto_backup_loop, daemon=True).start()
    threading.Thread(target=_auto_calendar_sync_loop, daemon=True).start()
    threading.Thread(target=_notify_loop, daemon=True).start()
    threading.Thread(target=vanolib, daemon=True).start()
    threading.Thread(target=_vanolib_weekly_loop, daemon=True).start()
    threading.Thread(target=_quotes_loop, daemon=True).start()
    threading.Thread(target=_app_code_watch, daemon=True).start()
    print(f"VanoAtlas v{APP_VERSION} · running at {url}   (Ctrl-C to quit)")
    ip = lan_ip()
    if ip:
        print(f"  On your phone (same Wi-Fi): http://{ip}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
