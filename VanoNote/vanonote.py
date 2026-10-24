#!/usr/bin/env python3
from __future__ import annotations

import json
import ipaddress
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import tempfile
import urllib.parse
import webbrowser
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ATTACH_DIR = BASE_DIR / "attachments"
AUDIO_DIR = BASE_DIR / "audio"
EXPORT_DIR = BASE_DIR / "exports"
BACKUP_DIR = BASE_DIR / "backups"
ASSETS_DIR = BASE_DIR / "assets"
DB_PATH = DATA_DIR / "vanonote.db"
PORT = 8766
MAX_BODY = 100 * 1024 * 1024

for p in (DATA_DIR, ATTACH_DIR, AUDIO_DIR, EXPORT_DIR, BACKUP_DIR, ASSETS_DIR):
    p.mkdir(parents=True, exist_ok=True)

try:
    import pymupdf as fitz
    HAVE_FITZ = True
except Exception:
    try:
        import fitz
        HAVE_FITZ = True
    except Exception:
        HAVE_FITZ = False

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS folders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  color TEXT DEFAULT '#355e4a',
  sort_order INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  kind TEXT DEFAULT 'notebook',
  paper TEXT DEFAULT 'blank',
  favorite INTEGER DEFAULT 0,
  archived INTEGER DEFAULT 0,
  locked INTEGER DEFAULT 0,
  content_json TEXT NOT NULL,
  plain_text TEXT DEFAULT '',
  transcript TEXT DEFAULT '',
  tags TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT DEFAULT '',
  revision INTEGER DEFAULT 1,
  share_token TEXT DEFAULT '',
  share_mode TEXT DEFAULT 'none'
);
CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
  kind TEXT DEFAULT 'file',
  name TEXT NOT NULL,
  mime TEXT DEFAULT 'application/octet-stream',
  rel_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  content_json TEXT NOT NULL,
  plain_text TEXT DEFAULT '',
  transcript TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flashcards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
  front TEXT NOT NULL,
  back TEXT NOT NULL,
  due_at TEXT DEFAULT '',
  interval_days INTEGER DEFAULT 0,
  ease REAL DEFAULT 2.5,
  reps INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder_id);
CREATE INDEX IF NOT EXISTS idx_attachments_note ON attachments(note_id);
CREATE INDEX IF NOT EXISTS idx_versions_note ON versions(note_id, created_at DESC);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def default_content(paper="blank"):
    return {
        "version": 1,
        "activePage": 0,
        "paper": paper,
        "pages": [{
            "id": secrets.token_hex(4), "name": "Page 1", "paper": paper,
            "width": 1200, "height": 1600, "strokes": [], "objects": [],
            "background": None
        }],
        "settings": {"penOnly": True, "snap": False}
    }


def init_db():
    conn = db()
    conn.executescript(SCHEMA)
    count = conn.execute("SELECT COUNT(*) c FROM folders").fetchone()["c"]
    if not count:
        t = now_iso()
        for i, (name, color) in enumerate([
            ("Courses", "#1f4e37"), ("Research", "#7a5c2e"),
            ("Meetings", "#355e4a"), ("Personal", "#8a5528")
        ]):
            conn.execute("INSERT INTO folders(name,color,sort_order,created_at,updated_at) VALUES(?,?,?,?,?)",
                         (name, color, i, t, t))
        conn.commit()
    conn.close()


def rowdict(r):
    return dict(r) if r else None


def note_meta(row):
    d = dict(row)
    d.pop("content_json", None)
    return d


def list_attachments(conn, note_id, share_token=""):
    out = []
    for r in conn.execute("SELECT * FROM attachments WHERE note_id=? ORDER BY id", (note_id,)):
        d = dict(r)
        suffix = "?share=" + urllib.parse.quote(share_token) if share_token else ""
        d["url"] = f"/file/{d['id']}{suffix}"
        out.append(d)
    return out


def get_note(conn, note_id, share_token=""):
    r = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["content"] = json.loads(d.pop("content_json"))
    except Exception:
        d["content"] = default_content(d.get("paper") or "blank")
        d.pop("content_json", None)
    if share_token:
        suffix = "?share=" + urllib.parse.quote(share_token)
        def add_share(value):
            if isinstance(value, dict): return {k: add_share(v) for k, v in value.items()}
            if isinstance(value, list): return [add_share(v) for v in value]
            if isinstance(value, str) and (value.startswith("/file/") or value.startswith("/pdfpage/")) and "?" not in value:
                return value + suffix
            return value
        d["content"] = add_share(d["content"])
    d["attachments"] = list_attachments(conn, note_id, share_token)
    return d


