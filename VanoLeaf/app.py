#!/usr/bin/env python3

import hashlib
import io
import os
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file

BASE_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = BASE_DIR / "projects"
CONFIG_PATH = BASE_DIR / "vanoleaf_config.json"

def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
PROJECTS_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder="vendor", static_url_path="/vendor")

STATUS_CHOICES = ["a_faire", "en_cours", "relecture", "termine"]

TEXT_EXTENSIONS = {
    ".tex", ".bib", ".sty", ".cls", ".bst", ".bbx", ".cbx", ".def", ".cfg", ".clo",
    ".dtx", ".ins", ".ltx", ".txt", ".md", ".tikz", ".pgf", ".csv", ".dat", ".json",
    ".yaml", ".yml", ".py", ".r", ".rnw", ".lua", ".asy", ".gnuplot",
}
TREE_IGNORED_NAMES = {"build", "yanovano.json", ".git", "venv", "__pycache__", ".DS_Store"}
COMPILER_CHOICES = {"pdflatex", "xelatex", "lualatex", "latex"}
ACTIVE_PROCESSES = {}
ACTIVE_PROCESSES_LOCK = threading.Lock()

DEFAULT_MAIN_TEX = r"""\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{amsmath,amssymb,amsthm}
\usepackage{hyperref}

\title{%s}
\author{%s}
\date{\today}

\begin{document}
\maketitle

\section{Introduction}
Start writing here.

%% Keep bibliography entries in the separate references.bib file.
%% Example citation: \cite{your-key}
\bibliographystyle{plain}
\bibliography{references}

\end{document}
"""


TEMPLATES_DIR = BASE_DIR / "templates"
TEMPLATE_TEXT_EXT = {".tex", ".bib", ".md", ".txt", ".sty", ".cls"}
_TPL_AVAIL = {"at": 0, "found": {}}
_TPL_TEST = {"running": False, "done": 0, "total": 0, "results": {}, "at": None}


def _kpsewhich():
    exe = shutil.which("kpsewhich")
    for cand in ("/Library/TeX/texbin/kpsewhich", "/usr/local/texlive/bin/universal-darwin/kpsewhich", "/opt/homebrew/bin/kpsewhich"):
        if not exe and Path(cand).exists():
            exe = cand
    return exe


def _tpl_available(names):
    names = sorted(set(names))
    if time.time() - _TPL_AVAIL["at"] > 600 or any(n not in _TPL_AVAIL["found"] for n in names):
        exe = _kpsewhich()
        found = {}
        if exe and names:
            try:
                out = subprocess.run([exe] + names, capture_output=True, text=True, timeout=60).stdout
                have = {Path(line.strip()).name for line in out.splitlines() if line.strip()}
                found = {n: (n in have) for n in names}
            except Exception:
                found = {n: True for n in names}
        else:
            found = {n: True for n in names}
        _TPL_AVAIL.update(at=time.time(), found={**_TPL_AVAIL["found"], **found})
    return {n: _TPL_AVAIL["found"].get(n, True) for n in names}


def list_templates():
    out = []
    if not TEMPLATES_DIR.exists():
        return out
    for d in sorted(TEMPLATES_DIR.iterdir()):
        f = d / "template.json"
        if d.is_dir() and f.exists():
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
                m["id"] = d.name
                out.append(m)
            except Exception:
                pass
    avail = _tpl_available([n for m in out for n in m.get("needs", [])])
    for m in out:
        m["missing"] = [n for n in m.get("needs", []) if not avail.get(n, True)]
        m["test"] = _TPL_TEST["results"].get(m["id"])
    out.sort(key=lambda m: (m.get("order", 50), m.get("name", "")))
    return out


def _tpl_fill(text, name, author):
    return (text.replace("{{TITLE}}", name).replace("{{AUTHOR}}", author or "Author")
                .replace("{{DATE}}", datetime.now().strftime("%Y-%m-%d")))


def _tpl_copy(tid, dest, name, author):
    src = (TEMPLATES_DIR / tid).resolve()
    if TEMPLATES_DIR.resolve() not in src.parents or not (src / "template.json").exists():
        raise ValueError("unknown template")
    meta = json.loads((src / "template.json").read_text(encoding="utf-8"))
    for f in src.rglob("*"):
        if not f.is_file() or f.name == "template.json":
            continue
        t = dest / f.relative_to(src)
        t.parent.mkdir(parents=True, exist_ok=True)
        if f.suffix.lower() in TEMPLATE_TEXT_EXT:
            t.write_text(_tpl_fill(f.read_text(encoding="utf-8"), name, author), encoding="utf-8")
        else:
            shutil.copy2(f, t)
    return meta


