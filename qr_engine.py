"""
QR engine: builds payloads for each content type and renders styled QR codes
to PNG (Pillow) or SVG from one shared layout, so both formats look the same.
"""
from __future__ import annotations

import base64
import io
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

import qrcode
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q
from PIL import Image, ImageDraw, ImageFont

ERROR_LEVELS = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
MODULE_SHAPES = ("square", "rounded", "dots", "gapped", "leaf")
EYE_SHAPES = ("square", "rounded", "circle")
FRAME_STYLES = ("none", "square", "rounded", "corners", "dashed", "double", "banner")
CONTENT_TYPES = ("text", "url", "vcard", "email", "sms", "wifi", "phone", "event", "geo")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_PAYLOAD_BYTES = 2900  # roughly the version-40 byte limit at level L


class QRError(ValueError):
    """User-facing validation error."""


# --------------------------------------------------------------------------
# Payload builders
# --------------------------------------------------------------------------
def _esc(value: str) -> str:
    """Escape characters that are special in WIFI:/vCard payloads."""
    return re.sub(r'([\\;,":])', r"\\\1", str(value))


def _f(data: dict, key: str) -> str:
    return str(data.get(key, "") or "").strip()


def build_payload(kind: str, data: dict) -> str:
    """Return the string to encode for a content type, or '' if incomplete."""
    if kind == "text":
        return str(data.get("text", "") or "").strip()

    if kind == "url":
        url = _f(data, "url")
        if url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
            url = "https://" + url
        return url

    if kind == "phone":
        phone = re.sub(r"[^\d+*#]", "", _f(data, "phone"))
        return f"tel:{phone}" if phone else ""

    if kind == "vcard":
        name, org, title = _f(data, "name"), _f(data, "org"), _f(data, "title")
        phone, email, url, address = _f(data, "phone"), _f(data, "email"), _f(data, "website"), _f(data, "address")
        if not (name or phone or email):
            return ""
        lines = ["BEGIN:VCARD", "VERSION:3.0"]
        if name:
            parts = name.split()
            last = parts[-1] if len(parts) > 1 else name
            first = " ".join(parts[:-1]) if len(parts) > 1 else ""
            lines += [f"N:{_esc(last)};{_esc(first)};;;", f"FN:{_esc(name)}"]
        if org:
            lines.append(f"ORG:{_esc(org)}")
        if title:
            lines.append(f"TITLE:{_esc(title)}")
        if phone:
            lines.append(f"TEL;TYPE=CELL:{_esc(phone)}")
        if email:
            lines.append(f"EMAIL:{_esc(email)}")
        if url:
            lines.append(f"URL:{url}")
        if address:
            lines.append(f"ADR:;;{_esc(address)};;;;")
        lines.append("END:VCARD")
        return "\n".join(lines)

    if kind == "email":
        to = _f(data, "to")
        if not to:
            return ""
        params = []
        if _f(data, "subject"):
            params.append("subject=" + quote(_f(data, "subject")))
        if _f(data, "body"):
            params.append("body=" + quote(_f(data, "body")))
        return f"mailto:{to}" + ("?" + "&".join(params) if params else "")

    if kind == "sms":
        phone = _f(data, "phone")
        if not phone:
            return ""
        msg = _f(data, "message")
        return f"SMSTO:{phone}:{msg}" if msg else f"SMSTO:{phone}"

    if kind == "wifi":
        ssid = _f(data, "ssid")
        if not ssid:
            return ""
        enc = data.get("encryption") or "WPA"
        if enc not in ("WPA", "WEP", "nopass"):
            enc = "WPA"
        out = f"WIFI:T:{enc};S:{_esc(ssid)};"
        if enc != "nopass":
            out += f"P:{_esc(str(data.get('password', '') or ''))};"
        if data.get("hidden") in (True, "true", "on", "1", 1):
            out += "H:true;"
        return out + ";"

    if kind == "event":
        title = _f(data, "title")
        start = _f(data, "start")
        if not (title and start):
            return ""

        def ical(dt: str) -> str:
            try:
                return datetime.fromisoformat(dt).strftime("%Y%m%dT%H%M%S")
            except ValueError:
                raise QRError("Event dates must be valid date/times.")

        lines = ["BEGIN:VEVENT", f"SUMMARY:{_esc(title)}", f"DTSTART:{ical(start)}"]
        if _f(data, "end"):
            lines.append(f"DTEND:{ical(_f(data, 'end'))}")
        if _f(data, "location"):
            lines.append(f"LOCATION:{_esc(_f(data, 'location'))}")
        if _f(data, "description"):
            lines.append(f"DESCRIPTION:{_esc(_f(data, 'description'))}")
        lines.append("END:VEVENT")
        return "\n".join(lines)

    if kind == "geo":
        lat, lng = _f(data, "lat"), _f(data, "lng")
        if not (lat and lng):
            return ""
        try:
            la, ln = float(lat), float(lng)
        except ValueError:
            raise QRError("Latitude and longitude must be numbers.")
        if not (-90 <= la <= 90 and -180 <= ln <= 180):
            raise QRError("Latitude must be -90..90 and longitude -180..180.")
        return f"geo:{la},{ln}"

    raise QRError(f"Unknown content type: {kind}")


