"""Estrazione metadati da foto, documenti e file generici."""

from __future__ import annotations

import hashlib
import json
import re
import mimetypes
import os
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from defusedxml import ElementTree as ET

from PIL import ExifTags, Image

import video

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # HEIC opzionale
    pass

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic", ".heif",
             ".bmp", ".gif", ".dng"}
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"}
ODF_EXT = {".odt", ".ods", ".odp"}


@dataclass
class Result:
    path: str
    kind: str = "file"
    summary: list[tuple[str, str]] = field(default_factory=list)
    sections: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    gps: tuple[float, float] | None = None
    errors: list[str] = field(default_factory=list)
    warning: str = ""
    address: str | None = None
    telemetry: list = field(default_factory=list)
    telemetry_src: str = ""

    def add(self, section: str, key: str, value) -> None:
        if value is None or value == "":
            return
        self.sections.setdefault(section, []).append((key, str(value)))

    def to_dict(self) -> dict:
        return {
            "file": self.path,
            "tipo": self.kind,
            "riepilogo": dict(self.summary),
            "gps": {"lat": self.gps[0], "lon": self.gps[1]} if self.gps else None,
            "luogo": self.address,
            "avviso": self.warning or None,
            "sezioni": {k: dict(v) for k, v in self.sections.items()},
            "errori": self.errors,
        }


# ---------------------------------------------------------------- generico

def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return str(n)


def _file_info(res: Result) -> None:
    p = res.path
    st = os.stat(p)
    sha = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    sec = "File"
    res.add(sec, "Nome", os.path.basename(p))
    res.add(sec, "Percorso", os.path.dirname(p))
    res.add(sec, "Dimensione", f"{_fmt_size(st.st_size)} ({st.st_size:,} byte)")
    res.add(sec, "Tipo MIME", mimetypes.guess_type(p)[0] or "sconosciuto")
    res.add(sec, "Creato (filesystem)", datetime.fromtimestamp(st.st_ctime).strftime("%d/%m/%Y %H:%M:%S"))
    res.add(sec, "Modificato (filesystem)", datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M:%S"))
    res.add(sec, "Ultimo accesso", datetime.fromtimestamp(st.st_atime).strftime("%d/%m/%Y %H:%M:%S"))
    res.add(sec, "SHA-256", sha.hexdigest())


# ---------------------------------------------------------------- immagini

def _val(v) -> str:
    """Rende leggibile un valore EXIF."""
    if isinstance(v, bytes):
        # UserComment ha 8 byte di intestazione codifica
        if v[:8] in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"\x00" * 8):
            body = v[8:]
            enc = "utf-16" if v[:7] == b"UNICODE" else "latin-1"
            v = body
            try:
                s = body.decode(enc).strip("\x00 ")
                return s
            except Exception:
                pass
        if len(v) > 64:
            return f"<dati binari, {len(v)} byte>"
        try:
            s = v.decode("utf-8").strip("\x00 ")
            if s.isprintable():
                return s
        except UnicodeDecodeError:
            pass
        return v.hex(" ")
    if isinstance(v, tuple):
        return ", ".join(_val(x) for x in v)
    if hasattr(v, "numerator") and hasattr(v, "denominator"):
        try:
            f = float(v)
        except ZeroDivisionError:
            return "0"
        return f"{f:g}"
    if isinstance(v, str):
        return v.strip("\x00 ")
    return str(v)


def _dms_to_deg(dms, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in dms)
    except Exception:
        return None
    deg = d + m / 60 + s / 3600
    if str(ref).upper().startswith(("S", "W")):
        deg = -deg
    return deg


def _deg_to_dms(deg: float, pos: str, neg: str) -> str:
    h = pos if deg >= 0 else neg
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = (deg - d - m / 60) * 3600
    return f"{d}° {m}' {s:.2f}\" {h}"


FLASH = {0: "Non scattato", 1: "Scattato", 5: "Scattato, ritorno non rilevato",
         7: "Scattato, ritorno rilevato", 9: "Scattato (forzato)", 16: "Non scattato (forzato off)",
         24: "Non scattato (auto)", 25: "Scattato (auto)", 31: "Scattato (auto, ritorno rilevato)",
         32: "Nessun flash", 65: "Scattato, riduzione occhi rossi", 89: "Scattato (auto, occhi rossi)"}