def _tpl_selftest():
    tpls = list_templates()
    _TPL_TEST.update(running=True, done=0, total=len(tpls), results={}, at=datetime.now().isoformat(timespec="seconds"))
    exe = shutil.which("latexmk") or ("/Library/TeX/texbin/latexmk" if Path("/Library/TeX/texbin/latexmk").exists() else None)
    for m in tpls:
        if m.get("missing"):
            _TPL_TEST["results"][m["id"]] = {"ok": None, "error": "missing: " + ", ".join(m["missing"])}
            _TPL_TEST["done"] += 1
            continue
        w = Path(tempfile.mkdtemp(prefix="vanoleaf-tpl-"))
        try:
            _tpl_copy(m["id"], w, "Template test", "Author")
            eng = {"xelatex": "-pdfxe", "lualatex": "-pdflua"}.get(m.get("compiler"), "-pdf")
            r = subprocess.run([exe or "latexmk", eng, "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                               cwd=w, capture_output=True, text=True, timeout=300)
            ok = (w / "main.pdf").exists()
            err = ""
            if not ok:
                log = (w / "main.log").read_text(errors="ignore") if (w / "main.log").exists() else r.stdout
                err = " ".join([l for l in log.splitlines() if l.startswith("!")][:2]) or r.stdout[-300:]
            _TPL_TEST["results"][m["id"]] = {"ok": ok, "error": err[:300]}
        except Exception as e:
            _TPL_TEST["results"][m["id"]] = {"ok": False, "error": str(e)[:300]}
        finally:
            shutil.rmtree(w, ignore_errors=True)
            _TPL_TEST["done"] += 1
    _TPL_TEST["running"] = False


def slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return name or "projet"


def project_path(slug: str) -> Path:
    p = (PROJECTS_DIR / slug).resolve()
    if PROJECTS_DIR.resolve() not in p.parents and p != PROJECTS_DIR.resolve():
        raise ValueError("invalid project slug")
    return p


def meta_path(slug: str) -> Path:
    return project_path(slug) / "yanovano.json"


def load_meta(slug: str) -> dict:
    mp = meta_path(slug)
    if mp.exists():
        meta = json.loads(mp.read_text())
    else:
        meta = {"title": slug, "author": "", "chapters": [], "created": datetime.now().isoformat()}
    meta.setdefault("archived", False)
    meta.setdefault("trashed_at", None)
    meta.setdefault("main_document", "main.tex")
    meta.setdefault("compiler", "pdflatex")
    meta.setdefault("shell_escape", False)
    return meta


def save_meta(slug: str, meta: dict):
    meta_path(slug).write_text(json.dumps(meta, indent=2, ensure_ascii=False))


def safe_join(base: Path, rel: str) -> Path:
    fp = (base / rel).resolve()
    if base.resolve() not in fp.parents and fp != base.resolve():
        raise ValueError("invalid path")
    return fp



def is_editable_text_file(fp: Path) -> bool:
    if fp.suffix.lower() in TEXT_EXTENSIONS:
        return True
    try:
        sample = fp.read_bytes()[:4096]
    except Exception:
        return False
    return b"\x00" not in sample


def iter_project_entries(root: Path):
    for fp in sorted(root.rglob("*"), key=lambda x: (not x.is_dir(), str(x).lower())):
        rel = fp.relative_to(root)
        if any(part in TREE_IGNORED_NAMES for part in rel.parts):
            continue
        if any(part.startswith(".") and part not in {".latexmkrc"} for part in rel.parts):
            continue
        yield fp, rel


def current_main_document(slug: str) -> str:
    meta = load_meta(slug)
    main = meta.get("main_document") or "main.tex"
    try:
        fp = safe_join(project_path(slug), main)
    except ValueError:
        return "main.tex"
    if not fp.exists():
        fallback = project_path(slug) / "main.tex"
        return "main.tex" if fallback.exists() else main
    return main


def current_pdf_path(slug: str) -> Path:
    main = current_main_document(slug)
    return project_path(slug) / "build" / f"{Path(main).stem}.pdf"



def history_folder(slug: str, rel: str) -> Path:
    root = project_path(slug) / ".vanoleaf_history"
    target = (root / rel).resolve()
    if target != root.resolve() and root.resolve() not in target.parents:
        raise ValueError("invalid history path")
    return target


def snapshot_content(slug: str, rel: str, content: str, force: bool = False):
    folder = history_folder(slug, rel)
    folder.mkdir(parents=True, exist_ok=True)
    snapshots = sorted(folder.glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True)
    if snapshots:
        try:
            if snapshots[0].read_text(encoding="utf-8", errors="replace") == content:
                return snapshots[0].name
        except Exception:
            pass
        if not force and time.time() - snapshots[0].stat().st_mtime < 60:
            return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out = folder / f"{stamp}.txt"
    out.write_text(content, encoding="utf-8")
    snapshots = sorted(folder.glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True)
    for old in snapshots[120:]:
        try:
            old.unlink()
        except Exception:
            pass
    return out.name


def safe_extract_zip(zf: zipfile.ZipFile, destination: Path):
    destination = destination.resolve()
    for member in zf.infolist():
        target = (destination / member.filename).resolve()
        if target != destination and destination not in target.parents:
            raise ValueError(f"unsafe zip member: {member.filename}")
    zf.extractall(destination)


@app.route("/")
def index():
    return send_file(BASE_DIR / "index.html")


@app.route("/api/tex-check")
def tex_check():
    exe = shutil.which("pdflatex")
    version = None
    if exe:
        try:
            out = subprocess.run(
                ["pdflatex", "--version"], capture_output=True, text=True, timeout=10
            )
            version = out.stdout.splitlines()[0] if out.stdout else None
        except Exception:
            pass
    return jsonify({
        "available": bool(exe),
        "path": exe,
        "version": version,
        "synctex_available": bool(shutil.which("synctex")),
        "latexmk_available": bool(shutil.which("latexmk")),
        "bibtex_available": bool(shutil.which("bibtex")),
        "biber_available": bool(shutil.which("biber")),
        "compilers": {name: bool(shutil.which(name)) for name in sorted(COMPILER_CHOICES)},
    })


@app.route("/api/projects", methods=["GET"])
def list_projects():
    projects = []
    for p in sorted(PROJECTS_DIR.iterdir()):
        if p.is_dir():
            meta = load_meta(p.name)
            projects.append({"slug": p.name, **meta})
    projects.sort(key=lambda x: x.get("updated") or x.get("created") or "", reverse=True)
    return jsonify(projects)


def touch_meta(slug: str):
    meta = load_meta(slug)
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return meta


@app.route("/api/projects", methods=["POST"])
def create_project():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    author = (data.get("author") or "").strip()
    tid = (data.get("template") or "").strip()
    if not name:
        return jsonify({"error": "missing name"}), 400
    slug = slugify(name)
    p = project_path(slug)
    if p.exists():
        return jsonify({"error": "a project with this name already exists"}), 400
    p.mkdir(parents=True)
    tmeta = {}
    if tid:
        try:
            tmeta = _tpl_copy(tid, p, name, author)
        except Exception as e:
            shutil.rmtree(p, ignore_errors=True)
            return jsonify({"error": f"template: {e}"}), 400
    (p / "chapters").mkdir(exist_ok=True)
    (p / "build").mkdir(exist_ok=True)
    if not tid:
        (p / "main.tex").write_text(DEFAULT_MAIN_TEX % (name, author or "Author"))
        (p / "references.bib").write_text("")
    now = datetime.now().isoformat()
    meta = {
        "title": name,
        "author": author,
        "chapters": [],
        "created": now,
        "updated": now,
        "last_compiled": None,
        "last_compile_ok": None,
        "archived": False,
        "trashed_at": None,
        "main_document": "main.tex",
        "compiler": tmeta.get("compiler") if tmeta.get("compiler") in COMPILER_CHOICES else "pdflatex",
        "shell_escape": False,
        "template": tid or None,
    }
    save_meta(slug, meta)
    return jsonify({"slug": slug, **meta})


@app.route("/api/templates")
def templates_list():
    return jsonify(list_templates())


@app.route("/api/templates/selftest", methods=["GET", "POST"])
def templates_selftest():
    if request.method == "POST" and not _TPL_TEST["running"]:
        threading.Thread(target=_tpl_selftest, daemon=True).start()
        time.sleep(0.2)
    return jsonify({k: v for k, v in _TPL_TEST.items()})


@app.route("/api/projects/<slug>/duplicate", methods=["POST"])
def duplicate_project(slug):
    src = project_path(slug)
    if not src.exists():
        return jsonify({"error": "project not found"}), 404
    meta = load_meta(slug)
    base_title = f"{meta.get('title', slug)} (Copy)"
    new_title = base_title
    new_slug = slugify(new_title)
    n = 2
    while project_path(new_slug).exists():
        new_title = f"{base_title} ({n})"
        new_slug = slugify(new_title)
        n += 1
    dest = project_path(new_slug)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("build", ".vanoleaf_history"))
    (dest / "build").mkdir(exist_ok=True)
    now = datetime.now().isoformat()
    new_meta = dict(meta)
    new_meta["title"] = new_title
    new_meta["created"] = now
    new_meta["updated"] = now
    new_meta["last_compiled"] = None
    new_meta["last_compile_ok"] = None
    new_meta["archived"] = False
    new_meta["trashed_at"] = None
    save_meta(new_slug, new_meta)
    return jsonify({"slug": new_slug, **new_meta})


@app.route("/api/projects/<slug>", methods=["DELETE"])
def delete_project(slug):
    p = project_path(slug)
    if p.exists():
        shutil.rmtree(p)
    return jsonify({"ok": True})


@app.route("/api/projects/<slug>", methods=["PATCH"])
def rename_project(slug):
    data = request.get_json(force=True) or {}
    meta = load_meta(slug)
    if "title" in data and data["title"].strip():
        meta["title"] = data["title"].strip()
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify({"slug": slug, **meta})


@app.route("/api/projects/<slug>/archive", methods=["POST"])
def archive_project(slug):
    meta = load_meta(slug)
    meta["archived"] = True
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify({"slug": slug, **meta})


@app.route("/api/projects/<slug>/unarchive", methods=["POST"])
def unarchive_project(slug):
    meta = load_meta(slug)
    meta["archived"] = False
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify({"slug": slug, **meta})


@app.route("/api/projects/<slug>/trash", methods=["POST"])
def trash_project(slug):
    meta = load_meta(slug)
    meta["trashed_at"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify({"slug": slug, **meta})


@app.route("/api/projects/<slug>/restore", methods=["POST"])
def restore_project(slug):
    meta = load_meta(slug)
    meta["trashed_at"] = None
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify({"slug": slug, **meta})


@app.route("/api/projects/import", methods=["POST"])
def import_project():
    name = (request.form.get("name") or "").strip()
    up = request.files.get("file")
    if not name:
        return jsonify({"error": "missing name"}), 400
    if not up:
        return jsonify({"error": "missing file"}), 400

    slug = slugify(name)
    p = project_path(slug)
    if p.exists():
        return jsonify({"error": "a project with this name already exists"}), 400

    p.mkdir(parents=True)
    (p / "build").mkdir()

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        zpath = tdp / "upload.zip"
        up.save(zpath)
        try:
            with zipfile.ZipFile(zpath) as zf:
                safe_extract_zip(zf, tdp / "extracted")
        except (zipfile.BadZipFile, ValueError):
            shutil.rmtree(p)
            return jsonify({"error": "invalid .zip file"}), 400

        extracted = tdp / "extracted"
        entries = list(extracted.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            extracted = entries[0]

        for item in extracted.iterdir():
            dest = p / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

    (p / "chapters").mkdir(exist_ok=True)
    if not (p / "references.bib").exists():
        (p / "references.bib").write_text("")

    main_tex = p / "main.tex"
    main_document = "main.tex"
    if not main_tex.exists():
        candidate = None
        for f in p.rglob("*.tex"):
            try:
                if "\\documentclass" in f.read_text(errors="ignore"):
                    candidate = f
                    break
            except Exception:
                continue
        if candidate:
            main_document = str(candidate.relative_to(p))

    now = datetime.now().isoformat()
    meta = {
        "title": name,
        "author": "",
        "chapters": [],
        "created": now,
        "updated": now,
        "last_compiled": None,
        "last_compile_ok": None,
        "imported": True,
        "archived": False,
        "trashed_at": None,
        "main_document": main_document,
        "compiler": "pdflatex",
        "shell_escape": False,
    }
    save_meta(slug, meta)
    return jsonify({"slug": slug, **meta})


@app.route("/api/projects/<slug>/export")
def export_project(slug):
    p = project_path(slug)
    if not p.exists():
        return jsonify({"error": "project not found"}), 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(p)
            if rel.parts and rel.parts[0] in {"build", ".vanoleaf_history"}:
                continue
            if f.name == "yanovano.json":
                continue
            zf.write(f, str(rel))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{slug}.zip",
    )


@app.route("/api/projects/<slug>/files")
def list_files(slug):
    p = project_path(slug)
    files = [str(rel) for fp, rel in iter_project_entries(p) if fp.is_file() and is_editable_text_file(fp)]
    return jsonify(files)


@app.route("/api/projects/<slug>/tree")
def project_tree(slug):
    p = project_path(slug)
    entries = []
    for fp, rel in iter_project_entries(p):
        entries.append({
            "path": str(rel),
            "name": fp.name,
            "type": "folder" if fp.is_dir() else "file",
            "editable": bool(fp.is_file() and is_editable_text_file(fp)),
            "size": fp.stat().st_size if fp.is_file() else None,
        })
    return jsonify({"entries": entries, "main_document": current_main_document(slug)})


@app.route("/api/projects/<slug>/file")
def get_file(slug):
    rel = request.args.get("path", "")
    try:
        fp = safe_join(project_path(slug), rel)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    if not fp.exists() or not fp.is_file():
        return jsonify({"error": "file not found"}), 404
    if not is_editable_text_file(fp):
        return jsonify({"error": "binary file", "editable": False, "path": rel}), 415
    try:
        content = fp.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = fp.read_text(encoding="utf-8", errors="replace")
    return jsonify({"path": rel, "content": content, "editable": True})


@app.route("/api/projects/<slug>/file", methods=["POST"])
def save_file(slug):
    data = request.get_json(force=True) or {}
    rel = data.get("path", "")
    content = data.get("content", "")
    try:
        fp = safe_join(project_path(slug), rel)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    if fp.exists() and fp.is_dir():
        return jsonify({"error": "path is a folder"}), 400
    fp.parent.mkdir(parents=True, exist_ok=True)
    if fp.exists():
        try:
            previous = fp.read_text(encoding="utf-8", errors="replace")
            if previous != content:
                snapshot_content(slug, rel, previous, force=False)
        except Exception:
            pass
    fp.write_text(content, encoding="utf-8")
    touch_meta(slug)
    return jsonify({"ok": True, "path": rel})


@app.route("/api/projects/<slug>/entry", methods=["POST"])
def create_entry(slug):
    data = request.get_json(force=True) or {}
    rel = (data.get("path") or "").strip().strip("/")
    kind = data.get("type", "file")
    if not rel or kind not in {"file", "folder"}:
        return jsonify({"error": "invalid entry"}), 400
    try:
        fp = safe_join(project_path(slug), rel)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    if fp.exists():
        return jsonify({"error": "entry already exists"}), 409
    if kind == "folder":
        fp.mkdir(parents=True)
    else:
        fp.parent.mkdir(parents=True, exist_ok=True)
        content = data.get("content")
        if content is None:
            if fp.suffix.lower() == ".tex":
                content = "% New VanoLeaf file\n"
            elif fp.suffix.lower() == ".bib":
                content = "% Bibliography\n"
            else:
                content = ""
        fp.write_text(content, encoding="utf-8")
    touch_meta(slug)
    return jsonify({"ok": True, "path": rel, "type": kind})


@app.route("/api/projects/<slug>/entry", methods=["PATCH"])
def move_entry(slug):
    data = request.get_json(force=True) or {}
    src_rel = (data.get("path") or "").strip().strip("/")
    dst_rel = (data.get("new_path") or "").strip().strip("/")
    if not src_rel or not dst_rel:
        return jsonify({"error": "missing path/new_path"}), 400
    try:
        src = safe_join(project_path(slug), src_rel)
        dst = safe_join(project_path(slug), dst_rel)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    if not src.exists():
        return jsonify({"error": "entry not found"}), 404
    if dst.exists():
        return jsonify({"error": "destination already exists"}), 409
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    meta = load_meta(slug)
    main_doc = meta.get("main_document") or "main.tex"
    if main_doc == src_rel:
        meta["main_document"] = dst_rel
    elif main_doc.startswith(src_rel.rstrip("/") + "/"):
        meta["main_document"] = dst_rel.rstrip("/") + main_doc[len(src_rel):]
    try:
        hsrc = project_path(slug) / ".vanoleaf_history" / src_rel
        hdst = project_path(slug) / ".vanoleaf_history" / dst_rel
        if hsrc.exists():
            hdst.parent.mkdir(parents=True, exist_ok=True)
            if not hdst.exists():
                hsrc.rename(hdst)
    except Exception:
        pass
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify({"ok": True, "path": dst_rel})


@app.route("/api/projects/<slug>/entry", methods=["DELETE"])
def remove_entry(slug):
    rel = (request.args.get("path") or "").strip().strip("/")
    if not rel:
        return jsonify({"error": "missing path"}), 400
    try:
        fp = safe_join(project_path(slug), rel)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    if not fp.exists():
        return jsonify({"error": "entry not found"}), 404
    if fp.is_dir():
        shutil.rmtree(fp)
    else:
        fp.unlink()
    meta = load_meta(slug)
    main_doc = meta.get("main_document") or "main.tex"
    if main_doc == rel or main_doc.startswith(rel.rstrip("/") + "/"):
        meta["main_document"] = "main.tex"
    try:
        hpath = project_path(slug) / ".vanoleaf_history" / rel
        if hpath.is_dir():
            shutil.rmtree(hpath)
        elif hpath.exists():
            hpath.unlink()
    except Exception:
        pass
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify({"ok": True})


@app.route("/api/projects/<slug>/upload", methods=["POST"])
def upload_files(slug):
    target_dir = (request.form.get("dir") or "").strip().strip("/")
    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify({"error": "no files uploaded"}), 400
    saved = []
    for up in uploads:
        name = Path(up.filename or "").name
        if not name:
            continue
        rel = f"{target_dir}/{name}" if target_dir else name
        try:
            fp = safe_join(project_path(slug), rel)
        except ValueError:
            continue
        fp.parent.mkdir(parents=True, exist_ok=True)
        up.save(fp)
        saved.append(rel)
    touch_meta(slug)
    return jsonify({"ok": True, "files": saved})


@app.route("/api/projects/<slug>/search")
def search_project(slug):
    query = request.args.get("q", "")
    case_sensitive = request.args.get("case", "0") == "1"
    regex_mode = request.args.get("regex", "0") == "1"
    whole_word = request.args.get("word", "0") == "1"
    if not query:
        return jsonify({"results": []})
    flags = 0 if case_sensitive else re.I
    if regex_mode:
        source = query
    else:
        source = re.escape(query)
    if whole_word:
        source = rf"\b(?:{source})\b"
    try:
        matcher = re.compile(source, flags)
    except re.error as exc:
        return jsonify({"error": f"invalid regex: {exc}"}), 400
    results = []
    for fp, rel in iter_project_entries(project_path(slug)):
        if not fp.is_file() or not is_editable_text_file(fp):
            continue
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line_no, line in enumerate(lines, 1):
            for match in matcher.finditer(line):
                results.append({
                    "file": str(rel), "line": line_no, "ch": match.start(),
                    "text": line[:500],
                })
                if len(results) >= 2000:
                    return jsonify({"results": results, "truncated": True})
    return jsonify({"results": results, "truncated": False})


@app.route("/api/projects/<slug>/completion-index")
def completion_index(slug):
    labels, citations, commands, environments, files = set(), set(), set(), set(), set()
    standard_envs = {
        "document", "abstract", "equation", "equation*", "align", "align*", "gather", "gather*",
        "multline", "multline*", "split", "cases", "matrix", "pmatrix", "bmatrix", "vmatrix",
        "theorem", "lemma", "proposition", "corollary", "definition", "remark", "proof",
        "itemize", "enumerate", "description", "figure", "table", "center", "flushleft", "flushright",
        "tikzpicture", "tikzcd", "frame",
    }
    environments |= standard_envs
    for fp, rel in iter_project_entries(project_path(slug)):
        if not fp.is_file():
            continue
        files.add(str(rel))
        if not is_editable_text_file(fp):
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if fp.suffix.lower() == ".bib":
            citations |= set(re.findall(r"@[A-Za-z]+\s*\{\s*([^,\s]+)\s*,", text))
        labels |= set(re.findall(r"\\label\s*\{([^}]+)\}", text))
        commands |= set(re.findall(r"\\(?:newcommand|renewcommand|providecommand)\s*\{?\\([A-Za-z@]+)", text))
        commands |= set(re.findall(r"\\DeclareMathOperator\*?\s*\{\\([A-Za-z@]+)\}", text))
        environments |= set(re.findall(r"\\newenvironment\s*\{([^}]+)\}", text))
    return jsonify({
        "labels": sorted(labels),
        "citations": sorted(citations),
        "commands": sorted(commands),
        "environments": sorted(environments),
        "files": sorted(files),
    })


@app.route("/api/projects/<slug>/settings", methods=["GET"])
def project_settings_get(slug):
    meta = load_meta(slug)
    tex_files = [str(rel) for fp, rel in iter_project_entries(project_path(slug)) if fp.is_file() and fp.suffix.lower() == ".tex"]
    return jsonify({
        "main_document": current_main_document(slug),
        "compiler": meta.get("compiler", "pdflatex"),
        "shell_escape": bool(meta.get("shell_escape", False)),
        "tex_files": tex_files,
        "available_compilers": {name: bool(shutil.which(name)) for name in sorted(COMPILER_CHOICES)},
    })


@app.route("/api/projects/<slug>/settings", methods=["PATCH"])
def project_settings_patch(slug):
    data = request.get_json(force=True) or {}
    meta = load_meta(slug)
    if "main_document" in data:
        candidate = str(data.get("main_document") or "").strip()
        try:
            fp = safe_join(project_path(slug), candidate)
        except ValueError:
            return jsonify({"error": "invalid main document"}), 400
        if not fp.exists() or fp.suffix.lower() != ".tex":
            return jsonify({"error": "main document must be an existing .tex file"}), 400
        meta["main_document"] = candidate
    if "compiler" in data:
        compiler = data.get("compiler")
        if compiler not in COMPILER_CHOICES:
            return jsonify({"error": "unsupported compiler"}), 400
        meta["compiler"] = compiler
    if "shell_escape" in data:
        meta["shell_escape"] = bool(data.get("shell_escape"))
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify({"ok": True, **meta})


@app.route("/api/projects/<slug>/history")
def file_history(slug):
    rel = (request.args.get("path") or "").strip().strip("/")
    if not rel:
        return jsonify({"error": "missing path"}), 400
    try:
        folder = history_folder(slug, rel)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    versions = []
    if folder.exists():
        for fp in sorted(folder.glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True):
            versions.append({
                "id": fp.name,
                "created": datetime.fromtimestamp(fp.stat().st_mtime).isoformat(),
                "size": fp.stat().st_size,
            })
    return jsonify({"versions": versions})


@app.route("/api/projects/<slug>/history/version")
def file_history_version(slug):
    rel = (request.args.get("path") or "").strip().strip("/")
    version_id = Path(request.args.get("id") or "").name
    if not rel or not version_id:
        return jsonify({"error": "missing path/id"}), 400
    try:
        fp = history_folder(slug, rel) / version_id
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    if not fp.exists() or fp.suffix != ".txt":
        return jsonify({"error": "version not found"}), 404
    return jsonify({"id": version_id, "content": fp.read_text(encoding="utf-8", errors="replace")})


@app.route("/api/projects/<slug>/history/snapshot", methods=["POST"])
def file_history_snapshot(slug):
    data = request.get_json(force=True) or {}
    rel = (data.get("path") or "").strip().strip("/")
    if not rel:
        return jsonify({"error": "missing path"}), 400
    try:
        fp = safe_join(project_path(slug), rel)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    if not fp.exists() or not fp.is_file() or not is_editable_text_file(fp):
        return jsonify({"error": "file not found/editable"}), 404
    version_id = snapshot_content(slug, rel, fp.read_text(encoding="utf-8", errors="replace"), force=True)
    return jsonify({"ok": True, "id": version_id})


@app.route("/api/projects/<slug>/history/restore", methods=["POST"])
def file_history_restore(slug):
    data = request.get_json(force=True) or {}
    rel = (data.get("path") or "").strip().strip("/")
    version_id = Path(data.get("id") or "").name
    if not rel or not version_id:
        return jsonify({"error": "missing path/id"}), 400
    try:
        target = safe_join(project_path(slug), rel)
        source = history_folder(slug, rel) / version_id
    except ValueError:
        return jsonify({"error": "invalid path"}), 400
    if not source.exists():
        return jsonify({"error": "version not found"}), 404
    if target.exists():
        snapshot_content(slug, rel, target.read_text(encoding="utf-8", errors="replace"), force=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    touch_meta(slug)
    return jsonify({"ok": True})


@app.route("/api/projects/<slug>/chapters", methods=["POST"])
def add_chapter(slug):
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "missing title"}), 400
    p = project_path(slug)
    cslug = slugify(title)
    chapter_file = f"chapters/{cslug}.tex"
    fp = p / chapter_file
    if not fp.exists():
        fp.write_text(f"\\chapter{{{title}}}\n\n")

    main_tex = p / "main.tex"
    txt = main_tex.read_text()
    include_line = f"\\input{{{chapter_file[:-4]}}}"
    if include_line not in txt:
        txt = txt.replace("\\end{document}", f"{include_line}\n\n\\end{{document}}")
        main_tex.write_text(txt)

    meta = load_meta(slug)
    meta.setdefault("chapters", []).append(
        {
            "title": title,
            "file": chapter_file,
            "status": "a_faire",
            "notes": "",
            "updated": datetime.now().isoformat(),
        }
    )
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify(meta)


@app.route("/api/projects/<slug>/chapters/<int:idx>", methods=["PATCH"])
def update_chapter(slug, idx):
    data = request.get_json(force=True) or {}
    meta = load_meta(slug)
    chapters = meta.get("chapters", [])
    if idx < 0 or idx >= len(chapters):
        return jsonify({"error": "chapter not found"}), 404
    ch = chapters[idx]
    if "status" in data and data["status"] in STATUS_CHOICES:
        ch["status"] = data["status"]
    if "notes" in data:
        ch["notes"] = data["notes"]
    if "title" in data and data["title"].strip():
        ch["title"] = data["title"].strip()
    ch["updated"] = datetime.now().isoformat()
    meta["updated"] = datetime.now().isoformat()
    save_meta(slug, meta)
    return jsonify(meta)


@app.route("/api/projects/<slug>/chapters/<int:idx>", methods=["DELETE"])
def delete_chapter(slug, idx):
    meta = load_meta(slug)
    chapters = meta.get("chapters", [])
    if 0 <= idx < len(chapters):
        chapters.pop(idx)
        save_meta(slug, meta)
    return jsonify(meta)


def _unique_paths(paths):
    result = []
    seen = set()
    for path in paths:
        try:
            path = Path(path).resolve()
        except Exception:
            continue
        key = str(path)
        if key not in seen and path.exists():
            seen.add(key)
            result.append(path)
    return result


def bibliography_assets(project_root: Path):
    supported = {".bib", ".bst", ".bbx", ".cbx"}
    root = project_root.resolve()
    build = (root / "build").resolve()
    assets = []
    for fp in root.rglob("*"):
        if not fp.is_file() or fp.suffix.lower() not in supported:
            continue
        try:
            resolved = fp.resolve()
            if resolved == build or build in resolved.parents:
                continue
            assets.append(fp)
        except Exception:
            continue
    return assets


def tex_project_env(project_root: Path, main_fp=None, build_dir=None) -> dict:
    env = os.environ.copy()

    local_bin = str(Path.home() / ".local" / "bin")
    current_path = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    env["PATH"] = os.pathsep.join(dict.fromkeys([local_bin] + current_path))
    root = project_root.resolve()

    tex_dirs = [root]
    if main_fp is not None:
        tex_dirs.insert(0, Path(main_fp).parent.resolve())
    if build_dir is not None:
        tex_dirs.append(Path(build_dir).resolve())

    bib_dirs = list(tex_dirs)
    bib_dirs.extend(fp.parent for fp in bibliography_assets(root))

    tex_dirs = _unique_paths(tex_dirs)
    bib_dirs = _unique_paths(bib_dirs)

    def extend_kpathsea(name, dirs):
        current = env.get(name, "")
        prefix = os.pathsep.join(str(p) for p in dirs)
        if prefix:
            env[name] = prefix + os.pathsep + current if current else prefix + os.pathsep

    extend_kpathsea("TEXINPUTS", tex_dirs)
    extend_kpathsea("BIBINPUTS", bib_dirs)
    extend_kpathsea("BSTINPUTS", bib_dirs)
    return env


def mirror_bibliography_assets(project_root: Path, build_dir: Path):
    build_dir.mkdir(parents=True, exist_ok=True)
    assets = bibliography_assets(project_root)

    counts = {}
    for fp in assets:
        counts[fp.name] = counts.get(fp.name, 0) + 1

    for fp in assets:
        try:
            rel = fp.relative_to(project_root)
            dest = build_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, dest)

            if counts.get(fp.name) == 1:
                flat = build_dir / fp.name
                if flat.resolve() != dest.resolve():
                    shutil.copy2(fp, flat)
        except Exception:
            continue