# --------------------------------------------------------------------------
# Design options
# --------------------------------------------------------------------------
@dataclass
class Design:
    size: int = 512
    margin: int = 4
    foreground: str = "#111827"
    foreground2: str = ""          # second gradient colour ('' = solid)
    gradient: str = "none"         # none | linear | radial
    background: str = "#ffffff"
    transparent: bool = False
    eye_color: str = ""            # '' = same as foreground
    shape: str = "square"
    eye_shape: str = "square"
    error_level: str = "M"
    frame_style: str = "none"
    frame_color: str = "#111827"
    caption: str = ""
    caption_color: str = ""
    logo: str = ""                 # data: URL
    logo_scale: float = 0.22
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Design":
        d = d or {}

        def color(key, default, allow_empty=False):
            v = str(d.get(key, default) or "").strip()
            if allow_empty and not v:
                return ""
            if not v.startswith("#"):
                v = "#" + v
            if not HEX_RE.match(v):
                raise QRError(f"'{key}' must be a hex colour like #1a2b3c.")
            return v.lower()

        def choice(key, options, default):
            v = d.get(key, default)
            return v if v in options else default

        try:
            size = int(d.get("size", 512))
            margin = int(d.get("margin", 4))
            logo_scale = float(d.get("logo_scale", 0.22))
        except (TypeError, ValueError):
            raise QRError("Size, margin and logo scale must be numbers.")

        logo = str(d.get("logo", "") or "")
        if logo and not logo.startswith("data:image/"):
            raise QRError("Logo must be an uploaded image.")
        if len(logo) > MAX_LOGO_BYTES * 4 // 3 + 100:
            raise QRError("Logo must be smaller than 2 MB.")

        return cls(
            size=max(128, min(size, 2048)),
            margin=max(0, min(margin, 10)),
            foreground=color("foreground", "#111827"),
            foreground2=color("foreground2", "", allow_empty=True),
            gradient=choice("gradient", ("none", "linear", "radial"), "none"),
            background=color("background", "#ffffff"),
            transparent=d.get("transparent") in (True, "true", "on", "1", 1),
            eye_color=color("eye_color", "", allow_empty=True),
            shape=choice("shape", MODULE_SHAPES, "square"),
            eye_shape=choice("eye_shape", EYE_SHAPES, "square"),
            error_level=choice("error_level", tuple(ERROR_LEVELS), "M"),
            frame_style=choice("frame_style", FRAME_STYLES, "none"),
            frame_color=color("frame_color", "#111827"),
            caption=str(d.get("caption", "") or "").strip()[:60],
            caption_color=color("caption_color", "", allow_empty=True),
            logo=logo,
            logo_scale=max(0.1, min(logo_scale, 0.3)),
        )

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "extra"}