def create_note(payload):
    title = str(payload.get("title") or "Untitled Note").strip()[:300]
    folder_id = payload.get("folder_id") or None
    kind = payload.get("kind") if payload.get("kind") in {"notebook", "pdf"} else "notebook"
    paper = payload.get("paper") if payload.get("paper") in {"blank", "ruled", "grid", "dots", "cornell", "blackboard"} else "blank"
    content = payload.get("content") or default_content(paper)
    plain_text = str(payload.get("plain_text") or "")
    transcript = str(payload.get("transcript") or "")
    tags = str(payload.get("tags") or "")[:2000]
    t = now_iso()
    conn = db()
    cur = conn.execute(
        "INSERT INTO notes(folder_id,title,kind,paper,content_json,plain_text,transcript,tags,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (folder_id, title, kind, paper, json.dumps(content, ensure_ascii=False), plain_text, transcript, tags, t, t))
    conn.commit()
    rid = cur.lastrowid
    out = get_note(conn, rid)
    conn.close()
    return out


def snapshot_if_needed(conn, row, force=False):
    last = conn.execute("SELECT created_at FROM versions WHERE note_id=? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
    should = bool(force)
    if last:
        if not force:
            try:
                should = (datetime.now() - datetime.fromisoformat(last["created_at"])).total_seconds() >= 180
            except Exception:
                should = True
    else:
        should = True
    if should:
        conn.execute("INSERT INTO versions(note_id,title,content_json,plain_text,transcript,created_at) VALUES(?,?,?,?,?,?)",
                     (row["id"], row["title"], row["content_json"], row["plain_text"], row["transcript"], now_iso()))
        conn.execute("DELETE FROM versions WHERE note_id=? AND id NOT IN (SELECT id FROM versions WHERE note_id=? ORDER BY id DESC LIMIT 100)",
                     (row["id"], row["id"]))


def update_note(note_id, payload, shared=False):
    conn = db()
    row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if not row:
        conn.close(); return None
    snapshot_if_needed(conn, row)
    allowed = ["title", "kind", "paper", "plain_text", "transcript"] if shared else [
        "folder_id", "title", "kind", "paper", "favorite", "archived", "locked",
        "plain_text", "transcript", "tags"
    ]
    cols, vals = [], []
    for k in allowed:
        if k in payload:
            cols.append(f"{k}=?")
            v = payload[k]
            if k in ("favorite", "archived", "locked"):
                v = 1 if v else 0
            if k == "folder_id" and not v:
                v = None
            if k == "title":
                v = str(v or "Untitled Note").strip()[:300]
            if k == "kind" and v not in {"notebook", "pdf"}:
                v = "notebook"
            if k == "paper" and v not in {"blank", "ruled", "grid", "dots", "cornell", "blackboard"}:
                v = "blank"
            if k == "tags":
                v = str(v or "")[:2000]
            vals.append(v)
    if "content" in payload:
        cols.append("content_json=?")
        vals.append(json.dumps(payload["content"], ensure_ascii=False, separators=(",", ":")))
    cols += ["updated_at=?", "revision=revision+1"]
    vals.append(now_iso())
    vals.append(note_id)
    conn.execute(f"UPDATE notes SET {','.join(cols)} WHERE id=?", vals)
    conn.commit()
    out = get_note(conn, note_id)
    conn.close()
    return out


def safe_name(name):
    name = Path(name or "file").name
    name = re.sub(r"[^A-Za-z0-9._()\- àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]", "_", name)
    return name[:180] or "file"


def is_loopback(host):
    try:
        return ipaddress.ip_address((host or "").split("%", 1)[0]).is_loopback
    except ValueError:
        return host in {"localhost", ""}


def safe_unlink(path):
    try:
        target = path.resolve()
        roots = (ATTACH_DIR.resolve(), AUDIO_DIR.resolve())
        if any(target == root or root in target.parents for root in roots) and target.is_file():
            target.unlink()
    except OSError:
        pass


def delete_note_files(conn, note_id):
    for row in conn.execute("SELECT id,rel_path FROM attachments WHERE note_id=?", (note_id,)):
        safe_unlink(BASE_DIR / row["rel_path"])
        shutil.rmtree(ATTACH_DIR / f"pdf_{row['id']}", ignore_errors=True)


def duplicate_note(note_id):
    conn = db()
    src = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if not src:
        conn.close(); return None
    t = now_iso()
    cur = conn.execute(
        "INSERT INTO notes(folder_id,title,kind,paper,favorite,archived,locked,content_json,plain_text,transcript,tags,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (src["folder_id"], (src["title"] + " Copy")[:300], src["kind"], src["paper"], 0, 0, src["locked"],
         src["content_json"], src["plain_text"], src["transcript"], src["tags"], t, t))
    new_id = cur.lastrowid
    content = src["content_json"]
    copied = []
    try:
        for att in conn.execute("SELECT * FROM attachments WHERE note_id=? ORDER BY id", (note_id,)).fetchall():
            source = BASE_DIR / att["rel_path"]
            root = AUDIO_DIR if att["kind"] == "audio" else ATTACH_DIR
            dest = root / f"{int(time.time())}_{secrets.token_hex(5)}_{safe_name(att['name'])}"
            if source.is_file():
                shutil.copy2(source, dest)
            cur2 = conn.execute(
                "INSERT INTO attachments(note_id,kind,name,mime,rel_path,created_at) VALUES(?,?,?,?,?,?)",
                (new_id, att["kind"], att["name"], att["mime"], str(dest.relative_to(BASE_DIR)), t))
            new_aid = cur2.lastrowid
            copied.append(dest)
            old_pages = ATTACH_DIR / f"pdf_{att['id']}"
            new_pages = ATTACH_DIR / f"pdf_{new_aid}"
            if old_pages.is_dir():
                shutil.copytree(old_pages, new_pages)
                copied.append(new_pages)
            content = content.replace(f"/file/{att['id']}", f"/file/{new_aid}")
            content = content.replace(f"/pdfpage/{att['id']}/", f"/pdfpage/{new_aid}/")
            content = re.sub(rf'("attachment_id"\s*:\s*){att["id"]}(\D)', rf'\g<1>{new_aid}\2', content)
        conn.execute("UPDATE notes SET content_json=? WHERE id=?", (content, new_id))
        conn.commit(); out = get_note(conn, new_id)
    except Exception:
        conn.rollback()
        for path in copied:
            if path.is_dir(): shutil.rmtree(path, ignore_errors=True)
            else: safe_unlink(path)
        conn.close(); raise
    conn.close(); return out


def office_to_pdf(src: Path):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None, "LibreOffice not installed; the original file was attached but not converted to PDF."
    outdir = src.parent / (src.stem + "_converted")
    outdir.mkdir(exist_ok=True)
    try:
        p = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(src)],
                           capture_output=True, text=True, timeout=90)
        pdf = outdir / (src.stem + ".pdf")
        if p.returncode == 0 and pdf.exists():
            return pdf, None
        return None, (p.stderr or p.stdout or "Conversion failed")[-1000:]
    except Exception as e:
        return None, str(e)