def bibliography_jobs(build_dir: Path):
    biber_jobs = []
    bibtex_jobs = []

    for bcf in sorted(build_dir.rglob("*.bcf")):
        if bcf.is_file():
            biber_jobs.append(bcf)

    biber_stems = {(p.parent.resolve(), p.stem) for p in biber_jobs}
    for aux in sorted(build_dir.rglob("*.aux")):
        if not aux.is_file():
            continue
        try:
            content = aux.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "\\bibdata" not in content:
            continue
        if (aux.parent.resolve(), aux.stem) in biber_stems:
            continue
        bibtex_jobs.append(aux)

    return biber_jobs, bibtex_jobs


def run_bibliography_jobs(build_dir: Path, slug: str, env: dict):
    logs = []
    ran_backend = False
    had_error = False
    stopped = False
    biber_jobs, bibtex_jobs = bibliography_jobs(build_dir)

    if biber_jobs:
        biber_exe = shutil.which("biber", path=env.get("PATH"))
        if not biber_exe:
            had_error = True
            logs.append(
                "== Bibliography error ==\n"
                "This document uses biblatex/Biber, but the 'biber' command is not available.\n"
                "Install Biber (for TeX Live/BasicTeX: sudo tlmgr install biber) and compile again."
            )
        else:
            for bcf in biber_jobs:
                ran_backend = True
                rb = run_latex([biber_exe, bcf.stem], cwd=bcf.parent, slug=slug, env=env)
                logs.append(
                    f"== Biber ({bcf.relative_to(build_dir)}) ==\n"
                    + (rb.stdout or "") + (rb.stderr or "")
                )
                if rb.returncode < 0:
                    stopped = True
                    break
                if rb.returncode != 0:
                    had_error = True

    if not stopped and bibtex_jobs:
        bibtex_exe = shutil.which("bibtex", path=env.get("PATH"))
        if not bibtex_exe:
            had_error = True
            logs.append(
                "== Bibliography error ==\n"
                "This document uses BibTeX, but the 'bibtex' command is not available.\n"
                "Install a complete TeX distribution (or BibTeX) and compile again."
            )
        else:
            for aux in bibtex_jobs:
                ran_backend = True
                rb = run_latex([bibtex_exe, aux.stem], cwd=aux.parent, slug=slug, env=env)
                logs.append(
                    f"== BibTeX ({aux.relative_to(build_dir)}) ==\n"
                    + (rb.stdout or "") + (rb.stderr or "")
                )
                if rb.returncode < 0:
                    stopped = True
                    break
                if rb.returncode != 0:
                    had_error = True

    return logs, ran_backend, had_error, stopped