# --------------------------------------------------------------------------
# Matrix + layout
# --------------------------------------------------------------------------
def make_matrix(payload: str, design: Design) -> tuple[list[list[bool]], int]:
    if not payload:
        raise QRError("Nothing to encode yet.")
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise QRError("Content is too long for a QR code (about 2,900 bytes max).")
    level = "H" if design.logo else design.error_level
    qr = qrcode.QRCode(error_correction=ERROR_LEVELS[level], border=0)
    qr.add_data(payload)
    try:
        qr.make(fit=True)
    except Exception:
        raise QRError("Content is too long for this error-correction level.")
    return qr.get_matrix(), qr.version


@dataclass
class Layout:
    width: float
    height: float
    qr_x: float
    qr_y: float
    qr_size: float
    module: float
    n: int
    caption_box: tuple | None   # (x, y, w, h)
    frame: tuple | None         # (x, y, w, h, stroke)
    font_size: float


def compute_layout(n: int, d: Design) -> Layout:
    total_modules = n + 2 * d.margin
    module = d.size / total_modules
    qr_size = d.size
    has_frame = d.frame_style != "none"
    stroke = max(4.0, d.size / 64) if has_frame else 0
    pad = stroke * 2.5 if has_frame else 0
    font_size = max(16.0, d.size / 13)
    cap_h = font_size * 2.0 if d.caption else 0
    if d.frame_style == "banner" and not d.caption:
        cap_h = 0

    width = qr_size + pad * 2
    if d.frame_style == "banner":
        height = qr_size + pad * 2 + (cap_h if cap_h else 0)
        frame = (stroke / 2, stroke / 2, width - stroke, height - stroke, stroke)
        cap_box = (0, height - cap_h - stroke / 2, width, cap_h) if cap_h else None
    else:
        height = qr_size + pad * 2 + cap_h
        frame = (stroke / 2, stroke / 2, width - stroke, qr_size + pad * 2 - stroke, stroke) if has_frame else None
        cap_box = (0, qr_size + pad * 2, width, cap_h) if cap_h else None
    return Layout(width, height, pad, pad, qr_size, module, n, cap_box, frame, font_size)


def _is_eye(r: int, c: int, n: int) -> bool:
    return (r < 7 and c < 7) or (r < 7 and c >= n - 7) or (r >= n - 7 and c < 7)


def _eye_origins(n: int):
    return [(0, 0), (0, n - 7), (n - 7, 0)]


# --------------------------------------------------------------------------
# Geometry -> shapes (shared by PNG and SVG)
# --------------------------------------------------------------------------
# A shape is a tuple: ("rect", x, y, w, h, (tl, tr, br, bl) radii) | ("circle", cx, cy, r)
#                     | ("poly", [(x, y), ...]); each carries a 'value' (True=ink, False=erase)

def module_shapes(matrix, lay: Layout, d: Design):
    n, m = lay.n, lay.module
    ox = lay.qr_x + d.margin * m
    oy = lay.qr_y + d.margin * m

    def on(r, c):
        return 0 <= r < n and 0 <= c < n and matrix[r][c] and not _is_eye(r, c, n)

    body = []
    for r in range(n):
        for c in range(n):
            if not on(r, c):
                continue
            x, y = ox + c * m, oy + r * m
            if d.shape == "square":
                body.append(("rect", x, y, m + 0.01, m + 0.01, (0, 0, 0, 0)))
            elif d.shape == "gapped":
                g = m * 0.07
                body.append(("rect", x + g, y + g, m - 2 * g, m - 2 * g, (m * 0.12,) * 4))
            elif d.shape == "dots":
                body.append(("circle", x + m / 2, y + m / 2, m * 0.5))
            elif d.shape == "leaf":
                rad = m * 0.5
                body.append(("rect", x, y, m + 0.01, m + 0.01, (rad, 0, rad, 0)))
            else:  # rounded: round only the corners with no neighbours
                t, b, lft, rgt = on(r - 1, c), on(r + 1, c), on(r, c - 1), on(r, c + 1)
                rad = m / 2
                radii = (
                    0 if (t or lft) else rad,
                    0 if (t or rgt) else rad,
                    0 if (b or rgt) else rad,
                    0 if (b or lft) else rad,
                )
                body.append(("rect", x, y, m + 0.01, m + 0.01, radii))

    eyes = []
    for (er, ec) in _eye_origins(n):
        x, y = ox + ec * m, oy + er * m
        s = d.eye_shape
        if s == "circle":
            eyes.append((("circle", x + 3.5 * m, y + 3.5 * m, 3.5 * m), True))
            eyes.append((("circle", x + 3.5 * m, y + 3.5 * m, 2.5 * m), False))
            eyes.append((("circle", x + 3.5 * m, y + 3.5 * m, 1.5 * m), True))
        else:
            ro, ri, rc = (2.2 * m, 1.4 * m, 0.9 * m) if s == "rounded" else (0, 0, 0)
            eyes.append((("rect", x, y, 7 * m, 7 * m, (ro,) * 4), True))
            eyes.append((("rect", x + m, y + m, 5 * m, 5 * m, (ri,) * 4), False))
            eyes.append((("rect", x + 2 * m, y + 2 * m, 3 * m, 3 * m, (rc,) * 4), True))
    return body, eyes