EXPOSURE_PROGRAM = {0: "Non definito", 1: "Manuale", 2: "Programma normale", 3: "Priorità diaframma",
                    4: "Priorità tempi", 5: "Creativo", 6: "Azione", 7: "Ritratto", 8: "Paesaggio"}
ORIENTATION = {1: "Normale", 2: "Specchiata orizz.", 3: "Ruotata 180°", 4: "Specchiata vert.",
               5: "Specchiata + 90° antiorario", 6: "Ruotata 90° orario", 7: "Specchiata + 90° orario",
               8: "Ruotata 90° antiorario"}
METERING = {0: "Sconosciuta", 1: "Media", 2: "Media pesata al centro", 3: "Spot", 4: "Multi-spot",
            5: "Matrix/valutativa", 6: "Parziale"}
WHITE_BALANCE = {0: "Automatico", 1: "Manuale"}


def _exif_dt(s: str) -> str:
    try:
        return datetime.strptime(s.strip("\x00 "), "%Y:%m:%d %H:%M:%S").strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return s


def _image(res: Result) -> None:
    res.kind = "immagine"
    with Image.open(res.path) as img:
        sec = "Immagine"
        res.add(sec, "Formato", f"{img.format} ({img.format_description})" if img.format_description else img.format)
        res.add(sec, "Risoluzione", f"{img.width} × {img.height} px ({img.width * img.height / 1e6:.1f} MP)")
        res.add(sec, "Modalità colore", img.mode)
        if "dpi" in img.info:
            try:
                res.add(sec, "DPI", " × ".join(f"{float(x):g}" for x in img.info["dpi"]))
            except Exception:
                pass
        if img.info.get("icc_profile"):
            res.add(sec, "Profilo colore ICC", f"presente ({len(img.info['icc_profile'])} byte)")
        if getattr(img, "n_frames", 1) > 1:
            res.add(sec, "Fotogrammi", img.n_frames)

        # testi PNG / commenti
        for k, v in img.info.items():
            if k in ("dpi", "icc_profile", "exif", "xmp", "XML:com.adobe.xmp", "transparency",
                     "jfif", "jfif_version", "jfif_unit", "jfif_density", "progressive",
                     "progression", "adobe", "adobe_transform", "photoshop", "mp", "loop",
                     "duration", "background", "interlace", "gamma", "aspect"):
                continue
            if isinstance(v, (str, bytes)):
                res.add("Testi incorporati", k, _val(v))

        exif = img.getexif()
        flat: dict[str, object] = {}
        for tag, v in exif.items():
            flat[ExifTags.TAGS.get(tag, f"Tag 0x{tag:04X}")] = v
        for ifd_id, section in ((ExifTags.IFD.Exif, "EXIF"), (ExifTags.IFD.Interop, "Interoperabilità")):
            try:
                for tag, v in exif.get_ifd(ifd_id).items():
                    flat[ExifTags.TAGS.get(tag, f"Tag 0x{tag:04X}")] = v
            except Exception:
                pass
        gps_raw = {}
        try:
            for tag, v in exif.get_ifd(ExifTags.IFD.GPSInfo).items():
                gps_raw[ExifTags.GPSTAGS.get(tag, f"GPS 0x{tag:02X}")] = v
        except Exception:
            pass

        for k, v in sorted(flat.items()):
            if k in ("ExifOffset", "GPSInfo", "ExifInteroperabilityOffset"):
                continue
            res.add("EXIF (completo)", k, _val(v))
        for k, v in gps_raw.items():
            res.add("GPS (grezzo)", k, _val(v))

        # XMP
        xmp = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp")
        if xmp:
            _parse_xmp(res, xmp)

    # ---------- riepilogo leggibile
    s = res.summary
    make = _val(flat.get("Make", "")).strip()
    model = _val(flat.get("Model", "")).strip()
    if make or model:
        dev = model if make and model.lower().startswith(make.lower().split()[0]) else f"{make} {model}".strip()
        s.append(("Dispositivo", dev))
    if flat.get("LensModel"):
        s.append(("Obiettivo", _val(flat["LensModel"])))
    dt = flat.get("DateTimeOriginal") or flat.get("DateTimeDigitized") or flat.get("DateTime")
    if dt:
        off = flat.get("OffsetTimeOriginal") or flat.get("OffsetTime")
        s.append(("Data scatto", _exif_dt(_val(dt)) + (f" (UTC{_val(off)})" if off else "")))
    if flat.get("Software"):
        s.append(("Software", _val(flat["Software"])))
    if flat.get("Artist"):
        s.append(("Autore", _val(flat["Artist"])))
    if flat.get("Copyright"):
        s.append(("Copyright", _val(flat["Copyright"])))
    if flat.get("ImageDescription") and _val(flat["ImageDescription"]).strip():
        s.append(("Descrizione", _val(flat["ImageDescription"])))
    if flat.get("BodySerialNumber"):
        s.append(("N. serie fotocamera", _val(flat["BodySerialNumber"])))

    shot = []
    if flat.get("ExposureTime"):
        et = float(flat["ExposureTime"])
        shot.append(f"1/{round(1 / et)} s" if 0 < et < 1 else f"{et:g} s")
    if flat.get("FNumber"):
        shot.append(f"f/{float(flat['FNumber']):g}")
    iso = flat.get("ISOSpeedRatings") or flat.get("PhotographicSensitivity")
    if iso:
        shot.append(f"ISO {_val(iso)}")
    if flat.get("FocalLength"):
        fl = f"{float(flat['FocalLength']):g} mm"
        if flat.get("FocalLengthIn35mmFilm"):
            fl += f" (eq. {flat['FocalLengthIn35mmFilm']} mm)"
        shot.append(fl)
    if shot:
        s.append(("Scatto", " · ".join(shot)))
    if "Flash" in flat:
        s.append(("Flash", FLASH.get(int(flat["Flash"]), str(flat["Flash"]))))
    if "ExposureProgram" in flat:
        s.append(("Programma", EXPOSURE_PROGRAM.get(int(flat["ExposureProgram"]), str(flat["ExposureProgram"]))))
    if "MeteringMode" in flat:
        s.append(("Misurazione", METERING.get(int(flat["MeteringMode"]), str(flat["MeteringMode"]))))
    if "WhiteBalance" in flat:
        s.append(("Bilanciamento bianco", WHITE_BALANCE.get(int(flat["WhiteBalance"]), str(flat["WhiteBalance"]))))
    if "Orientation" in flat:
        s.append(("Orientamento", ORIENTATION.get(int(flat["Orientation"]), str(flat["Orientation"]))))

    # GPS
    lat = _dms_to_deg(gps_raw.get("GPSLatitude"), gps_raw.get("GPSLatitudeRef", "N")) if "GPSLatitude" in gps_raw else None
    lon = _dms_to_deg(gps_raw.get("GPSLongitude"), gps_raw.get("GPSLongitudeRef", "E")) if "GPSLongitude" in gps_raw else None
    if lat is not None and lon is not None and not (lat == 0 and lon == 0):
        res.gps = (lat, lon)
        sec = "Posizione GPS"
        res.add(sec, "Latitudine", f"{lat:.6f}  ({_deg_to_dms(lat, 'N', 'S')})")
        res.add(sec, "Longitudine", f"{lon:.6f}  ({_deg_to_dms(lon, 'E', 'O')})")
        s.append(("Coordinate", f"{lat:.6f}, {lon:.6f}"))
        if "GPSAltitude" in gps_raw:
            alt = float(gps_raw["GPSAltitude"])
            below = gps_raw.get("GPSAltitudeRef") in (1, b"\x01")
            res.add(sec, "Altitudine", f"{'-' if below else ''}{alt:.1f} m s.l.m.")
            s.append(("Altitudine", f"{'-' if below else ''}{alt:.1f} m"))
        if "GPSImgDirection" in gps_raw:
            d = float(gps_raw["GPSImgDirection"])
            ref = {"T": "nord geografico", "M": "nord magnetico"}.get(_val(gps_raw.get("GPSImgDirectionRef", "")), "")
            res.add(sec, "Direzione inquadratura", f"{d:.1f}° {ref}".strip())
            s.append(("Direzione", f"{d:.0f}° ({_compass(d)})"))
        if "GPSSpeed" in gps_raw:
            unit = {"K": "km/h", "M": "mph", "N": "nodi"}.get(_val(gps_raw.get("GPSSpeedRef", "K")), "")
            res.add(sec, "Velocità", f"{float(gps_raw['GPSSpeed']):g} {unit}")
        if "GPSDateStamp" in gps_raw:
            ts = gps_raw.get("GPSTimeStamp")
            t = ":".join(f"{int(float(x)):02d}" for x in ts) if ts else ""
            res.add(sec, "Data/ora GPS (UTC)", f"{_val(gps_raw['GPSDateStamp'])} {t}".strip())
        if "GPSMapDatum" in gps_raw:
            res.add(sec, "Datum", _val(gps_raw["GPSMapDatum"]))
        if "GPSHPositioningError" in gps_raw:
            res.add(sec, "Precisione orizzontale", f"± {float(gps_raw['GPSHPositioningError']):g} m")
    else:
        s.append(("Posizione", "Nessun dato GPS nella foto"))

    if not (make or model or dt):
        name = os.path.basename(res.path)
        wa = re.search(r"WhatsApp (?:Image|Video) (\d{4})-(\d{2})-(\d{2}) at (\d{1,2})\.(\d{2})\.(\d{2})", name)
        if wa:
            y, mo, d, h, mi, se = wa.groups()
            s.insert(0, ("Inviata/ricevuta su WhatsApp", f"{d}/{mo}/{y} {int(h):02d}:{mi}:{se}"))
            res.warning = ("Foto passata da WhatsApp: WhatsApp cancella dispositivo, data di scatto e posizione "
                           "quando invia le foto. Per vedere tutto usa la foto originale (dal telefono o da iCloud).")
        else:
            res.warning = ("Questa foto non contiene metadati. Succede con screenshot, foto scaricate da social/chat "
                           "(WhatsApp, Telegram, Instagram, Facebook) o modificate da app che li cancellano.")