def project_bibliography_hint(project_root: Path):
    bibs = bibliography_assets(project_root)
    if not any(fp.suffix.lower() == ".bib" for fp in bibs):
        return None

    patterns = (
        re.compile(r"\\bibliography\s*\{"),
        re.compile(r"\\addbibresource(?:\s*\[[^\]]*\])?\s*\{"),
    )
    for fp in project_root.rglob("*.tex"):
        if "build" in fp.parts:
            continue
        try:
            source = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if any(p.search(source) for p in patterns):
            return None

    return (
        "A .bib file exists in this project, but no \\bibliography{...} or "
        "\\addbibresource{...} command was found in the .tex sources. "
        "LaTeX does not print a bibliography just because a .bib file exists."
    )


def run_latex(cmd, cwd, slug=None, env=None):
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if slug:
        with ACTIVE_PROCESSES_LOCK:
            ACTIVE_PROCESSES[slug] = proc
    try:
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    finally:
        if slug:
            with ACTIVE_PROCESSES_LOCK:
                if ACTIVE_PROCESSES.get(slug) is proc:
                    ACTIVE_PROCESSES.pop(slug, None)


_FMT_STATE = {}
_MYLATEXFORMAT = [None]


def _mylatexformat_ok(env):
    if _MYLATEXFORMAT[0] is None:
        try:
            r = subprocess.run(["kpsewhich", "mylatexformat.ltx"], capture_output=True, text=True, timeout=20, env=env)
            _MYLATEXFORMAT[0] = bool((r.stdout or "").strip())
        except Exception:
            _MYLATEXFORMAT[0] = False
    return _MYLATEXFORMAT[0]