def logo_box(lay: Layout, d: Design):
    if not d.logo:
        return None
    s = lay.qr_size * d.logo_scale
    cx = lay.qr_x + lay.qr_size / 2
    cy = lay.qr_y + lay.qr_size / 2
    pad = lay.module * 0.8
    return (cx - s / 2, cy - s / 2, s, s, pad)


def _clear_logo_area(body, box, lay: Layout):
    """Remove modules hidden under the logo so it sits on clean background."""
    if not box:
        return body
    x, y, s, _, pad = box
    x0, y0, x1, y1 = x - pad, y - pad, x + s + pad, y + s + pad

    def center(sh):
        if sh[0] == "circle":
            return sh[1], sh[2]
        if sh[0] == "poly":
            xs = [p[0] for p in sh[1]]
            ys = [p[1] for p in sh[1]]
            return sum(xs) / len(xs), sum(ys) / len(ys)
        return sh[1] + sh[3] / 2, sh[2] + sh[4] / 2

    return [sh for sh in body if not (x0 <= center(sh)[0] <= x1 and y0 <= center(sh)[1] <= y1)]


def _decode_logo(data_url: str) -> Image.Image:
    try:
        header, b64 = data_url.split(",", 1)
        raw = base64.b64decode(b64)
        if "svg" in header:
            raise QRError("SVG logos are not supported for PNG export - please use PNG or JPG.")
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except QRError:
        raise
    except Exception:
        raise QRError("Could not read the logo image.")


# --------------------------------------------------------------------------
# PNG rendering
# --------------------------------------------------------------------------
_FONT_CANDIDATES = [
    "arialbd.ttf", "Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int):
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _draw_shape(draw: ImageDraw.ImageDraw, sh, fill, k: float):
    if sh[0] == "rect":
        _, x, y, w, h, radii = sh
        box = [x * k, y * k, (x + w) * k, (y + h) * k]
        x0, y0, x1, y1 = box
        r = min(max(radii) * k, (x1 - x0) / 2, (y1 - y0) / 2)
        if r < 1:
            draw.rectangle(box, fill=fill)
            return
        # Hand-built rounded rect (per-corner) - Pillow's own is unreliable with float boxes.
        draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
        draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
        corners = [
            (radii[0], [x0, y0, x0 + 2 * r, y0 + 2 * r], 180, [x0, y0, x0 + r, y0 + r]),
            (radii[1], [x1 - 2 * r, y0, x1, y0 + 2 * r], 270, [x1 - r, y0, x1, y0 + r]),
            (radii[2], [x1 - 2 * r, y1 - 2 * r, x1, y1], 0, [x1 - r, y1 - r, x1, y1]),
            (radii[3], [x0, y1 - 2 * r, x0 + 2 * r, y1], 90, [x0, y1 - r, x0 + r, y1]),
        ]
        for rad, ebox, start, sq in corners:
            if rad > 0:
                draw.pieslice(ebox, start, start + 90, fill=fill)
            else:
                draw.rectangle(sq, fill=fill)
    elif sh[0] == "circle":
        _, cx, cy, r = sh
        draw.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=fill)
    elif sh[0] == "poly":
        draw.polygon([(px * k, py * k) for px, py in sh[1]], fill=fill)


