"""Metadati video e telemetria GPS (velocità) per la modalità Live."""

from __future__ import annotations

import math
import os
import re
import struct
from dataclasses import dataclass
from datetime import datetime

import av
from defusedxml import ElementTree as ET

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".mts", ".m2ts", ".webm", ".wmv", ".insv", ".lrv"}


@dataclass
class Sample:
    t: float          # secondi dall'inizio del video
    lat: float
    lon: float
    alt: float | None
    kmh: float | None


# ------------------------------------------------------------------ metadati

def parse_iso6709(s: str):
    """'+41.8902+012.4922+021.000/' -> (lat, lon, alt)."""
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?", s.strip())
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    alt = float(m.group(3)) if m.group(3) else None
    return lat, lon, alt


def _fmt_dur(sec: float) -> str:
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _dt(s: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(s, fmt)
            out = d.strftime("%d/%m/%Y %H:%M:%S")
            if d.utcoffset() is not None:
                off = d.utcoffset().total_seconds() / 3600
                out += " UTC" if fmt.endswith("Z") or off == 0 else f" (UTC{off:+g})"
            return out
        except ValueError:
            continue
    return s


def probe(path: str) -> dict:
    """Ritorna dict con summary, sections, gps, has_video."""
    out = {"summary": [], "sections": {}, "gps": None}
    add = lambda sec, k, v: v not in (None, "") and out["sections"].setdefault(sec, []).append((k, str(v)))
    with av.open(path) as c:
        meta = {k.lower(): v for k, v in (c.metadata or {}).items()}
        dur = c.duration / 1e6 if c.duration else None
        sec = "Video"
        if dur:
            add(sec, "Durata", f"{_fmt_dur(dur)} ({dur:.2f} s)")
        add(sec, "Contenitore", c.format.long_name)
        if c.bit_rate:
            add(sec, "Bitrate totale", f"{c.bit_rate / 1e6:.1f} Mbit/s")
        vstream = None
        for s in c.streams:
            cc = s.codec_context
            tag = s.codec_tag if hasattr(s, "codec_tag") else ""
            if s.type == "video" and vstream is None:
                vstream = s
                fps = float(s.average_rate) if s.average_rate else None
                add(sec, "Risoluzione", f"{cc.width} × {cc.height} px")
                if fps:
                    add(sec, "Fotogrammi al secondo", f"{fps:.2f} fps")
                add(sec, "Codec video", f"{cc.name.upper()} ({tag})" if tag else cc.name.upper())
                if s.frames:
                    add(sec, "Numero fotogrammi", s.frames)
                rot = _rotation(s)
                if rot:
                    add(sec, "Rotazione", f"{rot}°")
            elif s.type == "audio":
                add("Audio", "Codec", cc.name.upper())
                add("Audio", "Canali", getattr(cc, "channels", None))
                add("Audio", "Frequenza", f"{cc.sample_rate} Hz" if cc.sample_rate else None)
            elif s.type == "data":
                add("Tracce dati", f"Traccia {s.index}", f"{tag or 'dati'} - {s.metadata.get('handler_name', '')}".strip(" -"))
            for k, v in (s.metadata or {}).items():
                if k not in ("language", "handler_name", "vendor_id"):
                    add(f"Tag traccia {s.index} ({s.type})", k, v)
        for k, v in sorted(meta.items()):
            add("Tag contenitore", k, v)
        vinfo = None
        if vstream is not None:
            cc = vstream.codec_context
            fps = float(vstream.average_rate) if vstream.average_rate else 0
            vinfo = f"{cc.width}×{cc.height} @ {fps:.0f} fps, {cc.name.upper()}"

    # riepilogo leggibile
    s = out["summary"]
    make = meta.get("com.apple.quicktime.make") or meta.get("make") or meta.get("com.android.manufacturer")
    model = meta.get("com.apple.quicktime.model") or meta.get("model") or meta.get("com.android.model")
    if not (make or model) and "gopro" in (meta.get("encoder", "") + meta.get("firmware", "")).lower():
        make = "GoPro"
    fw = meta.get("firmware", "")
    if fw and not model:
        gm = re.match(r"HD(\d+)\.", fw)
        hm = re.match(r"H(2\d)\.", fw)
        if gm:
            make, model = "GoPro", f"HERO{gm[1]} (firmware {fw})"
        elif hm:
            make, model = "GoPro", f"HERO{int(hm[1]) - 11} (firmware {fw})"
        else:
            model = fw
    if make or model:
        dev = model if make and model and model.lower().startswith(make.lower()) else f"{make or ''} {model or ''}".strip()
        s.append(("Dispositivo", dev))
    when = meta.get("com.apple.quicktime.creationdate") or meta.get("creation_time") or meta.get("date")
    if when:
        s.append(("Data ripresa", _dt(when)))
    sw = meta.get("com.apple.quicktime.software") or meta.get("software") or meta.get("com.android.version")
    if sw:
        s.append(("Software", sw))
    if dur:
        s.append(("Durata", _fmt_dur(dur)))
    if vinfo:
        s.append(("Qualità", vinfo))
    loc = (meta.get("com.apple.quicktime.location.iso6709") or meta.get("location")
           or meta.get("location-eng"))
    if loc:
        p = parse_iso6709(loc)
        if p:
            lat, lon, alt = p
            out["gps"] = (lat, lon)
            add("Posizione GPS", "Latitudine", f"{lat:.6f}")
            add("Posizione GPS", "Longitudine", f"{lon:.6f}")
            if alt is not None:
                add("Posizione GPS", "Altitudine", f"{alt:.1f} m s.l.m.")
            acc = meta.get("com.apple.quicktime.location.accuracy.horizontal")
            if acc:
                add("Posizione GPS", "Precisione", f"± {float(acc):.0f} m")
            s.append(("Coordinate", f"{lat:.6f}, {lon:.6f}"))
            if alt is not None:
                s.append(("Altitudine", f"{alt:.0f} m"))
    return out


def _rotation(stream) -> int:
    try:
        for sd in stream.side_data:
            if "rotation" in str(sd).lower():
                return int(getattr(sd, "rotation", 0))
    except Exception:
        pass
    try:
        return int(stream.metadata.get("rotate", 0))
    except Exception:
        return 0


# ------------------------------------------------------------------ telemetria

def telemetry(path: str) -> tuple[list[Sample], str]:
    """Cerca dati GPS nel tempo. Ritorna (campioni, sorgente)."""
    base = os.path.splitext(path)[0]
    for ext in (".SRT", ".srt"):
        if os.path.isfile(base + ext):
            pts = _dji_srt(base + ext)
            if pts:
                return _fill_speed(pts), "file DJI .SRT"
    for ext in (".gpx", ".GPX"):
        if os.path.isfile(base + ext):
            pts = _gpx(base + ext, path)
            if pts:
                return _fill_speed(pts), "file .GPX"
    try:
        pts = _gpmf(path)
        if pts:
            return _fill_speed(pts), "telemetria GoPro (GPMF)"
    except Exception:
        pass
    try:
        pts = _camm(path)
        if pts:
            return _fill_speed(pts), "telemetria CAMM"
    except Exception:
        pass
    return [], ""


def _haversine(a: Sample, b: Sample) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _clean(pts: list[Sample]) -> list[Sample]:
    """Scarta salti GPS impossibili (> 400 km/h tra due punti consecutivi)."""
    out: list[Sample] = []
    for p in pts:
        if out:
            dt = p.t - out[-1].t
            if dt > 0 and _haversine(out[-1], p) / dt * 3.6 > 400:
                continue
        out.append(p)
    return out


def _fill_speed(pts: list[Sample]) -> list[Sample]:
    """Calcola km/h dove manca, dalle posizioni (finestra ~1 s)."""
    pts.sort(key=lambda p: p.t)
    pts[:] = _clean(pts)
    if all(p.kmh is not None for p in pts):
        return pts
    j = 0
    for i, p in enumerate(pts):
        if p.kmh is not None:
            continue
        while j < i and p.t - pts[j].t > 1.0:
            j += 1
        a = pts[j] if j < i else (pts[i - 1] if i else None)
        if a is None or p.t - a.t <= 0:
            p.kmh = 0.0 if i == 0 else pts[i - 1].kmh
            continue
        p.kmh = _haversine(a, p) / (p.t - a.t) * 3.6
    return pts


def stats(pts: list[Sample]) -> dict:
    if not pts:
        return {}
    # distanza = integrale della velocità (meno sensibile al rumore GPS)
    dist = sum((a.kmh or 0) / 3.6 * (b.t - a.t) for a, b in zip(pts, pts[1:]))
    sp = [p.kmh for p in pts if p.kmh is not None]
    alts = [p.alt for p in pts if p.alt is not None]
    d = {"distanza_km": dist / 1000, "max_kmh": max(sp) if sp else 0,
         "media_kmh": sum(sp) / len(sp) if sp else 0, "punti": len(pts)}
    if alts:
        d["alt_min"], d["alt_max"] = min(alts), max(alts)
    return d


def speed_at(pts: list[Sample], t: float) -> Sample | None:
    """Campione interpolato all'istante t."""
    if not pts:
        return None
    if t <= pts[0].t:
        return pts[0]
    if t >= pts[-1].t:
        return pts[-1]
    lo, hi = 0, len(pts) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if pts[mid].t <= t:
            lo = mid
        else:
            hi = mid
    a, b = pts[lo], pts[hi]
    f = (t - a.t) / (b.t - a.t) if b.t > a.t else 0
    lerp = lambda x, y: None if x is None or y is None else x + (y - x) * f
    return Sample(t, lerp(a.lat, b.lat), lerp(a.lon, b.lon), lerp(a.alt, b.alt), lerp(a.kmh, b.kmh))


# ---- GoPro GPMF

def _gpmf_walk(buf: bytes, scal_ctx: list):
    """Generatore di (key, type, size, repeat, payload) su un livello, ricorsivo sui contenitori."""
    i, n = 0, len(buf)
    while i + 8 <= n:
        key = buf[i:i + 4]
        typ = chr(buf[i + 4])
        size = buf[i + 5]
        rep = struct.unpack(">H", buf[i + 6:i + 8])[0]
        ln = size * rep
        payload = buf[i + 8:i + 8 + ln]
        i += 8 + ((ln + 3) & ~3)
        if typ == "\x00":
            yield from _gpmf_walk(payload, scal_ctx)
        else:
            yield key, typ, size, rep, payload


def _gpmf_packet(buf: bytes) -> list[tuple]:
    """Estrae righe GPS (lat, lon, alt, speed2d m/s) da un pacchetto GPMF."""
    rows: list[tuple] = []
    scal = None
    fix = None   # 0 = nessun segnale, 2 = 2D, 3 = 3D
    dop = None   # precisione x100 (sotto 500 = buona)
    for key, typ, size, rep, payload in _gpmf_walk(buf, []):
        if key == b"GPSF" and len(payload) >= 4:
            fix = struct.unpack(">I", payload[:4])[0]
        elif key == b"GPSP" and len(payload) >= 2:
            dop = struct.unpack(">H", payload[:2])[0]
        if key == b"SCAL":
            fmt = {"l": "i", "L": "I", "s": "h", "S": "H"}.get(typ)
            if fmt:
                cnt = size // struct.calcsize(fmt)
                scal = struct.unpack(f">{cnt * rep}{fmt}", payload)
        elif key == b"GPS5" and typ == "l":
            if fix is not None and fix < 2 or dop is not None and dop >= 500:
                continue
            vals = struct.unpack(f">{5 * rep}i", payload)
            sc = scal if scal and len(scal) >= 5 else (1,) * 5
            for r in range(rep):
                v = vals[r * 5:(r + 1) * 5]
                rows.append((v[0] / sc[0], v[1] / sc[1], v[2] / sc[2], v[3] / sc[3]))
        elif key == b"GPS9":
            # lat, lon, alt, speed2d, speed3d (int32) + giorni, secondi, dop, fix
            stride = size
            sc = scal if scal and len(scal) >= 5 else (1,) * 9
            for r in range(rep):
                chunk = payload[r * stride:r * stride + 20]
                if len(chunk) < 20:
                    break
                v = struct.unpack(">5i", chunk)
                if len(payload) >= (r + 1) * stride and stride >= 32:
                    f9 = struct.unpack(">H", payload[r * stride + 30:r * stride + 32])[0]
                    if f9 < 2:
                        continue
                rows.append((v[0] / sc[0], v[1] / sc[1], v[2] / sc[2], v[3] / sc[3]))
    return rows


def _gpmf(path: str) -> list[Sample]:
    pts: list[Sample] = []
    with av.open(path) as c:
        streams = [s for s in c.streams if s.type == "data" and
                   ("gpmd" in str(getattr(s, "codec_tag", "")) or "GoPro MET" in s.metadata.get("handler_name", ""))]
        if not streams:
            return []
        st = streams[0]
        tb = float(st.time_base) if st.time_base else 1 / 1000
        for pkt in c.demux(st):
            if pkt.size == 0 or pkt.pts is None:
                continue
            rows = _gpmf_packet(bytes(pkt))
            if not rows:
                continue
            t0 = pkt.pts * tb
            dur = (pkt.duration or 0) * tb or 1.0
            for k, (lat, lon, alt, sp) in enumerate(rows):
                if lat == 0 and lon == 0:
                    continue
                pts.append(Sample(t0 + dur * k / len(rows), lat, lon, alt, sp * 3.6))
    return pts


# ---- CAMM (Google/Insta360) tipo 5/6 = GPS

def _camm(path: str) -> list[Sample]:
    pts: list[Sample] = []
    with av.open(path) as c:
        streams = [s for s in c.streams if s.type == "data" and "camm" in str(getattr(s, "codec_tag", ""))]
        if not streams:
            return []
        st = streams[0]
        tb = float(st.time_base)
        for pkt in c.demux(st):
            b = bytes(pkt)
            if len(b) < 4 or pkt.pts is None:
                continue
            typ = struct.unpack("<H", b[2:4])[0]
            if typ == 5 and len(b) >= 28:
                lat, lon, alt = struct.unpack("<3d", b[4:28])
                pts.append(Sample(pkt.pts * tb, lat, lon, alt, None))
            elif typ == 6 and len(b) >= 60:
                _ts, _fix, lat, lon, alt = struct.unpack("<dIddf", b[4:40])[:5]
                ve, vn = struct.unpack("<ff", b[48:56])
                pts.append(Sample(pkt.pts * tb, lat, lon, alt, math.hypot(ve, vn) * 3.6))
    return pts


# ---- DJI SRT

def _dji_srt(path: str) -> list[Sample]:
    txt = open(path, encoding="utf-8", errors="ignore").read()
    pts = []
    for block in re.split(r"\n\s*\n", txt):
        tm = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->", block)
        lat = re.search(r"latitude\s*[:=]\s*([-\d.]+)", block, re.I) or re.search(r"GPS\s*\(([-\d.]+),\s*([-\d.]+)", block)
        lon = re.search(r"longitude\s*[:=]\s*([-\d.]+)", block, re.I)
        if not tm or not lat:
            continue
        t = int(tm[1]) * 3600 + int(tm[2]) * 60 + int(tm[3]) + int(tm[4]) / 1000
        if lon:
            la, lo = float(lat[1]), float(lon[1])
        else:  # formato vecchio GPS(lon, lat, alt)
            lo, la = float(lat[1]), float(lat[2])
        alt = re.search(r"(?:abs_alt|altitude)\s*[:=]\s*([-\d.]+)", block, re.I)
        if la == 0 and lo == 0:
            continue
        pts.append(Sample(t, la, lo, float(alt[1]) if alt else None, None))
    return pts


# ---- GPX accanto al video

def _gpx(gpx_path: str, video_path: str) -> list[Sample]:
    root = ET.parse(gpx_path).getroot()
    rows = []
    for el in root.iter():
        if el.tag.endswith("trkpt"):
            t_el = next((c for c in el if c.tag.endswith("time")), None)
            e_el = next((c for c in el if c.tag.endswith("ele")), None)
            if t_el is None:
                continue
            ts = datetime.fromisoformat(t_el.text.replace("Z", "+00:00")).timestamp()
            rows.append((ts, float(el.get("lat")), float(el.get("lon")), float(e_el.text) if e_el is not None else None))
    if not rows:
        return []
    t0 = rows[0][0]
    return [Sample(ts - t0, la, lo, al, None) for ts, la, lo, al in rows]


if __name__ == "__main__":
    import sys

    for p in sys.argv[1:]:
        pts, src = telemetry(p)
        print(os.path.basename(p), src, len(pts), stats(pts))
        for x in pts[:: max(1, len(pts) // 5)]:
            print(f"  t={x.t:6.2f}s  {x.lat:.6f},{x.lon:.6f}  alt={x.alt}  {x.kmh:.1f} km/h")
