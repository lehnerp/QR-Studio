"""
QR Studio - a local Flask dashboard for creating styled QR codes.

Run:  python app.py      then open http://127.0.0.1:5000
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta

from flask import Flask, Response, abort, g, jsonify, render_template, request, send_file

from qr_engine import (CONTENT_TYPES, Design, QRError, build_payload, describe,
                       render_png, render_svg)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("QR_DB", os.path.join(BASE_DIR, "instance", "qr_history.db"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # logos are sent as data URLs

TYPE_LABELS = {
    "url": "Website", "text": "Text", "vcard": "Contact", "wifi": "Wi-Fi", "email": "Email",
    "sms": "SMS", "phone": "Phone", "event": "Event", "geo": "Location",
}


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    fields      TEXT NOT NULL,
    design      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    favorite    INTEGER NOT NULL DEFAULT 0,
    downloads   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_codes_created ON codes(created_at);
"""


def db() -> sqlite3.Connection:
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.executescript(SCHEMA)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def row_to_dict(row: sqlite3.Row, with_design: bool = True) -> dict:
    d = dict(row)
    d["fields"] = json.loads(d["fields"])
    design = json.loads(d["design"])
    d["has_logo"] = bool(design.get("logo"))
    d["design"] = design if with_design else {k: v for k, v in design.items() if k != "logo"}
    d["type_label"] = TYPE_LABELS.get(d["kind"], d["kind"])
    d["favorite"] = bool(d["favorite"])
    return d


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def parse_request() -> tuple[str, dict, Design, str]:
    body = request.get_json(silent=True) or {}
    kind = body.get("kind", "url")
    if kind not in CONTENT_TYPES:
        raise QRError("Unknown content type.")
    fields = body.get("fields") or {}
    if not isinstance(fields, dict):
        raise QRError("Bad fields.")
    design = Design.from_dict(body.get("design") or {})
    payload = build_payload(kind, fields)
    if not payload:
        raise QRError("Fill in the required fields to generate a code.")
    return kind, fields, design, payload


def default_name(kind: str, fields: dict, payload: str) -> str:
    pick = {
        "url": fields.get("url"), "text": fields.get("text"), "vcard": fields.get("name"),
        "wifi": fields.get("ssid"), "email": fields.get("to"), "sms": fields.get("phone"),
        "phone": fields.get("phone"), "event": fields.get("title"),
        "geo": f"{fields.get('lat')}, {fields.get('lng')}",
    }.get(kind) or payload
    pick = re.sub(r"\s+", " ", str(pick)).strip()
    return (pick[:57] + "…") if len(pick) > 60 else pick


def save_code(kind, fields, design: Design, payload, name=None, count_download=False) -> int:
    """Insert or update (deduplicated by content+design). Returns id."""
    design_json = json.dumps(design.to_dict(), sort_keys=True)
    fp = hashlib.sha256(f"{kind}|{payload}|{design_json}".encode()).hexdigest()
    conn = db()
    existing = conn.execute("SELECT id FROM codes WHERE fingerprint=?", (fp,)).fetchone()
    stamp = now()
    if existing:
        conn.execute(
            "UPDATE codes SET updated_at=?, downloads=downloads+?, name=COALESCE(?, name) WHERE id=?",
            (stamp, 1 if count_download else 0, name or None, existing["id"]),
        )
        conn.commit()
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO codes (name, kind, fields, design, payload, fingerprint, downloads, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (name or default_name(kind, fields, payload), kind, json.dumps(fields), design_json,
         payload, fp, 1 if count_download else 0, stamp, stamp),
    )
    conn.commit()
    return cur.lastrowid


def slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (s or "qr-code")[:50]


def file_response(payload: str, design: Design, fmt: str, filename: str):
    if fmt == "svg":
        return Response(
            render_svg(payload, design), mimetype="image/svg+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}.svg"'},
        )
    return send_file(io.BytesIO(render_png(payload, design)), mimetype="image/png",
                     as_attachment=True, download_name=f"{filename}.png")


@app.errorhandler(QRError)
def handle_qr_error(err):
    return jsonify(error=str(err)), 400


@app.errorhandler(413)
def too_large(_err):
    return jsonify(error="Upload is too large (8 MB max)."), 413


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@app.context_processor
def inject_globals():
    return {"type_labels": TYPE_LABELS, "nav": request.endpoint}


@app.route("/")
def generator():
    return render_template("generator.html")


@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/bulk")
def bulk():
    return render_template("bulk.html")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@app.post("/api/preview")
def api_preview():
    _, _, design, payload = parse_request()
    png = render_png(payload, design)
    info = describe(payload, design)
    return jsonify(image="data:image/png;base64," + base64.b64encode(png).decode(),
                   payload=payload, info=info)


@app.post("/api/download/<fmt>")
def api_download(fmt):
    if fmt not in ("png", "svg"):
        abort(404)
    body = request.get_json(silent=True) or {}
    kind, fields, design, payload = parse_request()
    name = (body.get("name") or "").strip() or None
    if body.get("save", True):
        save_code(kind, fields, design, payload, name=name, count_download=True)
    return file_response(payload, design, fmt, slug(name or default_name(kind, fields, payload)))


@app.post("/api/codes")
def api_save():
    body = request.get_json(silent=True) or {}
    kind, fields, design, payload = parse_request()
    code_id = save_code(kind, fields, design, payload, name=(body.get("name") or "").strip() or None)
    return jsonify(id=code_id)