def render_pdf_pages(pdf_path: Path, attachment_id: int, share_token=""):
    if not HAVE_FITZ:
        return [], "PDF rendering needs PyMuPDF. Double-click install_dependencies.command once."
    outdir = ATTACH_DIR / f"pdf_{attachment_id}"
    outdir.mkdir(parents=True, exist_ok=True)
    urls = []
    try:
        doc = fitz.open(str(pdf_path))
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
            fn = outdir / f"page_{i+1:04d}.png"
            pix.save(str(fn))
            suffix = "?share=" + urllib.parse.quote(share_token) if share_token else ""
            urls.append({"url": f"/pdfpage/{attachment_id}/{i+1}{suffix}", "width": pix.width, "height": pix.height})
        return urls, None
    except Exception as e:
        return [], str(e)


_HI_LOCK = threading.Lock()


def hires_page(aid: int, page: int):
    hi = ATTACH_DIR / f"pdf_{aid}" / f"page_{page:04d}@hi.png"
    if hi.exists():
        return hi
    if not HAVE_FITZ or page < 1:
        return None
    conn = db(); r = conn.execute("SELECT rel_path FROM attachments WHERE id=?", (aid,)).fetchone(); conn.close()
    if not r:
        return None
    src = BASE_DIR / r["rel_path"]
    if src.suffix.lower() != ".pdf":
        src = src.parent / (src.stem + "_converted") / (src.stem + ".pdf")
    if not src.exists():
        return None
    with _HI_LOCK:
        if hi.exists():
            return hi
        try:
            doc = fitz.open(str(src))
            if page > len(doc):
                doc.close(); return None
            pg = doc[page - 1]
            scale = max(2.0, min(8.0, 2600.0 / max(1.0, pg.rect.width)))
            pix = pg.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            tmp = hi.with_name(hi.stem + ".tmp.png")
            pix.save(str(tmp)); tmp.replace(hi); doc.close()
            return hi
        except Exception:
            return None


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def make_backup():
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out = BACKUP_DIR / f"VanoNote_backup_{stamp}.zip"
    fd, temp_name = tempfile.mkstemp(prefix="vanonote_backup_", suffix=".db")
    os.close(fd)
    temp_db = Path(temp_name)
    try:
        source = db(); target = sqlite3.connect(temp_db)
        source.backup(target)
        target.close(); source.close()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(temp_db, "data/vanonote.db")
            for root in (ATTACH_DIR, AUDIO_DIR):
                for p in root.rglob("*"):
                    if p.is_file(): z.write(p, p.relative_to(BASE_DIR))
    finally:
        temp_db.unlink(missing_ok=True)
    return out