def _compass(d: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    return dirs[int((d % 360) / 45 + 0.5) % 8]


def _parse_xmp(res: Result, xmp) -> None:
    if isinstance(xmp, bytes):
        xmp = xmp.decode("utf-8", "ignore")
    try:
        root = ET.fromstring(xmp[xmp.find("<"):])
    except Exception:
        res.add("XMP", "Contenuto", f"<{len(xmp)} caratteri, non analizzabile>")
        return
    for el in root.iter():
        # attributi di rdf:Description
        for k, v in el.attrib.items():
            name = k.split("}")[-1]
            if name in ("about",) or not v.strip():
                continue
            res.add("XMP", name, v)
        if el.text and el.text.strip() and len(el) == 0:
            name = el.tag.split("}")[-1]
            if name != "li":
                res.add("XMP", name, el.text.strip())
            else:
                parent_name = "valore"
                res.add("XMP", parent_name, el.text.strip())


# ---------------------------------------------------------------- documenti

def _pdf(res: Result) -> None:
    res.kind = "documento PDF"
    from pypdf import PdfReader

    r = PdfReader(res.path)
    sec = "PDF"
    if r.is_encrypted:
        res.add(sec, "Crittografato", "Sì")
        try:
            r.decrypt("")
        except Exception:
            res.summary.append(("🔒 Stato", "PDF protetto da password"))
            return
    res.add(sec, "Pagine", len(r.pages))
    res.add(sec, "Versione PDF", r.pdf_header.replace("%PDF-", ""))
    try:
        box = r.pages[0].mediabox
        w, h = float(box.width) * 25.4 / 72, float(box.height) * 25.4 / 72
        res.add(sec, "Formato pagina", f"{w:.0f} × {h:.0f} mm")
    except Exception:
        pass
    meta = r.metadata or {}
    labels = {"/Title": "Titolo", "/Author": "Autore", "/Subject": "Oggetto", "/Keywords": "Parole chiave",
              "/Creator": "Creato con", "/Producer": "Prodotto da", "/CreationDate": "Data creazione",
              "/ModDate": "Data modifica"}
    for k, v in meta.items():
        label = labels.get(k, k.lstrip("/"))
        if k in ("/CreationDate", "/ModDate"):
            v = _pdf_date(str(v))
        res.add("Proprietà documento", label, v)
    fields = r.get_fields() or {}
    if fields:
        res.add(sec, "Campi modulo", len(fields))
    try:
        if r.xmp_metadata is not None:
            _parse_xmp(res, r.xmp_metadata.stream.get_data())
    except Exception:
        pass
    for k in ("Titolo", "Autore", "Creato con", "Prodotto da", "Data creazione", "Data modifica"):
        for kk, vv in res.sections.get("Proprietà documento", []):
            if kk == k:
                res.summary.append((k, vv))
    res.summary.append(("Pagine", str(len(r.pages))))


def _pdf_date(s: str) -> str:
    s = s.replace("D:", "").replace("'", "")
    try:
        return datetime.strptime(s[:14], "%Y%m%d%H%M%S").strftime("%d/%m/%Y %H:%M:%S") + (f" ({s[14:]})" if s[14:] else "")
    except Exception:
        return s


NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
}
CORE_LABELS = {"title": "Titolo", "subject": "Oggetto", "creator": "Autore", "keywords": "Parole chiave",
               "description": "Descrizione", "lastModifiedBy": "Ultima modifica di", "revision": "Revisione",
               "created": "Data creazione", "modified": "Data modifica", "lastPrinted": "Ultima stampa",
               "category": "Categoria", "language": "Lingua"}
