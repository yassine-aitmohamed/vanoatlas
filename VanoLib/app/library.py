
import json
import os
import re
import sqlite3
import ssl
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PDFS = ROOT / "pdfs"
DB = DATA / "math_articles.db"
STORE = DATA / "vanolib.json"
SITE_DATA = Path(os.environ.get("VANOLIB_SITE_DATA", str(ROOT / "site-data")))

UA = "VanoLib/7.0 (personal math library; research use)"
ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom"}

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()

DEFAULT_CATS = [
    "math.DG", "math.SG", "math-ph", "math.AG", "math.QA", "math.RT",
    "math.AC", "math.DS", "math.GT", "math.KT", "math.AT", "math.NT",
    "math.GR", "math.RA", "math.OA", "math.CV", "math.FA",
]
DEFAULT_SETTINGS = {
    "concept": "A",
    "dark_a": False,
    "categories": DEFAULT_CATS,
    "pinned": ["math.SG", "math.AG", "math.DG", "math-ph", "math.QA"],
    "reader_theme": {"A": "day", "B": "night", "C": "day"},
}

COLS = "uid,title,authors,abstract,category,published,link,pdf_link,ext_id,local_pdf_path"


def _row(r, to_read=None, state=None):
    d = dict(zip(COLS.split(","), r))
    d["authors"] = (d["authors"] or "").replace("; ", ", ").replace(";", ",")
    d["year"] = (d["published"] or "")[:4]
    d["has_pdf"] = bool(d["local_pdf_path"]) and Path(d["local_pdf_path"]).exists()
    d.pop("local_pdf_path", None)
    if to_read is not None:
        d["to_read"] = d["uid"] in to_read
    if state is not None:
        s = state.get(d["uid"])
        d["progress"] = round(100 * s.get("ratio", 0)) if s else 0
        d["pages"] = s.get("pages") if s else None
        d["page"] = s.get("page") if s else None
    return d