def _preamble_hash(project_root: Path, main_fp: Path, compiler: str):
    try:
        src = main_fp.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    i = src.find("\\begin{document}")
    if i < 0:
        return None
    pre = src[:i]
    h = hashlib.sha1()
    h.update(compiler.encode())
    h.update(datetime.now().strftime("%Y-%m-%d").encode())
    h.update(pre.encode("utf-8", "ignore"))
    for group in re.findall(r"\\(?:input|include|usepackage|documentclass|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}", pre):
        for name in group.split(","):
            name = name.strip()
            if not name:
                continue
            for base in (main_fp.parent, project_root):
                for ext in ("", ".tex", ".sty", ".cls"):
                    f = base / (name + ext)
                    try:
                        if f.is_file():
                            h.update(f"{f}:{f.stat().st_mtime_ns}".encode())
                    except Exception:
                        pass
    return h.hexdigest()[:16]


def _fmt_ready(slug, project_root, main, main_fp, build, compiler, env):
    if compiler != "pdflatex" or not _mylatexformat_ok(env):
        return None
    h = _preamble_hash(project_root, main_fp, compiler)
    if not h:
        return None
    st = _FMT_STATE.setdefault(slug, {"bad": set(), "busy": False})
    if h in st["bad"]:
        return None
    stem = Path(main).stem
    fmt = build / f"{stem}.vlfmt.fmt"
    try:
        if fmt.exists() and (build / f"{stem}.vlfmt.hash").read_text().strip() == h:
            return str(fmt)[:-4], h
    except Exception:
        pass
    return None


