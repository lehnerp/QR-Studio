# QR Studio — local Flask QR code dashboard

A local web dashboard for making styled QR codes, modelled on the site's
`pages/make-qr.html` generator. QR codes are rendered in Python on your own
machine, and nothing is sent to any other server.

## Start it

**Windows:** double-click `run.bat`. The first run sets up a `.venv` folder and
installs the dependencies. After that it opens <http://127.0.0.1:5000>.

**Any OS:**

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>.

Optional environment variables: `QR_PORT` (default 5000), `QR_HOST` (default
127.0.0.1; set to `0.0.0.0` to open it to your LAN), `QR_DB` (path to the
SQLite file), and `QR_DEBUG=1`.

## What it does

| Page | Features |
|---|---|
| **Create** | Website, Text, Wi-Fi, Contact (vCard), Email, SMS, Phone, Event (iCal) and Location (geo) codes. The preview updates as you type. |
| | Style presets. Dot colour, background colour, diagonal or radial gradient, separate corner (eye) colour, transparent background. |
| | 5 dot shapes (square, rounded, dots, gapped, leaf) and 3 corner shapes (square, rounded, circle). |
| | Frames (banner, square, rounded, corners, dashed, double) with a caption. |
| | Logo upload: error correction switches to High automatically, and the modules under the logo are cleared. |
| | Export size from 256 to 2048 px, quiet zone, error-correction level. |
| | Download PNG or **SVG** (vector, for print), copy the image to the clipboard, or save it to the library. |
| **Library** | Every code you download or save. Search, filter by type, favourite, rename inline, re-download PNG/SVG, open in the editor, delete. Shows stats (total, this week, downloads, by type). Wi-Fi passwords are masked in the list. |
| **Bulk** | Paste URLs or text, one per line (optionally `name, content`), or import a CSV. You get a ZIP of PNGs or SVGs in one style, plus a `manifest.csv`. |

Your unsaved work on the Create page is kept as a draft in the browser.

## Files

```
app.py            Flask routes, API and SQLite library (instance/qr_history.db)
qr_engine.py      Payload builders + styled renderer (PNG via Pillow, SVG by hand)
templates/        base, generator, history, bulk, shared design panel
static/           app.css, app.js (helpers), design.js, generator.js, history.js, bulk.js
run.bat           Windows one-click launcher
```

## API (for scripts)

```
POST /api/preview          {kind, fields, design}  -> {image (data URL), payload, info}
POST /api/download/png|svg {kind, fields, design, name?, save?}  -> file
POST /api/codes            save to library
GET  /api/codes?q=&kind=&favorites=1
GET|PATCH|DELETE /api/codes/<id>
GET  /codes/<id>.png | .svg
POST /api/bulk             {kind: url|text, rows, design, format, save}  -> zip
GET  /api/stats
```

Example:

```bash
curl -X POST http://127.0.0.1:5000/api/download/png -H "Content-Type: application/json" ^
  -d "{\"kind\":\"wifi\",\"fields\":{\"ssid\":\"Cafe\",\"password\":\"coffee123\"},\"design\":{\"shape\":\"rounded\",\"frame_style\":\"banner\",\"caption\":\"FREE WI-FI\"}}" ^
  -o wifi.png
```

## Scan reliability

Every combination of dot shape, corner shape, logo and frame was test-decoded
with jsQR and OpenCV. Dark dots on a light background with Medium or higher
error correction scan best. Always test a code with your phone before printing.