APP_LABELS = {"Application": "Applicazione", "AppVersion": "Versione app", "Company": "Azienda",
              "Manager": "Responsabile", "Pages": "Pagine", "Words": "Parole", "Characters": "Caratteri",
              "Lines": "Righe", "Paragraphs": "Paragrafi", "Slides": "Diapositive", "TotalTime": "Tempo modifica (min)",
              "Template": "Modello"}


def _iso(s: str) -> str:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M:%S UTC")
    except Exception:
        return s


def _office(res: Result) -> None:
    res.kind = "documento Office"
    with zipfile.ZipFile(res.path) as z:
        names = z.namelist()
        if "docProps/core.xml" in names:
            root = ET.fromstring(z.read("docProps/core.xml"))
            for el in root:
                tag = el.tag.split("}")[-1]
                if el.text:
                    v = _iso(el.text) if tag in ("created", "modified", "lastPrinted") else el.text
                    res.add("Proprietà documento", CORE_LABELS.get(tag, tag), v)
        if "docProps/app.xml" in names:
            root = ET.fromstring(z.read("docProps/app.xml"))
            for el in root:
                tag = el.tag.split("}")[-1]
                if el.text and len(el) == 0:
                    res.add("Applicazione", APP_LABELS.get(tag, tag), el.text)
        if "docProps/custom.xml" in names:
            root = ET.fromstring(z.read("docProps/custom.xml"))
            for el in root:
                name = el.attrib.get("name")
                val = next((c.text for c in el), None)
                res.add("Proprietà personalizzate", name, val)
        media = [n for n in names if "/media/" in n]
        if media:
            res.add("Contenuto", "Immagini incorporate", len(media))
        if any(n.endswith("vbaProject.bin") for n in names):
            res.add("Contenuto", "Macro VBA", "PRESENTI")
        if any("comments" in n for n in names):
            res.add("Contenuto", "Commenti", "presenti")
    for sec in ("Proprietà documento", "Applicazione"):
        for k, v in res.sections.get(sec, []):
            if k in ("Titolo", "Autore", "Ultima modifica di", "Data creazione", "Data modifica",
                     "Applicazione", "Azienda", "Pagine", "Revisione"):
                res.summary.append((k, v))