def _fmt_build_async(slug, project_root, main, main_fp, build, compiler, env):
    if compiler != "pdflatex" or not _mylatexformat_ok(env):
        return
    h = _preamble_hash(project_root, main_fp, compiler)
    st = _FMT_STATE.setdefault(slug, {"bad": set(), "busy": False})
    if not h or h in st["bad"] or st["busy"]:
        return
    stem = Path(main).stem
    hf = build / f"{stem}.vlfmt.hash"
    try:
        if (build / f"{stem}.vlfmt.fmt").exists() and hf.read_text().strip() == h:
            return
    except Exception:
        pass
    st["busy"] = True

    def go():
        try:
            new = build / f"{stem}.vlfmtnew.fmt"
            if new.exists():
                new.unlink()
            subprocess.run([compiler, "-ini", "-interaction=nonstopmode", f"-jobname={stem}.vlfmtnew",
                            f"-output-directory={build}", f"&{compiler}", "mylatexformat.ltx", main],
                           cwd=project_root, env=env, capture_output=True, text=True, timeout=300)
            if new.exists() and new.stat().st_size > 0:
                os.replace(new, build / f"{stem}.vlfmt.fmt")
                hf.write_text(h)
            else:
                st["bad"].add(h)
        except Exception:
            st["bad"].add(h)
        finally:
            st["busy"] = False

    threading.Thread(target=go, daemon=True).start()