class Library:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        PDFS.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.sync = {"running": False, "msg": "", "new": 0}
        self._cats_cache = None
        self._init_db()
        self.store = self._load_store()
        threading.Thread(target=self._import_site_data, daemon=True).start()
        threading.Thread(target=self._speed_indexes, daemon=True).start()

    def _db(self):
        c = sqlite3.connect(str(DB), timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_db(self):
        c = self._db()
        c.execute("""CREATE TABLE IF NOT EXISTS articles (
            uid TEXT PRIMARY KEY, source TEXT NOT NULL, ext_id TEXT, title TEXT,
            authors TEXT, abstract TEXT, category TEXT, published TEXT, link TEXT,
            pdf_link TEXT, last_fetched TIMESTAMP, local_pdf_path TEXT)""")
        try:
            c.execute("ALTER TABLE articles ADD COLUMN local_pdf_path TEXT")
        except Exception:
            pass
        c.execute("CREATE TABLE IF NOT EXISTS to_read (uid TEXT PRIMARY KEY, added_date TIMESTAMP)")
        c.execute("""CREATE TABLE IF NOT EXISTS fetch_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetch_date TIMESTAMP, source TEXT, status TEXT, count INTEGER)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pub ON articles(published)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cat ON articles(category)")
        c.commit()
        c.close()

    def _speed_indexes(self):
        try:
            c = sqlite3.connect(str(DB), timeout=60)
            c.execute("CREATE INDEX IF NOT EXISTS idx_cat_pub ON articles(category, published)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_has_pdf ON articles(uid) "
                      "WHERE local_pdf_path IS NOT NULL AND local_pdf_path!=''")
            c.execute("ANALYZE articles")
            c.commit()
            c.close()
        except Exception:
            pass

    def _load_store(self):
        s = {}
        try:
            if STORE.exists():
                s = json.loads(STORE.read_text())
        except Exception:
            s = {}
        st = s.setdefault("settings", {})
        for k, v in DEFAULT_SETTINGS.items():
            st.setdefault(k, v)
        s.setdefault("state", {})
        s.setdefault("highlights", {})
        s.setdefault("log", {})
        return s

    def _save_store(self):
        with self.lock:
            tmp = STORE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.store, ensure_ascii=False))
            tmp.replace(STORE)

    def _to_read(self, c):
        return {r[0] for r in c.execute("SELECT uid FROM to_read")}

    def get_settings(self):
        return self.store["settings"]

    def set_settings(self, **kw):
        self.store["settings"].update({k: v for k, v in kw.items() if k in DEFAULT_SETTINGS})
        self._save_store()
        return self.store["settings"]

    def overview(self):
        c = self._db()
        total = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        toread = c.execute("SELECT COUNT(*) FROM to_read").fetchone()[0]
        pdfs = c.execute("SELECT COUNT(*) FROM articles WHERE local_pdf_path IS NOT NULL AND local_pdf_path!=''").fetchone()[0]
        c.close()
        st = self.store["state"]
        cont = sum(1 for s in st.values() if 0 < s.get("ratio", 0) < 0.97)
        last = self.last_sync()
        return {"total": total, "to_read": toread, "pdfs": pdfs, "in_progress": cont,
                "categories": self.categories(), "last_sync": last.isoformat() if last else None,
                "settings": self.get_settings(), "sync": self.sync}

    def categories(self):
        if self._cats_cache is None:
            c = self._db()
            self._cats_cache = {r[0]: r[1] for r in c.execute(
                "SELECT category, COUNT(*) FROM articles WHERE category!='' GROUP BY category ORDER BY 2 DESC")}
            c.close()
        return self._cats_cache

    def articles(self, filter="all", cat="", q="", sort="new", offset=0, limit=60):
        offset, limit = int(offset), min(int(limit), 200)
        where, args = [], []
        st = self.store["state"]
        if filter == "read":
            where.append("uid IN (SELECT uid FROM to_read)")
        elif filter == "pdf":
            where.append("local_pdf_path IS NOT NULL AND local_pdf_path!=''")
        elif filter == "mine":
            where.append("source='mine'")
        elif filter == "cont":
            uids = [u for u, s in st.items() if 0 < s.get("ratio", 0) < 0.97]
            where.append("uid IN (%s)" % ",".join("?" * len(uids)) if uids else "0")
            args += uids
        if cat:
            where.append("category=?")
            args.append(cat)
        q = (q or "").strip()
        if q:
            for w in q.split()[:6]:
                like = f"%{w}%"
                where.append("(title LIKE ? OR authors LIKE ? OR abstract LIKE ? OR ext_id LIKE ?)")
                args += [like, like, like, like]
        order = {"new": "published DESC", "old": "published ASC", "az": "title COLLATE NOCASE ASC"}.get(sort, "published DESC")
        sql_w = (" WHERE " + " AND ".join(where)) if where else ""
        c = self._db()
        rows = c.execute(f"SELECT {COLS} FROM articles{sql_w} ORDER BY {order} LIMIT ? OFFSET ?",
                         args + [limit, offset]).fetchall()
        total = None
        if offset == 0:
            total = c.execute(f"SELECT COUNT(*) FROM articles{sql_w}", args).fetchone()[0]
        tr = self._to_read(c)
        c.close()
        items = [_row(r, tr, st) for r in rows]
        if filter == "cont":
            items.sort(key=lambda a: -(st.get(a["uid"], {}).get("updated", 0)))
        return {"items": items, "total": total, "offset": offset}

    def article(self, uid):
        c = self._db()
        r = c.execute(f"SELECT {COLS} FROM articles WHERE uid=?", (uid,)).fetchone()
        tr = self._to_read(c)
        c.close()
        if not r:
            return None
        return _row(r, tr, self.store["state"])

    def desk(self):
        st = self.store["state"]
        cont_uids = sorted([u for u, s in st.items() if 0 < s.get("ratio", 0) < 0.97],
                           key=lambda u: -st[u].get("updated", 0))[:8]
        c = self._db()
        tr = self._to_read(c)

        def fetch(uids):
            if not uids:
                return []
            rows = c.execute(f"SELECT {COLS} FROM articles WHERE uid IN ({','.join('?' * len(uids))})", uids).fetchall()
            m = {r[0]: _row(r, tr, st) for r in rows}
            return [m[u] for u in uids if u in m]

        cont = fetch(cont_uids)
        queue = c.execute("SELECT uid FROM to_read ORDER BY added_date DESC LIMIT 12").fetchall()
        queue = fetch([r[0] for r in queue])
        pinned = self.store["settings"].get("pinned") or DEFAULT_CATS
        rows = c.execute(f"SELECT {COLS} FROM articles WHERE +category IN ({','.join('?' * len(pinned))}) "
                         "ORDER BY published DESC LIMIT 40", pinned).fetchall()
        feed = [_row(r, tr, st) for r in rows]
        week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        new_week = c.execute("SELECT COUNT(*) FROM articles WHERE published>=?", (week_start,)).fetchone()[0]
        c.close()
        today = datetime.now().date()
        days = [(today - timedelta(days=6 - i)) for i in range(7)]
        log = self.store["log"]
        return {"continue": cont, "queue": queue, "feed": feed, "new_week": new_week,
                "week": [{"d": d.strftime("%a")[0], "pages": log.get(d.isoformat(), 0)} for d in days],
                "pages_week": sum(log.get(d.isoformat(), 0) for d in days)}

    def toggle_read(self, uid):
        c = self._db()
        if c.execute("SELECT 1 FROM to_read WHERE uid=?", (uid,)).fetchone():
            c.execute("DELETE FROM to_read WHERE uid=?", (uid,))
            v = False
        else:
            c.execute("INSERT OR IGNORE INTO to_read(uid,added_date) VALUES(?,?)", (uid, datetime.now().isoformat()))
            v = True
        c.commit()
        c.close()
        return {"to_read": v}

    def bibtex(self, uid):
        a = self.article(uid)
        if not a:
            return {"bibtex": ""}
        first = (a["authors"].split(",")[0].split() or ["anon"])[-1]
        key = re.sub(r"[^A-Za-z]", "", first) + a["year"]
        eid = re.sub(r"v\d+$", "", a.get("ext_id") or "")
        bib = (f"@misc{{{key},\n  title = {{{a['title']}}},\n  author = {{{a['authors'].replace(', ', ' and ')}}},\n"
               f"  year = {{{a['year']}}},\n  eprint = {{{eid}}},\n  archivePrefix = {{arXiv}},\n"
               f"  primaryClass = {{{a['category']}}}\n}}")
        return {"bibtex": bib}

    def get_reader(self, uid):
        return {"state": self.store["state"].get(uid), "highlights": self.store["highlights"].get(uid, [])}

    def save_state(self, uid, **s):
        old = self.store["state"].get(uid, {})
        page, pages = int(s.get("page") or 0), int(s.get("pages") or 0)
        prev_max = old.get("maxpage", 0)
        if page > prev_max:
            day = datetime.now().date().isoformat()
            self.store["log"][day] = self.store["log"].get(day, 0) + (page - prev_max if prev_max else 1)
        new = dict(old)
        new.update({k: s[k] for k in ("page", "pages", "ratio", "scale", "fit", "spread") if k in s})
        new["maxpage"] = max(prev_max, page)
        new["updated"] = time.time()
        self.store["state"][uid] = new
        self._save_store()
        return {"ok": True}

    def save_highlights(self, uid, highlights):
        if highlights:
            self.store["highlights"][uid] = highlights
        else:
            self.store["highlights"].pop(uid, None)
        self._save_store()
        return {"ok": True}

    def pdf_path(self, uid):
        c = self._db()
        r = c.execute("SELECT title, local_pdf_path, pdf_link, ext_id FROM articles WHERE uid=?", (uid,)).fetchone()
        c.close()
        if not r:
            return None
        title, local, link, ext = r
        if local and Path(local).exists():
            return Path(local)
        if local:
            alt = PDFS / Path(local).name
            if alt.exists():
                self._set_local(uid, alt)
                return alt
        return None

    def _set_local(self, uid, path):
        c = self._db()
        c.execute("UPDATE articles SET local_pdf_path=? WHERE uid=?", (str(path), uid))
        c.commit()
        c.close()

    def ensure_pdf(self, uid):
        p = self.pdf_path(uid)
        if p:
            return {"ok": True}
        c = self._db()
        r = c.execute("SELECT title, pdf_link, ext_id FROM articles WHERE uid=?", (uid,)).fetchone()
        c.close()
        if not r:
            return {"ok": False, "error": "Unknown article"}
        title, link, ext = r
        url = f"https://arxiv.org/pdf/{ext}" if ext else (link or "")
        if not url.startswith("http"):
            return {"ok": False, "error": "No PDF link for this article"}
        url = url.replace("http://", "https://")
        try:
            data = urlopen(Request(url, headers={"User-Agent": UA}), timeout=60, context=SSL_CTX).read()
        except Exception as e:
            return {"ok": False, "error": f"Download failed: {e}"}
        if b"%PDF" not in data[:1024]:
            return {"ok": False, "error": "arXiv did not return a PDF"}
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in (title or uid))[:80].strip() or "paper"
        dest = PDFS / f"{safe}.pdf"
        if dest.exists():
            dest = PDFS / f"{safe} [{(ext or uid).replace('/', '_')}].pdf"
        dest.write_bytes(data)
        self._set_local(uid, dest)
        return {"ok": True}

    def import_pdf(self, name="", data="", title="", authors="", abstract=""):
        import base64
        import hashlib
        raw = base64.b64decode((data or "").split(",")[-1])
        if b"%PDF" not in raw[:1024]:
            return {"ok": False, "error": "This file is not a PDF"}
        h = hashlib.sha1(raw).hexdigest()[:12]
        uid = "mine:" + h
        c = self._db()
        if c.execute("SELECT 1 FROM articles WHERE uid=?", (uid,)).fetchone():
            c.close()
            return {"ok": True, "uid": uid, "existing": True}
        if not title:
            m = re.search(rb"/Title\s*\(([^)]{3,200})\)", raw[:400000])
            if m:
                try:
                    title = m.group(1).decode("utf-8", "ignore").strip()
                except Exception:
                    title = ""
            if not title or title.lower() in ("untitled", "microsoft word"):
                title = re.sub(r"[_-]+", " ", Path(name or "document.pdf").stem).strip() or "My document"
        mine = PDFS / "mine"
        mine.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in title)[:80].strip() or "document"
        dest = mine / f"{safe} [{h}].pdf"
        dest.write_bytes(raw)
        now = datetime.now()
        c.execute("INSERT INTO articles (uid, source, ext_id, title, authors, abstract, category, published, link, pdf_link, "
                  "last_fetched, local_pdf_path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (uid, "mine", "", title, authors or "", abstract or "", "Mes articles", now.strftime("%Y-%m-%d"),
                   "", "", now.isoformat(timespec="seconds"), str(dest)))
        c.commit()
        c.close()
        self._cats_cache = None
        return {"ok": True, "uid": uid, "title": title}

    def update_mine(self, uid, title=None, authors=None, abstract=None):
        if not str(uid).startswith("mine:"):
            return {"ok": False, "error": "Only your own documents can be edited"}
        c = self._db()
        for col, val in (("title", title), ("authors", authors), ("abstract", abstract)):
            if val is not None:
                c.execute(f"UPDATE articles SET {col}=? WHERE uid=?", (str(val)[:2000], uid))
        c.commit()
        c.close()
        return {"ok": True}

    def delete_mine(self, uid):
        if not str(uid).startswith("mine:"):
            return {"ok": False, "error": "Only your own documents can be removed"}
        c = self._db()
        r = c.execute("SELECT local_pdf_path FROM articles WHERE uid=?", (uid,)).fetchone()
        c.execute("DELETE FROM articles WHERE uid=?", (uid,))
        c.execute("DELETE FROM to_read WHERE uid=?", (uid,))
        c.commit()
        c.close()
        if r and r[0]:
            try:
                p = Path(r[0])
                if p.exists() and (PDFS / "mine") in p.parents:
                    p.unlink()
            except Exception:
                pass
        self.store["state"].pop(uid, None)
        self._cats_cache = None
        return {"ok": True}

    def _import_site_data(self):
        try:
            man = SITE_DATA / "manifest.json"
            if not man.exists() or self.store.get("site_import", 0) >= man.stat().st_mtime:
                return
            stamp = man.stat().st_mtime
            self.sync = {"running": True, "msg": "Importing papers from the website data…", "new": 0}
            c = self._db()
            have = {re.sub(r"v\d+$", "", e or "") for (e,) in c.execute("SELECT ext_id FROM articles")}
            abstracts = {}
            try:
                for r in json.loads((SITE_DATA / "latest.json").read_text()):
                    if len(r) > 5:
                        abstracts[re.sub(r"v\d+$", "", r[0])] = r[5]
            except Exception:
                pass
            now, rows = datetime.now().isoformat(), []
            for f in sorted(SITE_DATA.glob("[0-9][0-9][0-9][0-9].json")):
                for r in json.loads(f.read_text()):
                    if not r or not r[0]:
                        continue
                    b = re.sub(r"v\d+$", "", r[0])
                    if b in have:
                        continue
                    have.add(b)
                    d = str(r[4] or "")
                    rows.append((f"arxiv:{r[0]}", "arXiv", r[0], r[1], r[2], abstracts.get(b, ""), r[3],
                                 d + "T00:00:00Z" if len(d) == 10 else d,
                                 f"https://arxiv.org/abs/{r[0]}", f"https://arxiv.org/pdf/{r[0]}", now))
            if rows:
                c.executemany("""INSERT OR IGNORE INTO articles (uid,source,ext_id,title,authors,abstract,category,
                                 published,link,pdf_link,last_fetched) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
                c.commit()
            c.close()
            self._cats_cache = None
            self.store["site_import"] = stamp
            self._save_store()
            self.sync = {"running": False, "msg": f"{len(rows)} papers imported", "new": len(rows), "done": time.time()}
        except Exception as e:
            self.sync = {"running": False, "msg": f"Import error: {e}", "new": 0, "done": time.time()}

    def last_sync(self):
        c = self._db()
        r = c.execute("SELECT MAX(fetch_date) FROM fetch_log WHERE status='ok' AND source='arXiv'").fetchone()
        c.close()
        try:
            return datetime.fromisoformat(r[0]) if r and r[0] else None
        except Exception:
            return None

    def needs_sync(self):
        last = self.last_sync()
        if not last:
            return True
        today = datetime.now().date()
        monday = today - timedelta(days=today.weekday())
        return last.date() < monday

    def start_sync(self, force=False):
        if self.sync["running"] or (not force and not self.needs_sync()):
            return self.sync
        self.sync = {"running": True, "msg": "Starting…", "new": 0}
        threading.Thread(target=self._sync_worker, daemon=True).start()
        return self.sync

    def _sync_worker(self):
        cats = self.store["settings"].get("categories") or DEFAULT_CATS
        last = self.last_sync()
        end = datetime.now(timezone.utc)
        start = (last.replace(tzinfo=timezone.utc) - timedelta(days=3)) if last else end - timedelta(days=30)
        start = max(start, end - timedelta(days=120))
        total, ok = 0, 0
        try:
            for i, cat in enumerate(cats, 1):
                self.sync["msg"] = f"{cat} ({i}/{len(cats)})"
                n = self._collect(cat, start, end)
                if n is not None:
                    ok += 1
                    total += n
                self.sync["new"] = total
                if not ok and i >= 2:
                    break
            if not ok:
                raise RuntimeError("arXiv unreachable (check your internet connection)")
            c = self._db()
            c.execute("INSERT INTO fetch_log(fetch_date,source,status,count) VALUES(?,?,?,?)",
                      (datetime.now().isoformat(), "arXiv", "ok", total))
            c.commit()
            c.close()
            self._cats_cache = None
            self.sync = {"running": False, "msg": f"{total} new papers", "new": total, "done": time.time()}
        except Exception as e:
            self.sync = {"running": False, "msg": f"Sync error: {e}", "new": total, "done": time.time()}

    def _collect(self, cat, start, end):
        q = f"cat:{cat} AND submittedDate:[{start:%Y%m%d}0000 TO {end:%Y%m%d}2359]"
        idx, saved, reached = 0, 0, False
        while True:
            params = urlencode({"search_query": q, "start": idx, "max_results": 500,
                                "sortBy": "submittedDate", "sortOrder": "descending"})
            try:
                xml = urlopen(Request(f"{ARXIV_API}?{params}", headers={"User-Agent": UA}), timeout=60, context=SSL_CTX).read()
                entries = ET.fromstring(xml).findall("a:entry", ATOM)
            except Exception:
                break
            reached = True
            if not entries:
                break
            saved += self._save(entries, cat)
            if len(entries) < 500:
                break
            idx += 500
            time.sleep(3)
        time.sleep(3)
        return saved if reached else None

    def _save(self, entries, cat):
        rows = []
        for e in entries:
            try:
                aid = e.find("a:id", ATOM).text.split("/abs/")[-1]
                t = e.find("a:title", ATOM)
                s = e.find("a:summary", ATOM)
                p = e.find("a:published", ATOM)
                auth = "; ".join(a.find("a:name", ATOM).text for a in e.findall("a:author", ATOM)
                                 if a.find("a:name", ATOM) is not None)
                rows.append((f"arxiv:{aid}", "arXiv", aid, " ".join((t.text or "").split()) if t is not None else "",
                             auth, (s.text or "").strip() if s is not None else "", cat,
                             p.text if p is not None else "", f"https://arxiv.org/abs/{aid}",
                             f"https://arxiv.org/pdf/{aid}", datetime.now().isoformat()))
            except Exception:
                continue
        c = self._db()
        before = c.total_changes
        c.executemany("""INSERT OR IGNORE INTO articles (uid,source,ext_id,title,authors,abstract,category,
                         published,link,pdf_link,last_fetched) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
        n = c.total_changes - before
        c.commit()
        c.close()
        return n

    def open_url(self, url):
        if url.startswith(("http://", "https://")):
            webbrowser.open(url)
        return {"ok": True}
