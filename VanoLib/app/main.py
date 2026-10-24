#!/usr/bin/env python3

import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from library import Library

UI = Path(__file__).resolve().parent / "ui"
LIB = Library()
WINDOW = None

API = {
    "overview": LIB.overview,
    "articles": LIB.articles,
    "article": LIB.article,
    "desk": LIB.desk,
    "toggle_read": LIB.toggle_read,
    "bibtex": LIB.bibtex,
    "get_reader": LIB.get_reader,
    "save_state": LIB.save_state,
    "save_highlights": LIB.save_highlights,
    "ensure_pdf": LIB.ensure_pdf,
    "sync": lambda force=False: LIB.start_sync(force=bool(force)),
    "sync_status": lambda: LIB.sync,
    "settings": lambda **kw: LIB.set_settings(**kw) if kw else LIB.get_settings(),
    "open_url": LIB.open_url,
    "import_pdf": LIB.import_pdf,
    "update_mine": LIB.update_mine,
    "delete_mine": LIB.delete_mine,
}


def toggle_fullscreen():
    if WINDOW is not None:
        WINDOW.toggle_fullscreen()
    return {"ok": WINDOW is not None}


API["fullscreen"] = toggle_fullscreen


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)) or body is None:
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype=None):
        data = path.read_bytes()
        ctype = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            a = int(a or 0)
            b = int(b) if b else len(data) - 1
            chunk = data[a:b + 1]
            return self._send(206, chunk, ctype, {"Accept-Ranges": "bytes",
                                                  "Content-Range": f"bytes {a}-{a + len(chunk) - 1}/{len(data)}"})
        self._send(200, data, ctype, {"Accept-Ranges": "bytes"})

    def _api(self, name, params):
        fn = API.get(name)
        if not fn:
            return self._send(404, {"error": "unknown api"})
        try:
            self._send(200, fn(**params))
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_GET(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        if p.startswith("/api/"):
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            return self._api(p[5:], q)
        if p.startswith("/pdf/"):
            f = LIB.pdf_path(p[5:])
            return self._file(f, "application/pdf") if f else self._send(404, {"error": "PDF not downloaded"})
        if p in ("/", "/index.html"):
            return self._file(UI / "index.html", "text/html; charset=utf-8")
        f = (UI / p.lstrip("/")).resolve()
        if UI in f.parents and f.is_file():
            return self._file(f)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            params = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            params = {}
        if u.path.startswith("/api/"):
            return self._api(u.path[5:], params)
        self._send(404, {"error": "not found"})


def main():
    global WINDOW
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    LIB.start_sync()
    print(f"VanoLib running at {url}")
    if "--browser" not in sys.argv and "--server" not in sys.argv:
        try:
            import webview
            WINDOW = webview.create_window("VanoLib", url, width=1440, height=920,
                                           min_size=(1000, 640), background_color="#f4f1ea")
            webview.start()
            return
        except Exception as e:
            print(f"pywebview unavailable ({e}) — opening in your browser instead.")
    if "--server" not in sys.argv:
        import webbrowser
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