@app.route("/api/projects/<slug>/compile/stop", methods=["POST"])
def stop_compile(slug):
    with ACTIVE_PROCESSES_LOCK:
        proc = ACTIVE_PROCESSES.get(slug)
    if not proc or proc.poll() is not None:
        return jsonify({"ok": True, "stopped": False})
    try:
        proc.terminate()
        return jsonify({"ok": True, "stopped": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/projects/<slug>/compile/clean", methods=["POST"])
def clean_compile(slug):
    build = project_path(slug) / "build"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(exist_ok=True)
    return jsonify({"ok": True})


@app.route("/api/projects/<slug>/compile", methods=["POST"])
def compile_project(slug):
    started = time.monotonic()
    p = project_path(slug)
    build = p / "build"
    build.mkdir(exist_ok=True)
    meta = load_meta(slug)
    main = current_main_document(slug)
    main_fp = safe_join(p, main)
    if not main_fp.exists():
        return jsonify({"ok": False, "log": f"Main document not found: {main}"}), 400

    compiler = meta.get("compiler", "pdflatex")
    if compiler not in COMPILER_CHOICES:
        compiler = "pdflatex"
    if not shutil.which(compiler):
        return jsonify({
            "ok": False,
            "log": f"{compiler} not found. Install a TeX distribution that provides it, or choose another compiler in Settings.",
        })

    shell_escape = bool(meta.get("shell_escape", False))
    shell_args = ["-shell-escape"] if shell_escape else []
    tex_env = tex_project_env(p, main_fp, build)
    mirror_bibliography_assets(p, build)
    log_parts = []

    fast = request.args.get("fast") == "1"
    pdf_target = current_pdf_path(slug)
    aux_ready = (build / f"{Path(main).stem}.aux").exists() and pdf_target.exists()
    if fast and aux_ready and compiler != "latex":
        fmt_info = _fmt_ready(slug, p, main, main_fp, build, compiler, tex_env)

        def fast_pass(fmt_arg):
            t0 = time.time()
            cmd = [compiler, *([f"-fmt={fmt_arg}"] if fmt_arg else []), "-interaction=nonstopmode",
                   "-file-line-error", "-synctex=1", *shell_args, f"-output-directory={build}", main]
            rr = run_latex(cmd, cwd=p, slug=slug, env=tex_env)
            ok_pdf = pdf_target.exists() and pdf_target.stat().st_mtime >= t0 - 1
            return rr, ok_pdf

        r, fresh = fast_pass(fmt_info[0] if fmt_info else None)
        if fmt_info and not fresh and r.returncode >= 0:
            _FMT_STATE.setdefault(slug, {"bad": set(), "busy": False})["bad"].add(fmt_info[1])
            r, fresh = fast_pass(None)
            fmt_info = None
        stopped = r.returncode < 0
        out = (r.stdout or "") + (r.stderr or "")
        if fresh and not fmt_info:
            _fmt_build_async(slug, p, main, main_fp, build, compiler, tex_env)
        rerun = bool(re.search(r"Rerun to get|There were undefined references|Label\(s\) may have changed", out))
        has_err = bool(re.search(r"^.*:\d+: |^! ", out, re.M))
        return jsonify({
            "ok": fresh and not stopped and not has_err, "forced": fresh and has_err, "fast": True,
            "preamble_cache": bool(fmt_info),
            "needs_full": rerun, "stopped": stopped, "compiler": compiler, "main_document": main,
            "duration": round(time.monotonic() - started, 3),
            "log": ("== Fast compile (1 pass, forced: errors do not stop the PDF) ==\n" + out)[-100000:],
            "missing_files": [{"file": n, "package": re.sub(r"\.(sty|cls|clo|def|cfg)$", "", n)}
                              for n in sorted(set(re.findall(r"File `([^']+)' not found", out)))],
            "tlmgr_hint": None,
        })
    stopped = False
    bibliography_error = False

    latexmk_exe = shutil.which("latexmk")
    if latexmk_exe:
        latexmk_mode = {
            "pdflatex": "-pdf",
            "xelatex": "-xelatex",
            "lualatex": "-lualatex",
            "latex": "-pdfdvi",
        }[compiler]
        cmd = [
            latexmk_exe, latexmk_mode, "-f", "-interaction=nonstopmode", "-synctex=1",
            *shell_args, f"-outdir={build}", main,
        ]

        log_parts.append(f"== Using latexmk ({compiler}) ==")
        r = run_latex(cmd, cwd=p, slug=slug, env=tex_env)
        log_parts.append((r.stdout or "") + (r.stderr or ""))
        stopped = r.returncode < 0

        if not stopped:
            bib_logs, ran_bib, bib_err, bib_stopped = run_bibliography_jobs(build, slug, tex_env)
            log_parts.extend(bib_logs)
            bibliography_error = bibliography_error or bib_err
            stopped = stopped or bib_stopped

            if ran_bib and not stopped:
                r2 = run_latex(cmd, cwd=p, slug=slug, env=tex_env)
                log_parts.append("== latexmk after bibliography ==\n" + (r2.stdout or "") + (r2.stderr or ""))
                stopped = r2.returncode < 0

    elif compiler == "latex":
        return jsonify({
            "ok": False,
            "log": "The classic latex -> DVI pipeline requires latexmk in VanoLeaf. Install latexmk or choose pdfLaTeX/XeLaTeX/LuaLaTeX.",
        }), 400
    else:
        log_parts.append("== latexmk not found: manual compiler passes ==")
        base_cmd = [
            compiler, "-interaction=nonstopmode", "-synctex=1", *shell_args,
            f"-output-directory={build}", main,
        ]
        r1 = run_latex(base_cmd, cwd=p, slug=slug, env=tex_env)
        log_parts.append(f"== Pass 1 ({compiler}) ==\n" + (r1.stdout or "") + (r1.stderr or ""))
        stopped = r1.returncode < 0

        if not stopped:
            bib_logs, _ran_bib, bib_err, bib_stopped = run_bibliography_jobs(build, slug, tex_env)
            log_parts.extend(bib_logs)
            bibliography_error = bibliography_error or bib_err
            stopped = stopped or bib_stopped

        if not stopped:
            r2 = run_latex(base_cmd, cwd=p, slug=slug, env=tex_env)
            log_parts.append(f"== Pass 2 ({compiler}) ==\n" + (r2.stdout or "") + (r2.stderr or ""))
            stopped = r2.returncode < 0
        if not stopped:
            r3 = run_latex(base_cmd, cwd=p, slug=slug, env=tex_env)
            log_parts.append(f"== Final pass ({compiler}) ==\n" + (r3.stdout or "") + (r3.stderr or ""))
            stopped = r3.returncode < 0

    biber_jobs, bibtex_jobs = bibliography_jobs(build)
    if not biber_jobs and not bibtex_jobs:
        hint = project_bibliography_hint(p)
        if hint:
            log_parts.append("== Bibliography note ==\n" + hint)

    pdf = current_pdf_path(slug)
    ok = pdf.exists() and not stopped and not bibliography_error
    if pdf.exists() and not stopped:
        _fmt_build_async(slug, p, main, main_fp, build, compiler, tex_env)
    meta = load_meta(slug)
    meta["last_compiled"] = datetime.now().isoformat()
    meta["last_compile_ok"] = ok
    save_meta(slug, meta)

    full_log = "\n\n".join(log_parts)
    missing_names = set(re.findall(r"File `([^']+)' not found", full_log))
    missing_names |= set(re.findall(r"! I can't find file `([^']+)'", full_log))
    missing_files = []
    for name in sorted(missing_names):
        pkg = re.sub(r"\.(sty|cls|clo|def|cfg)$", "", name)
        missing_files.append({"file": name, "package": pkg})
    tlmgr_hint = None
    if missing_files:
        pkgs = " ".join(sorted({m["package"] for m in missing_files}))
        tlmgr_hint = f"sudo tlmgr install {pkgs}"

    return jsonify({
        "ok": ok,
        "stopped": stopped,
        "compiler": compiler,
        "main_document": main,
        "duration": round(time.monotonic() - started, 3),
        "log": full_log[-100000:],
        "missing_files": missing_files,
        "tlmgr_hint": tlmgr_hint,
        "bibliography": {
            "bibtex_available": bool(shutil.which("bibtex")),
            "biber_available": bool(shutil.which("biber")),
            "biber_jobs": len(biber_jobs),
            "bibtex_jobs": len(bibtex_jobs),
        },
    })


@app.route("/api/projects/<slug>/synctex")
def synctex_lookup(slug):
    page = request.args.get("page")
    x = request.args.get("x")
    y = request.args.get("y")
    if not (page and x and y):
        return jsonify({"error": "missing page/x/y"}), 400
    if not shutil.which("synctex"):
        return jsonify({"error": "synctex not installed"}), 200

    p = project_path(slug)
    build = p / "build"
    pdf = current_pdf_path(slug)
    if not pdf.exists():
        return jsonify({"error": "not compiled yet"}), 404

    try:
        x0, y0 = float(x), float(y)
    except ValueError:
        return jsonify({"error": "invalid x/y"}), 400

    def try_point(px, py):
        spec = f"{page}:{px:.2f}:{py:.2f}:{pdf.name}"
        r = subprocess.run(
            ["synctex", "edit", "-o", spec],
            cwd=build,
            capture_output=True,
            text=True,
        )
        out = r.stdout
        file_match = re.search(r"^Input:\d+:(.+)$", out, re.M)
        line_match = re.search(r"^Line:(\d+)$", out, re.M)
        if not file_match or not line_match:
            return None, out
        return (file_match.group(1).strip(), int(line_match.group(1))), out

    offsets = [(0, 0)]
    for r_ in (3, 8):
        offsets += [(r_, 0), (-r_, 0), (0, r_), (0, -r_)]

    result, raw = None, ""
    for dx, dy in offsets:
        result, raw = try_point(x0 + dx, y0 + dy)
        if result:
            break

    if not result:
        return jsonify({"error": "no match", "raw": raw[-2000:]}), 200

    abs_file, line = result
    try:
        rel_file = str(Path(abs_file).resolve().relative_to(p.resolve()))
    except ValueError:
        rel_file = abs_file
    return jsonify({"file": rel_file, "line": line})


@app.route("/api/projects/<slug>/synctex-forward")
def synctex_forward(slug):
    rel_file = request.args.get("file", current_main_document(slug))
    line = request.args.get("line")
    if not line:
        return jsonify({"error": "missing line"}), 400
    if not shutil.which("synctex"):
        return jsonify({"error": "synctex not installed"}), 200

    p = project_path(slug)
    build = p / "build"
    pdf = current_pdf_path(slug)
    if not pdf.exists():
        return jsonify({"error": "not compiled yet"}), 404

    try:
        src = safe_join(p, rel_file)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400

    try:
        line = int(line)
    except ValueError:
        return jsonify({"error": "invalid line"}), 400

    candidates = [rel_file, Path(rel_file).name, str(src)]

    def try_line(target_line):
        for candidate in candidates:
            spec = f"{target_line}:1:{candidate}"
            r = subprocess.run(
                ["synctex", "view", "-i", spec, "-o", pdf.name],
                cwd=build,
                capture_output=True,
                text=True,
            )
            out = r.stdout
            page_match = re.search(r"^Page:(\d+)", out, re.M)
            if not page_match:
                continue
            x_match = re.search(r"^x:([\-0-9.]+)", out, re.M)
            y_match = re.search(r"^y:([\-0-9.]+)", out, re.M)
            h_match = re.search(r"^h:([\-0-9.]+)", out, re.M)
            v_match = re.search(r"^v:([\-0-9.]+)", out, re.M)
            return {
                "page": int(page_match.group(1)),
                "x": float(x_match.group(1)) if x_match else (float(h_match.group(1)) if h_match else None),
                "y": float(y_match.group(1)) if y_match else (float(v_match.group(1)) if v_match else None),
            }
        return None

    MAX_RADIUS = 25
    offsets = [0]
    for d in range(1, MAX_RADIUS + 1):
        offsets += [d, -d]

    result = None
    matched_line = None
    for offset in offsets:
        candidate_line = line + offset
        if candidate_line < 1:
            continue
        result = try_line(candidate_line)
        if result:
            matched_line = candidate_line
            break

    if not result:
        return jsonify({"error": "no match", "searched_radius": MAX_RADIUS}), 200

    result["matched_line"] = matched_line
    result["requested_line"] = line
    return jsonify(result)


@app.route("/api/projects/<slug>/pdf")
def get_pdf(slug):
    pdf = current_pdf_path(slug)
    if not pdf.exists():
        return jsonify({"error": "not compiled yet"}), 404
    return send_file(pdf, mimetype="application/pdf")


def _open_browser(url):
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    PORT = 5500
    URL = f"http://127.0.0.1:{PORT}"
    if os.environ.get("VANOLEAF_SILENT") != "1":
        threading.Timer(1.2, _open_browser, args=(URL,)).start()
    print(f"VanoLeaf is running at {URL} (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=PORT, debug=False)