@app.get("/api/codes")
def api_list():
    q = (request.args.get("q") or "").strip()
    kind = request.args.get("kind") or ""
    fav = request.args.get("favorites") == "1"
    sql, args = "SELECT * FROM codes WHERE 1=1", []
    if q:
        sql += " AND (name LIKE ? OR payload LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if kind in CONTENT_TYPES:
        sql += " AND kind=?"
        args.append(kind)
    if fav:
        sql += " AND favorite=1"
    sql += " ORDER BY favorite DESC, updated_at DESC LIMIT 500"
    rows = db().execute(sql, args).fetchall()
    return jsonify(codes=[row_to_dict(r, with_design=False) for r in rows])


@app.get("/api/stats")
def api_stats():
    conn = db()
    total = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(downloads),0) d FROM codes").fetchone()
    week_ago = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    week = conn.execute("SELECT COUNT(*) c FROM codes WHERE created_at>=?", (week_ago,)).fetchone()["c"]
    by_type = conn.execute("SELECT kind, COUNT(*) c FROM codes GROUP BY kind ORDER BY c DESC").fetchall()
    return jsonify(total=total["c"], downloads=total["d"], this_week=week,
                   by_type=[{"kind": r["kind"], "label": TYPE_LABELS.get(r["kind"], r["kind"]), "count": r["c"]} for r in by_type])


def get_row(code_id: int) -> sqlite3.Row:
    row = db().execute("SELECT * FROM codes WHERE id=?", (code_id,)).fetchone()
    if not row:
        abort(404)
    return row


@app.get("/api/codes/<int:code_id>")
def api_get(code_id):
    return jsonify(row_to_dict(get_row(code_id)))


@app.patch("/api/codes/<int:code_id>")
def api_update(code_id):
    get_row(code_id)
    body = request.get_json(silent=True) or {}
    conn = db()
    if "name" in body:
        name = str(body["name"]).strip()[:120]
        if name:
            conn.execute("UPDATE codes SET name=? WHERE id=?", (name, code_id))
    if "favorite" in body:
        conn.execute("UPDATE codes SET favorite=? WHERE id=?", (1 if body["favorite"] else 0, code_id))
    conn.commit()
    return jsonify(row_to_dict(get_row(code_id), with_design=False))


@app.delete("/api/codes/<int:code_id>")
def api_delete(code_id):
    get_row(code_id)
    db().execute("DELETE FROM codes WHERE id=?", (code_id,))
    db().commit()
    return jsonify(ok=True)


@app.get("/codes/<int:code_id>/thumb.png")
def code_thumb(code_id):
    row = get_row(code_id)
    design = Design.from_dict({**json.loads(row["design"]), "size": 200})
    resp = send_file(io.BytesIO(render_png(row["payload"], design)), mimetype="image/png")
    resp.headers["Cache-Control"] = "max-age=60"
    return resp


@app.get("/codes/<int:code_id>.<fmt>")
def code_file(code_id, fmt):
    if fmt not in ("png", "svg"):
        abort(404)
    row = get_row(code_id)
    design = Design.from_dict(json.loads(row["design"]))
    db().execute("UPDATE codes SET downloads=downloads+1 WHERE id=?", (code_id,))
    db().commit()
    return file_response(row["payload"], design, fmt, slug(row["name"]))


@app.post("/api/bulk")
def api_bulk():
    """Body: {kind: 'url'|'text', rows: 'name,content\\n...', design: {...}, format: 'png'|'svg', save: bool}"""
    body = request.get_json(silent=True) or {}
    kind = body.get("kind", "url")
    if kind not in ("url", "text"):
        raise QRError("Bulk mode supports URL and Text codes.")
    fmt = "svg" if body.get("format") == "svg" else "png"
    design = Design.from_dict(body.get("design") or {})
    raw = str(body.get("rows") or "")
    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^[a-z][a-z0-9+.-]*://", line, re.I):
            name, content = "", line  # a bare URL may itself contain commas
        else:
            parts = [p.strip() for p in next(csv.reader([line]))]
            if len(parts) >= 2:
                name, content = parts[0], ",".join(parts[1:]).strip()
            else:
                name, content = "", parts[0] if parts else ""
        if content:
            entries.append((name, content))
    if not entries:
        raise QRError("Add at least one line to generate.")
    if len(entries) > 500:
        raise QRError("Bulk mode is limited to 500 codes at a time.")

    buf = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = ["file,name,content"]
        for idx, (name, content) in enumerate(entries, 1):
            fields = {"url": content} if kind == "url" else {"text": content}
            payload = build_payload(kind, fields)
            label = name or default_name(kind, fields, payload)
            base = f"{idx:03d}-{slug(label)}"
            while base in used:
                base += "-x"
            used.add(base)
            data = render_svg(payload, design).encode() if fmt == "svg" else render_png(payload, design)
            zf.writestr(f"{base}.{fmt}", data)
            manifest.append(f'{base}.{fmt},"{label.replace(chr(34), "")}","{payload.replace(chr(34), "")}"')
            if body.get("save"):
                save_code(kind, fields, design, payload, name=name or None)
        zf.writestr("manifest.csv", "\n".join(manifest))
    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"qr-codes-{stamp}.zip")


if __name__ == "__main__":
    host = os.environ.get("QR_HOST", "127.0.0.1")
    port = int(os.environ.get("QR_PORT", "5000"))
    print(f"\n  QR Studio running at http://{host}:{port}\n")
    app.run(host=host, port=port, debug=os.environ.get("QR_DEBUG") == "1")