def _paint(size, d: Design, c1: str, c2: str, region):
    """Solid or gradient RGBA fill covering the QR region."""
    w, h = size
    if d.gradient == "none" or not c2:
        return Image.new("RGBA", size, _rgb(c1) + (255,))
    x0, y0, x1, y1 = region
    a, b = _rgb(c1), _rgb(c2)
    region_size = (int(x1 - x0), int(y1 - y0))
    if d.gradient == "linear":
        # diagonal top-left -> bottom-right: average of vertical and horizontal ramps
        vertical = Image.linear_gradient("L")
        mask = Image.blend(vertical, vertical.rotate(90), 0.5).resize(region_size)
    else:
        mask = Image.radial_gradient("L").resize(region_size)
    full = Image.new("L", size, 0)
    full.paste(mask, (int(x0), int(y0)))
    img_a = Image.new("RGBA", size, a + (255,))
    img_b = Image.new("RGBA", size, b + (255,))
    return Image.composite(img_b, img_a, full)


def render_png(payload: str, design: Design) -> bytes:
    matrix, _ = make_matrix(payload, design)
    lay = compute_layout(len(matrix), design)
    body, eyes = module_shapes(matrix, lay, design)
    lbox = logo_box(lay, design)
    body = _clear_logo_area(body, lbox, lay)

    ss = 3  # supersampling for smooth edges
    W, H = math.ceil(lay.width), math.ceil(lay.height)
    big = (W * ss, H * ss)

    bg_alpha = 0 if design.transparent else 255
    canvas = Image.new("RGBA", big, _rgb(design.background) + (bg_alpha,))

    region = (lay.qr_x * ss, lay.qr_y * ss, (lay.qr_x + lay.qr_size) * ss, (lay.qr_y + lay.qr_size) * ss)

    # body modules
    mask = Image.new("L", big, 0)
    md = ImageDraw.Draw(mask)
    for sh in body:
        _draw_shape(md, sh, 255, ss)
    canvas.alpha_composite(_with_mask(_paint(big, design, design.foreground, design.foreground2, region), mask))

    # finder eyes (optional separate colour)
    emask = Image.new("L", big, 0)
    ed = ImageDraw.Draw(emask)
    for sh, ink in eyes:
        _draw_shape(ed, sh, 255 if ink else 0, ss)
    eye_c = design.eye_color or design.foreground
    eye_c2 = "" if design.eye_color else design.foreground2
    canvas.alpha_composite(_with_mask(_paint(big, design, eye_c, eye_c2, region), emask))

    draw = ImageDraw.Draw(canvas)

    # frame
    if lay.frame:
        _draw_frame_png(draw, lay, design, ss)

    # caption
    if lay.caption_box and design.caption:
        x, y, w, h = lay.caption_box
        if design.frame_style == "banner":
            draw.rectangle([x * ss, y * ss, (x + w) * ss, (y + h) * ss + lay.frame[4] * ss], fill=_rgb(design.frame_color))
            col = design.caption_color or design.background
        else:
            col = design.caption_color or design.frame_color
        font = _font(int(lay.font_size * ss))
        text = _fit_text(draw, design.caption, font, (w - 24) * ss)
        draw.text(((x + w / 2) * ss, (y + h / 2) * ss), text, font=font, fill=_rgb(col), anchor="mm")

    # logo
    if lbox:
        x, y, s, _, pad = lbox
        draw.rounded_rectangle(
            [(x - pad) * ss, (y - pad) * ss, (x + s + pad) * ss, (y + s + pad) * ss],
            radius=pad * 1.5 * ss, fill=_rgb(design.background) + (255,),
        )
        logo = _decode_logo(design.logo)
        logo.thumbnail((int(s * ss), int(s * ss)), Image.LANCZOS)
        lx = int((x + (s - logo.width / ss) / 2) * ss)
        ly = int((y + (s - logo.height / ss) / 2) * ss)
        canvas.alpha_composite(logo, (lx, ly))

    out = canvas.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _with_mask(paint: Image.Image, mask: Image.Image) -> Image.Image:
    layer = paint.copy()
    layer.putalpha(mask)
    return layer