def _odf(res: Result) -> None:
    res.kind = "documento OpenDocument"
    with zipfile.ZipFile(res.path) as z:
        root = ET.fromstring(z.read("meta.xml"))
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "document-statistic":
            for k, v in el.attrib.items():
                res.add("Statistiche", k.split("}")[-1], v)
        elif el.text and el.text.strip() and len(el) == 0:
            if tag == "user-defined":
                tag = el.attrib.get("{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}name", tag)
            res.add("Proprietà documento", tag, el.text.strip())
    res.summary.extend(res.sections.get("Proprietà documento", [])[:8])


# ---------------------------------------------------------------- API

def extract(path: str) -> Result:
    res = Result(path=os.path.abspath(path))
    try:
        _file_info(res)
    except Exception as e:
        res.errors.append(f"Info file: {e}")
        return res
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in IMAGE_EXT:
            _image(res)
        elif ext == ".pdf":
            _pdf(res)
        elif ext in OFFICE_EXT:
            _office(res)
        elif ext in ODF_EXT:
            _odf(res)
        elif ext in video.VIDEO_EXT:
            _video(res)
        else:
            # prova comunque come immagine
            try:
                _image(res)
            except Exception:
                res.summary.append(("Tipo", "Formato non specifico: solo informazioni di base sul file"))
    except Exception as e:
        res.errors.append(f"{type(e).__name__}: {e}")
    return res