def backup_loop():
    time.sleep(20)
    while True:
        try:
            make_backup()
            backups = sorted(BACKUP_DIR.glob("VanoNote_backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in backups[20:]:
                old.unlink(missing_ok=True)
        except Exception as e:
            print("[backup]", e)
        time.sleep(6 * 3600)


class Handler(BaseHTTPRequestHandler):
    server_version = "VanoNote/1.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def handle_one_request(self):
        self._response_started = False
        try:
            return super().handle_one_request()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            if not self._response_started:
                self.send_json({"error": str(exc) or "invalid request"}, 400)
        except sqlite3.IntegrityError as exc:
            if not self._response_started:
                self.send_json({"error": "invalid database reference", "detail": str(exc)}, 409)
        except Exception as exc:
            self.log_error("Unhandled request error: %r", exc)
            if not self._response_started:
                self.send_json({"error": "internal server error"}, 500)

    def send_response(self, code, message=None):
        self._response_started = True
        return super().send_response(code, message)

    def is_local(self):
        return is_loopback(self.client_address[0])

    def require_local(self):
        if self.is_local(): return True
        self.send_json({"error": "This action is only available on the VanoNote Mac."}, 403)
        return False

    def share_allows_attachment(self, attachment_id, token):
        if self.is_local(): return True
        if not token: return False
        conn = db()
        row = conn.execute(
            "SELECT 1 FROM attachments a JOIN notes n ON n.id=a.note_id "
            "WHERE a.id=? AND n.share_token=? AND n.share_mode!='none' AND n.deleted_at=''",
            (attachment_id, token)).fetchone()
        conn.close(); return bool(row)

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        try: n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError): raise ValueError("Invalid Content-Length")
        if n < 0 or n > MAX_BODY:
            raise ValueError("Request too large")
        raw = self.rfile.read(n)
        if len(raw) != n: raise ValueError("Incomplete request body")
        return raw

    def read_json(self):
        raw = self.read_body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def serve_file(self, fp: Path, ctype=None, disposition=None, cache=False):
        if not fp.exists() or not fp.is_file():
            self.send_error(404); return
        data = fp.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or mimetypes.guess_type(fp.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public,max-age=86400" if cache else "no-store")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        q = urllib.parse.parse_qs(u.query)
        if path in ("/", "/index.html") or path.startswith("/s/"):
            return self.serve_file(BASE_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/manifest.json":
            return self.serve_file(BASE_DIR / "manifest.json", "application/manifest+json")
        if path.startswith("/assets/"):
            rel = Path(path[len("/assets/"):]).name
            return self.serve_file(ASSETS_DIR / rel, cache=False)
        if path == "/api/ping":
            return self.send_json({"ok": True, "app": "VanoNote", "port": PORT})
        private = not (path.startswith("/api/shared/") or path.startswith("/file/") or path.startswith("/pdfpage/"))
        if private and not self.require_local(): return
        if path == "/api/bootstrap":
            conn = db()
            folders = [dict(r) for r in conn.execute("SELECT * FROM folders ORDER BY sort_order,name")]
            notes = [note_meta(r) for r in conn.execute("SELECT * FROM notes ORDER BY updated_at DESC")]
            conn.close()
            return self.send_json({"folders": folders, "notes": notes, "fitz": HAVE_FITZ, "lan": lan_ip(), "port": PORT})
        m = re.fullmatch(r"/api/notes/(\d+)", path)
        if m:
            conn = db(); n = get_note(conn, int(m.group(1))); conn.close()
            return self.send_json(n or {"error": "not found"}, 200 if n else 404)
        m = re.fullmatch(r"/api/notes/(\d+)/versions", path)
        if m:
            conn = db(); rows = [dict(r) for r in conn.execute("SELECT id,title,created_at FROM versions WHERE note_id=? ORDER BY id DESC LIMIT 100", (int(m.group(1)),))]; conn.close()
            return self.send_json(rows)
        m = re.fullmatch(r"/api/shared/([A-Za-z0-9_\-]+)", path)
        if m:
            token = m.group(1); conn = db()
            r = conn.execute("SELECT id,share_mode FROM notes WHERE share_token=? AND deleted_at=''", (token,)).fetchone()
            if not r: conn.close(); return self.send_json({"error":"invalid share link"},404)
            n = get_note(conn, r["id"], token); conn.close(); n["shared_mode"] = r["share_mode"]
            return self.send_json(n)
        m = re.fullmatch(r"/file/(\d+)", path)
        if m:
            aid = int(m.group(1)); token = (q.get("share") or [""])[0]
            if not self.share_allows_attachment(aid, token): return self.send_json({"error":"forbidden"},403)
            conn = db(); r = conn.execute("SELECT * FROM attachments WHERE id=?", (aid,)).fetchone(); conn.close()
            if not r: return self.send_error(404)
            fp = BASE_DIR / r["rel_path"]
            return self.serve_file(fp, r["mime"], f'inline; filename="{safe_name(r["name"])}"')
        m = re.fullmatch(r"/pdfpage/(\d+)/(\d+)", path)
        if m:
            aid, page = int(m.group(1)), int(m.group(2))
            token = (q.get("share") or [""])[0]
            if not self.share_allows_attachment(aid, token): return self.send_json({"error":"forbidden"},403)
            hi = hires_page(aid, page) if (q.get("v") or [""])[0] == "hi" else None
            return self.serve_file(hi or (ATTACH_DIR / f"pdf_{aid}" / f"page_{page:04d}.png"), "image/png", cache=True)
        m = re.fullmatch(r"/api/versions/(\d+)", path)
        if m:
            conn = db(); r = conn.execute("SELECT * FROM versions WHERE id=?", (int(m.group(1)),)).fetchone(); conn.close()
            if not r: return self.send_json({"error":"not found"},404)
            d=dict(r); d["content"]=json.loads(d.pop("content_json")); return self.send_json(d)
        if path == "/api/backups":
            arr=[{"name":p.name,"size":p.stat().st_size,"mtime":datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds')} for p in sorted(BACKUP_DIR.glob('*.zip'), reverse=True)]
            return self.send_json(arr)
        if path.startswith("/backup/"):
            name=Path(path[len('/backup/'):]).name
            return self.serve_file(BACKUP_DIR/name,"application/zip",f'attachment; filename="{name}"')
        self.send_error(404)

    def do_POST(self):
        u=urllib.parse.urlparse(self.path); path=u.path; q=urllib.parse.parse_qs(u.query)
        if path != "/api/upload" and not self.require_local(): return
        if path == "/api/notes":
            return self.send_json(create_note(self.read_json()),201)
        if path == "/api/folders":
            o=self.read_json(); t=now_iso(); conn=db()
            cur=conn.execute("INSERT INTO folders(parent_id,name,color,sort_order,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                             (o.get('parent_id') or None, str(o.get('name') or 'Folder')[:200], o.get('color') or '#355e4a', int(o.get('sort_order') or 0), t,t)); conn.commit(); rid=cur.lastrowid; r=dict(conn.execute('SELECT * FROM folders WHERE id=?',(rid,)).fetchone()); conn.close(); return self.send_json(r,201)
        m=re.fullmatch(r"/api/notes/(\d+)/restore",path)
        if m:
            conn=db(); conn.execute("UPDATE notes SET deleted_at='',updated_at=?,revision=revision+1 WHERE id=?",(now_iso(),int(m.group(1)))); conn.commit(); n=get_note(conn,int(m.group(1))); conn.close(); return self.send_json(n)
        m=re.fullmatch(r"/api/notes/(\d+)/duplicate",path)
        if m:
            o=duplicate_note(int(m.group(1)))
            return self.send_json(o or {'error':'not found'},201 if o else 404)
        m=re.fullmatch(r"/api/notes/(\d+)/share",path)
        if m:
            o=self.read_json(); mode=o.get('mode','view')
            if mode not in ('none','view','edit'): mode='view'
            conn=db(); row=conn.execute('SELECT share_token FROM notes WHERE id=?',(int(m.group(1)),)).fetchone()
            if not row: conn.close(); return self.send_json({'error':'not found'},404)
            token=row['share_token'] or secrets.token_urlsafe(16)
            if mode=='none': token=''
            conn.execute('UPDATE notes SET share_token=?,share_mode=?,updated_at=? WHERE id=?',(token,mode,now_iso(),int(m.group(1)))); conn.commit(); conn.close()
            host=lan_ip(); url=f"http://{host}:{PORT}/s/{token}" if token else ''
            return self.send_json({'mode':mode,'token':token,'url':url})
        m=re.fullmatch(r"/api/notes/(\d+)/restore-version/(\d+)",path)
        if m:
            nid,vid=map(int,m.groups()); conn=db(); old=conn.execute('SELECT * FROM notes WHERE id=?',(nid,)).fetchone(); v=conn.execute('SELECT * FROM versions WHERE id=? AND note_id=?',(vid,nid)).fetchone()
            if not old or not v: conn.close(); return self.send_json({'error':'not found'},404)
            snapshot_if_needed(conn,old,force=True)
            conn.execute('UPDATE notes SET title=?,content_json=?,plain_text=?,transcript=?,updated_at=?,revision=revision+1 WHERE id=?',(v['title'],v['content_json'],v['plain_text'],v['transcript'],now_iso(),nid)); conn.commit(); n=get_note(conn,nid); conn.close(); return self.send_json(n)
        if path == "/api/upload":
            nid=int((q.get('note_id') or ['0'])[0]); kind=(q.get('kind') or ['file'])[0]; name=safe_name((q.get('name') or ['file'])[0]); raw=self.read_body()
            if not nid: return self.send_json({'error':'note_id required'},400)
            if kind not in {'file','audio','image','pdf','document','slides'}: kind='file'
            share_token=(q.get('share') or [''])[0]
            conn=db(); note=conn.execute("SELECT share_token,share_mode,deleted_at FROM notes WHERE id=?",(nid,)).fetchone(); conn.close()
            if not note: return self.send_json({'error':'note not found'},404)
            if not self.is_local() and not (share_token and secrets.compare_digest(note['share_token'] or '',share_token) and note['share_mode']=='edit' and not note['deleted_at']):
                return self.send_json({'error':'forbidden'},403)
            ext=Path(name).suffix; stem=Path(name).stem; unique=f"{int(time.time())}_{secrets.token_hex(4)}_{safe_name(stem)}{ext}"
            dest=(AUDIO_DIR if kind=='audio' else ATTACH_DIR)/unique; dest.write_bytes(raw)
            mime=self.headers.get('Content-Type') or mimetypes.guess_type(name)[0] or 'application/octet-stream'
            try:
                conn=db(); cur=conn.execute('INSERT INTO attachments(note_id,kind,name,mime,rel_path,created_at) VALUES(?,?,?,?,?,?)',(nid,kind,name,mime,str(dest.relative_to(BASE_DIR)),now_iso())); conn.commit(); aid=cur.lastrowid; conn.close()
            except Exception:
                safe_unlink(dest); raise
            suffix='?share='+urllib.parse.quote(share_token) if share_token else ''
            result={'id':aid,'name':name,'kind':kind,'mime':mime,'url':f'/file/{aid}{suffix}','pages':[]}
            pdf_source=None
            if kind=='pdf' or mime=='application/pdf' or ext.lower()=='.pdf': pdf_source=dest
            elif kind in ('document','slides') or ext.lower() in ('.doc','.docx','.ppt','.pptx','.odt','.odp'):
                pdf_source,err=office_to_pdf(dest); result['conversion_error']=err
            if pdf_source:
                pages,err=render_pdf_pages(pdf_source,aid,share_token); result['pages']=pages; result['render_error']=err
            return self.send_json(result,201)
        if path == "/api/backup":
            p=make_backup(); return self.send_json({'ok':True,'name':p.name,'url':'/backup/'+p.name})
        self.send_error(404)

    def do_PUT(self):
        path=urllib.parse.urlparse(self.path).path
        if not path.startswith('/api/shared/') and not self.require_local(): return
        m=re.fullmatch(r"/api/notes/(\d+)",path)
        if m:
            n=update_note(int(m.group(1)),self.read_json()); return self.send_json(n or {'error':'not found'},200 if n else 404)
        m=re.fullmatch(r"/api/shared/([A-Za-z0-9_\-]+)",path)
        if m:
            token=m.group(1); conn=db(); r=conn.execute("SELECT id,share_mode FROM notes WHERE share_token=? AND deleted_at=''",(token,)).fetchone(); conn.close()
            if not r: return self.send_json({'error':'invalid share link'},404)
            if r['share_mode']!='edit': return self.send_json({'error':'read only'},403)
            n=update_note(r['id'],self.read_json(),shared=True); return self.send_json(n)
        m=re.fullmatch(r"/api/folders/(\d+)",path)
        if m:
            o=self.read_json(); fields=[]; vals=[]
            for k in ('parent_id','name','color','sort_order'):
                if k in o: fields.append(k+'=?'); vals.append(o[k] or None if k=='parent_id' else o[k])
            fields.append('updated_at=?'); vals.append(now_iso()); vals.append(int(m.group(1))); conn=db(); conn.execute('UPDATE folders SET '+','.join(fields)+' WHERE id=?',vals); conn.commit(); r=conn.execute('SELECT * FROM folders WHERE id=?',(int(m.group(1)),)).fetchone(); conn.close(); return self.send_json(dict(r) if r else {'error':'not found'},200 if r else 404)
        self.send_error(404)

    def do_DELETE(self):
        path=urllib.parse.urlparse(self.path).path
        if not self.require_local(): return
        m=re.fullmatch(r"/api/notes/(\d+)",path)
        if m:
            conn=db(); conn.execute("UPDATE notes SET deleted_at=?,updated_at=? WHERE id=?",(now_iso(),now_iso(),int(m.group(1)))); conn.commit(); conn.close(); return self.send_json({'ok':True})
        m=re.fullmatch(r"/api/notes/(\d+)/permanent",path)
        if m:
            nid=int(m.group(1)); conn=db(); delete_note_files(conn,nid); conn.execute('DELETE FROM notes WHERE id=?',(nid,)); conn.commit(); conn.close(); return self.send_json({'ok':True})
        m=re.fullmatch(r"/api/folders/(\d+)",path)
        if m:
            conn=db(); conn.execute('UPDATE notes SET folder_id=NULL WHERE folder_id=?',(int(m.group(1)),)); conn.execute('DELETE FROM folders WHERE id=?',(int(m.group(1)),)); conn.commit(); conn.close(); return self.send_json({'ok':True})
        self.send_error(404)


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(.25); return s.connect_ex(('127.0.0.1',port))==0


def main():
    init_db()
    if port_busy(PORT):
        print(f"VanoNote already appears to be running on http://localhost:{PORT}")
        if os.environ.get('VANONOTE_SILENT')!='1': webbrowser.open(f'http://localhost:{PORT}')
        return
    threading.Thread(target=backup_loop,daemon=True).start()
    httpd=ThreadingHTTPServer(('0.0.0.0',PORT),Handler)
    print(f"VanoNote: http://localhost:{PORT}")
    print(f"Classroom/LAN: http://{lan_ip()}:{PORT}")
    print(f"Data folder: {BASE_DIR}")
    if os.environ.get('VANONOTE_SILENT')!='1':
        threading.Timer(.6,lambda:webbrowser.open(f'http://localhost:{PORT}')).start()
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    finally: httpd.server_close()

if __name__=='__main__':
    main()