def _fit_text(draw, text, font, max_w):
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _draw_frame_png(draw: ImageDraw.ImageDraw, lay: Layout, d: Design, k: float):
    x, y, w, h, sw = lay.frame
    col = _rgb(d.frame_color)
    lw = max(1, int(sw * k))
    box = [x * k, y * k, (x + w) * k, (y + h) * k]
    st = d.frame_style
    if st in ("square", "banner"):
        draw.rectangle(box, outline=col, width=lw)
    elif st == "rounded":
        draw.rounded_rectangle(box, radius=sw * 5 * k, outline=col, width=lw)
    elif st == "double":
        draw.rectangle(box, outline=col, width=lw)
        g = sw * 1.4
        draw.rectangle([(x + g) * k, (y + g) * k, (x + w - g) * k, (y + h - g) * k], outline=col, width=max(1, lw // 2))
    elif st == "dashed":
        dash, gap = sw * 3, sw * 2
        for (ax, ay, bx, by) in [(x, y, x + w, y), (x + w, y, x + w, y + h), (x + w, y + h, x, y + h), (x, y + h, x, y)]:
            length = math.hypot(bx - ax, by - ay)
            ux, uy = (bx - ax) / length, (by - ay) / length
            p = 0.0
            while p < length:
                q = min(p + dash, length)
                draw.line([((ax + ux * p) * k, (ay + uy * p) * k), ((ax + ux * q) * k, (ay + uy * q) * k)], fill=col, width=lw)
                p += dash + gap
    elif st == "corners":
        L = min(w, h) * 0.18
        for (cx, cy, dx, dy) in [(x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)]:
            draw.line([(cx * k, (cy + dy * L) * k), (cx * k, cy * k), ((cx + dx * L) * k, cy * k)], fill=col, width=lw, joint="curve")


# --------------------------------------------------------------------------
# SVG rendering
# --------------------------------------------------------------------------
def _svg_rect_path(x, y, w, h, radii):
    tl, tr, br, bl = radii
    return (
        f"M{x + tl:.2f},{y:.2f}H{x + w - tr:.2f}"
        + (f"A{tr:.2f},{tr:.2f} 0 0 1 {x + w:.2f},{y + tr:.2f}" if tr else "")
        + f"V{y + h - br:.2f}"
        + (f"A{br:.2f},{br:.2f} 0 0 1 {x + w - br:.2f},{y + h:.2f}" if br else "")
        + f"H{x + bl:.2f}"
        + (f"A{bl:.2f},{bl:.2f} 0 0 1 {x:.2f},{y + h - bl:.2f}" if bl else "")
        + f"V{y + tl:.2f}"
        + (f"A{tl:.2f},{tl:.2f} 0 0 1 {x + tl:.2f},{y:.2f}" if tl else "")
        + "Z"
    )


def _svg_shape_path(sh):
    if sh[0] == "rect":
        return _svg_rect_path(*sh[1:5], sh[5])
    if sh[0] == "circle":
        _, cx, cy, r = sh
        return f"M{cx - r:.2f},{cy:.2f}a{r:.2f},{r:.2f} 0 1 0 {2 * r:.2f},0a{r:.2f},{r:.2f} 0 1 0 {-2 * r:.2f},0Z"
    pts = sh[1]
    return "M" + "L".join(f"{px:.2f},{py:.2f}" for px, py in pts) + "Z"


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_svg(payload: str, design: Design) -> str:
    matrix, _ = make_matrix(payload, design)
    lay = compute_layout(len(matrix), design)
    body, eyes = module_shapes(matrix, lay, design)
    lbox = logo_box(lay, design)
    body = _clear_logo_area(body, lbox, lay)
    W, H = lay.width, lay.height
    d = design
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}">']
    defs = []

    def fill_for(c1, c2, gid):
        if d.gradient == "none" or not c2:
            return c1
        x0, y0, s = lay.qr_x, lay.qr_y, lay.qr_size
        if d.gradient == "linear":
            defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{x0}" y1="{y0}" x2="{x0 + s}" y2="{y0 + s}">'
                        f'<stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>')
        else:
            defs.append(f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="{x0 + s / 2}" cy="{y0 + s / 2}" r="{s / 2}">'
                        f'<stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></radialGradient>')
        return f"url(#{gid})"

    body_fill = fill_for(d.foreground, d.foreground2, "g-body")
    eye_fill = fill_for(d.eye_color or d.foreground, "" if d.eye_color else d.foreground2, "g-eye")
    if defs:
        out.append("<defs>" + "".join(defs) + "</defs>")

    if not d.transparent:
        out.append(f'<rect width="100%" height="100%" fill="{d.background}"/>')

    out.append(f'<path fill="{body_fill}" d="' + "".join(_svg_shape_path(s) for s in body) + '"/>')
    # eyes: outer ring via evenodd (outer + inner hole), then centre
    eye_paths = []
    for i in range(0, len(eyes), 3):
        outer, hole, centre = eyes[i][0], eyes[i + 1][0], eyes[i + 2][0]
        eye_paths.append(f'<path fill-rule="evenodd" d="{_svg_shape_path(outer)}{_svg_shape_path(hole)}"/>')
        eye_paths.append(f'<path d="{_svg_shape_path(centre)}"/>')
    out.append(f'<g fill="{eye_fill}">' + "".join(eye_paths) + "</g>")

    if lay.frame:
        x, y, w, h, sw = lay.frame
        st = d.frame_style
        common = f'fill="none" stroke="{d.frame_color}" stroke-width="{sw:.2f}"'
        if st in ("square", "banner"):
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" {common}/>')
        elif st == "rounded":
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{sw * 5}" {common}/>')
        elif st == "dashed":
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" stroke-dasharray="{sw * 3} {sw * 2}" {common}/>')
        elif st == "double":
            g = sw * 1.4
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" {common}/>')
            out.append(f'<rect x="{x + g}" y="{y + g}" width="{w - 2 * g}" height="{h - 2 * g}" fill="none" stroke="{d.frame_color}" stroke-width="{sw / 2:.2f}"/>')
        elif st == "corners":
            L = min(w, h) * 0.18
            segs = []
            for (cx, cy, dx, dy) in [(x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)]:
                segs.append(f"M{cx},{cy + dy * L}L{cx},{cy}L{cx + dx * L},{cy}")
            out.append(f'<path d="{"".join(segs)}" {common} stroke-linecap="round" stroke-linejoin="round"/>')

    if lay.caption_box and d.caption:
        x, y, w, h = lay.caption_box
        if d.frame_style == "banner":
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h + lay.frame[4]}" fill="{d.frame_color}"/>')
            col = d.caption_color or d.background
        else:
            col = d.caption_color or d.frame_color
        out.append(f'<text x="{x + w / 2:.2f}" y="{y + h / 2:.2f}" fill="{col}" font-family="Arial, Helvetica, sans-serif" '
                   f'font-weight="700" font-size="{lay.font_size:.1f}" text-anchor="middle" dominant-baseline="central">{_xml(d.caption)}</text>')

    if lbox:
        x, y, s, _, pad = lbox
        out.append(f'<rect x="{x - pad:.2f}" y="{y - pad:.2f}" width="{s + 2 * pad:.2f}" height="{s + 2 * pad:.2f}" rx="{pad * 1.5:.2f}" fill="{d.background}"/>')
        out.append(f'<image href="{_xml(d.logo)}" x="{x:.2f}" y="{y:.2f}" width="{s:.2f}" height="{s:.2f}" preserveAspectRatio="xMidYMid meet"/>')

    out.append("</svg>")
    return "".join(out)


def describe(payload: str, design: Design) -> dict:
    """Small info block for the UI (version, modules, effective EC level)."""
    matrix, version = make_matrix(payload, design)
    return {
        "version": version,
        "modules": len(matrix),
        "error_level": "H" if design.logo else design.error_level,
        "bytes": len(payload.encode("utf-8")),
    }