def _video(res: Result) -> None:
    res.kind = "video"
    d = video.probe(res.path)
    res.summary.extend(d["summary"])
    for sec, rows in d["sections"].items():
        res.sections.setdefault(sec, []).extend(rows)
    res.gps = d["gps"]
    try:
        pts, src = video.telemetry(res.path)
    except Exception as e:
        pts, src = [], ""
        res.errors.append(f"Telemetria: {e}")
    res.telemetry, res.telemetry_src = pts, src
    if pts:
        st = video.stats(pts)
        res.summary.append(("Velocità massima", f"{st['max_kmh']:.0f} km/h"))
        res.summary.append(("Velocità media", f"{st['media_kmh']:.0f} km/h"))
        res.summary.append(("Distanza percorsa", f"{st['distanza_km']:.2f} km"))
        sec = "Telemetria GPS"
        res.add(sec, "Sorgente", src)
        res.add(sec, "Punti GPS", st["punti"])
        res.add(sec, "Velocità massima", f"{st['max_kmh']:.1f} km/h")
        res.add(sec, "Velocità media", f"{st['media_kmh']:.1f} km/h")
        res.add(sec, "Distanza", f"{st['distanza_km'] * 1000:.0f} m")
        if "alt_min" in st:
            res.add(sec, "Altitudine min/max", f"{st['alt_min']:.0f} / {st['alt_max']:.0f} m")
        if not res.gps:
            res.gps = (pts[0].lat, pts[0].lon)
            res.summary.append(("Coordinate", f"{pts[0].lat:.6f}, {pts[0].lon:.6f}"))
    else:
        res.summary.append(("Velocità", "Non registrata in questo video"))
    if not res.gps and not pts:
        res.summary.append(("Posizione", "Nessun dato GPS nel video"))
    name = os.path.basename(res.path)
    if not any(k == "Dispositivo" for k, _ in res.summary) and re.search(r"WhatsApp|VID-\d{8}-WA", name):
        res.warning = ("Video passato da WhatsApp: WhatsApp cancella dispositivo, data e posizione. "
                       "Usa il video originale dal telefono.")


def reverse_geocode(lat: float, lon: float, timeout: float = 8) -> str | None:
    """Indirizzo dalle coordinate tramite OpenStreetMap Nominatim (richiede internet)."""
    q = urllib.parse.urlencode({"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2",
                                "accept-language": "it", "zoom": 18})
    req = urllib.request.Request(f"https://nominatim.openstreetmap.org/reverse?{q}",
                                 headers={"User-Agent": "Metadat/1.0 (desktop metadata viewer)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return data.get("display_name")


if __name__ == "__main__":
    import sys

    for p in sys.argv[1:]:
        r = extract(p)
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))


