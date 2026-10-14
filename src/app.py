"""Metadat - metadati di foto, video e documenti."""

from __future__ import annotations

import json
import math
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, ttk

from PIL import Image, ImageGrab, ImageOps, ImageTk

import extractor
import video

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
except Exception:  # drag & drop opzionale
    DND_FILES = None
    BaseTk = tk.Tk

try:
    import tkintermapview

    import mapfix
    mapfix.patch(tkintermapview)
except Exception:
    tkintermapview = None

BG = "#f3f4f6"
CARD = "#ffffff"
ACCENT = "#2563eb"
DARK = "#111827"
MUTED = "#6b7280"
WARN_BG = "#fef3c7"
WARN_FG = "#92400e"
F = "Segoe UI"

# campi mostrati nella vista Normale, nell'ordine
NORMAL_KEYS = [
    ("Luogo", "Dove"),
    ("Dispositivo", "Dispositivo"),
    ("Obiettivo", "Fotocamera"),
    ("Data scatto", "Quando"),
    ("Data ripresa", "Quando"),
    ("Inviata/ricevuta su WhatsApp", "Su WhatsApp il"),
    ("Durata", "Durata"),
    ("Velocità massima", "Velocità max"),
    ("Distanza percorsa", "Distanza"),
    ("Velocità", "Velocità"),
    ("Coordinate", "Coordinate"),
    ("Altitudine", "Altitudine"),
    ("Posizione", "Posizione"),
    ("Titolo", "Titolo"),
    ("Autore", "Autore"),
    ("Ultima modifica di", "Modificato da"),
    ("Data creazione", "Creato il"),
    ("Pagine", "Pagine"),
]


def resource(name: str) -> str:
    if hasattr(sys, "_MEIPASS"):  # PyInstaller
        base = sys._MEIPASS
    elif getattr(sys, "frozen", False):  # cx_Freeze / MSI
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


# ====================================================================== player
class VideoPlayer:
    """Decodifica video in un thread, la UI legge l'ultimo fotogramma."""

    def __init__(self, path: str, size: tuple[int, int]):
        self.path = path
        self.size = size
        self.frame: Image.Image | None = None
        self.t = 0.0
        self.duration = 0.0
        self.playing = False
        self._seek: float | None = 0.0
        self._stop = False
        self._lock = threading.Lock()
        with video.av.open(path) as c:
            self.duration = (c.duration or 0) / 1e6
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def _to_img(self, fr) -> Image.Image:
        img = fr.to_image()
        rot = getattr(fr, "rotation", 0) or 0
        if rot:
            img = img.rotate(rot, expand=True)
        img.thumbnail(self.size)
        return img

    def _run(self):
        try:
            c = video.av.open(self.path)
        except Exception:
            return
        vs = c.streams.video[0]
        vs.thread_type = "AUTO"
        tb = float(vs.time_base)
        gen = None
        wall0 = t0 = 0.0
        while not self._stop:
            if self._seek is not None:
                target = self._seek
                self._seek = None
                try:
                    c.seek(int(max(0, target - 0.5) / tb), stream=vs, backward=True)
                except Exception:
                    c.seek(0)
                gen = c.decode(vs)
                fr = None
                for fr in gen:  # avanza fino al punto richiesto
                    if fr.time is not None and fr.time >= target - 0.04:
                        break
                if fr is not None:
                    with self._lock:
                        self.frame, self.t = self._to_img(fr), fr.time or 0
                wall0, t0 = time.perf_counter(), self.t
                continue
            if not self.playing or gen is None:
                time.sleep(0.02)
                wall0, t0 = time.perf_counter(), self.t
                continue
            try:
                fr = next(gen)
            except StopIteration:
                self.playing = False
                continue
            except Exception:
                self.playing = False
                continue
            ft = fr.time or 0
            delay = (ft - t0) - (time.perf_counter() - wall0)
            if delay < -0.25:  # in ritardo: salta il fotogramma
                self.t = ft
                continue
            if delay > 0:
                time.sleep(min(delay, 0.5))
            img = self._to_img(fr)
            with self._lock:
                self.frame, self.t = img, ft
        c.close()

    def get(self):
        with self._lock:
            return self.frame, self.t

    def toggle(self):
        if not self.playing and self.duration and self.t >= self.duration - 0.1:
            self.seek(0)
        self.playing = not self.playing

    def seek(self, t: float):
        self.t = t
        self._seek = t

    def close(self):
        self._stop = True


# ====================================================================== tachimetro
class Speedometer(tk.Canvas):
    def __init__(self, master, size=160):
        super().__init__(master, width=size, height=int(size * 0.93), bg=CARD, highlightthickness=0)
        self.s = size
        self.max = 60
        self.draw(None)

    def set_max(self, vmax: float):
        for m in (20, 40, 60, 80, 120, 160, 200, 260, 320, 400):
            if vmax <= m * 0.95:
                self.max = m
                return
        self.max = math.ceil(vmax / 100) * 100

    def draw(self, kmh: float | None):
        self.delete("all")
        s = self.s
        cx, cy, r = s / 2, s * 0.58, s * 0.44
        self.create_arc(cx - r, cy - r, cx + r, cy + r, start=-30, extent=240, style="arc", width=12,
                        outline="#e5e7eb")
        if kmh is not None:
            frac = max(0.0, min(1.0, kmh / self.max))
            col = "#16a34a" if frac < 0.5 else "#f59e0b" if frac < 0.8 else "#dc2626"
            self.create_arc(cx - r, cy - r, cx + r, cy + r, start=210, extent=-240 * frac, style="arc",
                            width=12, outline=col)
            ang = math.radians(210 - 240 * frac)
            self.create_line(cx, cy, cx + (r - 22) * math.cos(ang), cy - (r - 22) * math.sin(ang), width=3,
                             fill=DARK, capstyle="round")
        for i in range(5):
            v = self.max * i / 4
            a = math.radians(210 - 240 * i / 4)
            self.create_text(cx + (r - 26) * math.cos(a), cy - (r - 26) * math.sin(a), text=f"{v:.0f}",
                             fill=MUTED, font=(F, 8))
        self.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=DARK, outline="")
        self.create_text(cx, cy + r * 0.38, text="--" if kmh is None else f"{kmh:.0f}", font=(F, 20, "bold"),
                         fill=DARK)
        self.create_text(cx, cy + r * 0.38 + 19, text="km/h", font=(F, 9), fill=MUTED)


# ====================================================================== app
class App(BaseTk):
    def __init__(self, files: list[str]):
        super().__init__()
        self.title("Metadat")
        self.geometry("1280x800")
        self.minsize(1000, 640)
        self.configure(bg=BG)
        try:
            self.state("zoomed")
        except Exception:
            pass
        try:
            self.iconbitmap(resource("metadat.ico"))
        except Exception:
            pass

        self.results: dict[str, extractor.Result] = {}
        self.current: extractor.Result | None = None
        self.detailed = tk.BooleanVar(value=False)
        self.player: VideoPlayer | None = None
        self._img = None
        self._marker = None
        self._live_marker = None
        self._path = None
        self._dragging = False
        self._ui_q: queue.Queue = queue.Queue()  # callback dai thread verso la UI

        self._style()
        self._build()
        if DND_FILES:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
        self.bind_all("<Control-v>", self.paste)
        self.bind_all("<Control-V>", self.paste)
        self.bind_all("<Control-o>", lambda e: self.open_dialog())
        self.bind_all("<Control-c>", self._ctrl_c)
        self.bind("<space>", lambda e: self._toggle_play())
        self.protocol("WM_DELETE_WINDOW", self._quit)
        for f in files:
            self.load(f)
        self.after(30, self._tick)

    # ------------------------------------------------------------ stile
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", font=(F, 10), background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("Top.TFrame", background=DARK)
        s.configure("Logo.TLabel", background=DARK, foreground="white", font=(F, 17, "bold"))
        s.configure("Hint.TLabel", background=DARK, foreground="#9ca3af", font=(F, 9))
        s.configure("Primary.TButton", background=ACCENT, foreground="white", borderwidth=0, padding=(16, 8),
                    font=(F, 10, "bold"))
        s.map("Primary.TButton", background=[("active", "#1d4ed8")])
        s.configure("Dark.TButton", background="#1f2937", foreground="white", borderwidth=0, padding=(12, 8))
        s.map("Dark.TButton", background=[("active", "#374151"), ("disabled", "#1f2937")],
              foreground=[("disabled", "#4b5563")])
        s.configure("Seg.TButton", background="#1f2937", foreground="#d1d5db", borderwidth=0, padding=(14, 8))
        s.configure("SegOn.TButton", background="white", foreground=DARK, borderwidth=0, padding=(14, 8),
                    font=(F, 10, "bold"))
        s.map("Seg.TButton", background=[("active", "#374151")])
        s.map("SegOn.TButton", background=[("active", "white")])
        s.configure("Light.TButton", background="#eef2ff", foreground=ACCENT, borderwidth=0, padding=(10, 6))
        s.map("Light.TButton", background=[("active", "#e0e7ff"), ("disabled", "#f3f4f6")],
              foreground=[("disabled", "#9ca3af")])
        s.configure("Name.TLabel", background=CARD, font=(F, 15, "bold"), foreground=DARK)
        s.configure("Sub.TLabel", background=CARD, foreground=MUTED)
        s.configure("Key.TLabel", background=CARD, foreground=MUTED, font=(F, 10))
        s.configure("Val.TLabel", background=CARD, foreground=DARK, font=(F, 12))
        s.configure("Place.TLabel", background=CARD, foreground=ACCENT, font=(F, 12, "bold"))
        s.configure("Sec.TLabel", background=CARD, foreground=DARK, font=(F, 11, "bold"))
        s.configure("Treeview", rowheight=26, background=CARD, fieldbackground=CARD, borderwidth=0)
        s.configure("Treeview.Heading", font=(F, 10, "bold"), background="#e5e7eb", relief="flat")
        s.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "black")])
        s.map("TCombobox", fieldbackground=[("readonly", "white")], foreground=[("readonly", "black")])
        s.configure("Horizontal.TScale", background=CARD)

    # ------------------------------------------------------------ layout
    def _build(self):
        top = ttk.Frame(self, style="Top.TFrame", padding=(16, 10))
        top.pack(fill="x")
        ttk.Label(top, text="Metadat", style="Logo.TLabel").pack(side="left")
        ttk.Button(top, text="Apri…", style="Primary.TButton", command=self.open_dialog).pack(side="left",
                                                                                           padx=(18, 6))
        ttk.Button(top, text="Incolla", style="Dark.TButton", command=self.paste).pack(side="left")
        ttk.Label(top, text="   trascina, Ctrl+V per incollare foto/video copiati", style="Hint.TLabel").pack(
            side="left")

        self.btn_export = ttk.Button(top, text="Salva report", style="Dark.TButton", command=self.export,
                                     state="disabled")
        self.btn_export.pack(side="right", padx=(6, 0))
        self.btn_copy = ttk.Button(top, text="Copia info", style="Dark.TButton", command=self.copy_all,
                                   state="disabled")
        self.btn_copy.pack(side="right", padx=(6, 18))
        seg = ttk.Frame(top, style="Top.TFrame")
        seg.pack(side="right")
        self.seg_normal = ttk.Button(seg, text="Normale", style="SegOn.TButton", command=lambda: self.set_mode(False))
        self.seg_normal.pack(side="left")
        self.seg_detail = ttk.Button(seg, text="Dettagliata", style="Seg.TButton", command=lambda: self.set_mode(True))
        self.seg_detail.pack(side="left")

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=5, uniform="c")
        body.columnconfigure(1, weight=6, uniform="c")
        body.rowconfigure(0, weight=1)

        # ---- sinistra: media
        left = ttk.Frame(body, style="Card.TFrame", padding=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.left = left
        self.files_bar = ttk.Frame(left, style="Card.TFrame")
        self.files_bar.pack(fill="x")
        self.files_combo = ttk.Combobox(self.files_bar, state="readonly")
        self.files_combo.pack(fill="x")
        self.files_combo.bind("<<ComboboxSelected>>", self._on_pick)
        self.files_bar.pack_forget()

        # contenitore a dimensione fissa: la sua misura NON dipende dall'immagine (evita loop di resize)
        self.media_frame = tk.Frame(left, bg="#eef0f3")
        self.media_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.media_frame.pack_propagate(False)
        self.media = tk.Label(self.media_frame, bg="#eef0f3", fg=MUTED, font=(F, 13), cursor="hand2",
                              text="Trascina qui foto, video o documenti\n\noppure clicca per aprire\no premi Ctrl+V")
        self.media.pack(fill="both", expand=True)
        self.media.bind("<Button-1>", lambda e: self._toggle_play() if self.player else self.open_dialog())
        self.media_frame.bind("<Configure>", self._on_media_configure)
        self._box = (0, 0)
        self._resize_job = None
        self._src_img: Image.Image | None = None

        # controlli video
        self.vctrl = ttk.Frame(left, style="Card.TFrame")
        self.btn_play = ttk.Button(self.vctrl, text="▶  Play", style="Primary.TButton", command=self._toggle_play)
        self.btn_play.pack(side="left")
        self.pos_var = tk.DoubleVar()
        self.scale = ttk.Scale(self.vctrl, from_=0, to=1, variable=self.pos_var, command=self._on_scale)
        self.scale.pack(side="left", fill="x", expand=True, padx=10)
        self.scale.bind("<ButtonPress-1>", lambda e: setattr(self, "_dragging", True))
        self.scale.bind("<ButtonRelease-1>", self._scale_release)
        self.time_lbl = ttk.Label(self.vctrl, text="0:00 / 0:00", style="Sub.TLabel")
        self.time_lbl.pack(side="left")

        # pannello Live velocità
        self.live = ttk.Frame(left, style="Card.TFrame")
        ttk.Label(self.live, text="LIVE  velocità dal GPS del video", style="Sec.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(10, 2))
        self.gauge = Speedometer(self.live)
        self.gauge.grid(row=1, column=0, rowspan=4, sticky="w")
        self.live_vals = {}
        for i, k in enumerate(("Velocità max", "Velocità media", "Distanza", "Altitudine ora")):
            fr = ttk.Frame(self.live, style="Card.TFrame")
            fr.grid(row=1 + i, column=1, sticky="w", padx=(16, 0))
            ttk.Label(fr, text=k, style="Key.TLabel", width=14).pack(side="left")
            lb = ttk.Label(fr, text="-", style="Val.TLabel")
            lb.pack(side="left")
            self.live_vals[k] = lb
        self.live_src = ttk.Label(self.live, text="", style="Sub.TLabel")
        self.live_src.grid(row=5, column=0, columnspan=2, sticky="w")

        self.nolive = ttk.Label(left, style="Sub.TLabel", wraplength=520, justify="left",
                                text="Velocità: questo video non contiene una traccia GPS nel tempo, quindi i km/h "
                                     "non si possono leggere. iPhone e Android salvano solo il punto di partenza. "
                                     "Le registrano GoPro, droni DJI (file .SRT) e dashcam; puoi anche mettere un "
                                     "file .GPX con lo stesso nome accanto al video.")

        # ---- destra: info + mappa
        right = ttk.Frame(body, style="Card.TFrame", padding=(16, 12))
        right.grid(row=0, column=1, sticky="nsew")
        self.name_lbl = ttk.Label(right, text="Nessun file", style="Name.TLabel")
        self.name_lbl.pack(anchor="w")
        self.kind_lbl = ttk.Label(right, text="Apri o trascina un file per vedere tutte le sue informazioni",
                                  style="Sub.TLabel")
        self.kind_lbl.pack(anchor="w", pady=(0, 6))
        self.warn = tk.Label(right, bg=WARN_BG, fg=WARN_FG, font=(F, 10), justify="left", anchor="w", padx=10,
                             pady=8, wraplength=640)

        self.info_holder = ttk.Frame(right, style="Card.TFrame")
        self.info_holder.pack(fill="both", expand=True)

        # vista normale
        self.normal = ttk.Frame(self.info_holder, style="Card.TFrame")
        self.normal.pack(fill="both", expand=True)

        # vista dettagliata
        self.detail = ttk.Frame(self.info_holder, style="Card.TFrame")
        bar = ttk.Frame(self.detail, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Cerca", style="Key.TLabel").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._fill_tree())
        ttk.Entry(bar, textvariable=self.search, width=30).pack(side="left", padx=6)
        ttk.Label(bar, text="doppio clic = copia valore", style="Sub.TLabel").pack(side="right")
        tf = ttk.Frame(self.detail, style="Card.TFrame")
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("v",), selectmode="browse")
        self.tree.heading("#0", text="Campo", anchor="w")
        self.tree.heading("v", text="Valore", anchor="w")
        self.tree.column("#0", width=240, stretch=False)
        self.tree.column("v", width=420)
        self.tree.tag_configure("sec", font=(F, 10, "bold"), background="#f3f4f6")
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._copy_tree_value)

        # mappa
        self.map_box = ttk.Frame(right, style="Card.TFrame")
        self.map_box.pack(fill="both", expand=True, pady=(10, 0))
        mb = ttk.Frame(self.map_box, style="Card.TFrame")
        mb.pack(fill="x", pady=(0, 6))
        self.btn_g = ttk.Button(mb, text="Google Maps", style="Light.TButton", command=lambda: self._open_map("g"))
        self.btn_g.pack(side="left")
        self.btn_o = ttk.Button(mb, text="OpenStreetMap", style="Light.TButton", command=lambda: self._open_map("o"))
        self.btn_o.pack(side="left", padx=6)
        self.btn_c = ttk.Button(mb, text="Copia coordinate", style="Light.TButton", command=self._copy_coords)
        self.btn_c.pack(side="left")
        self.map_type = tk.StringVar(value="Satellite")
        cb = ttk.Combobox(mb, textvariable=self.map_type, values=("Stradale", "Satellite"), width=9,
                          state="readonly")
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self._set_tiles())
        if tkintermapview:
            self.map = tkintermapview.TkinterMapView(self.map_box, corner_radius=0, height=300)
            self.map.pack(fill="both", expand=True)
            self.map.set_position(42.5, 12.5)
            self.map.set_zoom(5)
            self._set_tiles()
        else:
            self.map = None
        self.map_box.pack_forget()

        self.status = tk.Label(self, text="Pronto", bg=BG, fg=MUTED, anchor="w", padx=14, font=(F, 9))
        self.status.pack(fill="x", pady=(0, 6))

    # ------------------------------------------------------------ modalità
    def set_mode(self, detailed: bool):
        self.detailed.set(detailed)
        self.seg_normal.configure(style="Seg.TButton" if detailed else "SegOn.TButton")
        self.seg_detail.configure(style="SegOn.TButton" if detailed else "Seg.TButton")
        if detailed:
            self.normal.pack_forget()
            self.detail.pack(fill="both", expand=True)
            self._fill_tree()
        else:
            self.detail.pack_forget()
            self.normal.pack(fill="both", expand=True)

    # ------------------------------------------------------------ apertura file
    def open_dialog(self):
        paths = filedialog.askopenfilenames(title="Scegli file", filetypes=[
            ("Foto, video e documenti", "*.jpg *.jpeg *.png *.heic *.heif *.tif *.tiff *.webp *.dng *.gif *.bmp "
                                        "*.mp4 *.mov *.m4v *.3gp *.avi *.mkv *.mts *.webm *.insv "
                                        "*.pdf *.docx *.xlsx *.pptx *.odt *.ods *.odp"),
            ("Tutti i file", "*.*")])
        for p in paths:
            self.load(p)

    def _on_drop(self, event):
        for p in self.tk.splitlist(event.data):
            self._load_any(p)

    def _load_any(self, p: str):
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                fp = os.path.join(p, name)
                if os.path.isfile(fp):
                    self.load(fp)
        else:
            self.load(p)

    def paste(self, _e=None):
        """Ctrl+V: file copiati da Esplora file, immagine negli appunti o percorso di testo."""
        try:
            data = ImageGrab.grabclipboard()
        except Exception:
            data = None
        if isinstance(data, list) and data:
            for p in data:
                self._load_any(p)
            return "break"
        if isinstance(data, Image.Image):
            name = f"Immagine incollata {datetime.now():%H.%M.%S}.png"
            out = os.path.join(tempfile.gettempdir(), name)
            data.save(out)
            self.load(out)
            if self.current:
                self.current.warning = ("Immagine incollata dagli appunti: Windows copia solo i pixel, senza "
                                        "metadati. Per vedere dispositivo e posizione copia il FILE da Esplora file "
                                        "(clic destro > Copia) e poi Ctrl+V qui.")
                self.show(self.current)
            return "break"
        try:
            txt = self.clipboard_get().strip().strip('"')
        except Exception:
            txt = ""
        if txt and os.path.exists(txt):
            self._load_any(txt)
            return "break"
        if _e is None or not isinstance(self.focus_get(), (tk.Entry, ttk.Entry)):
            self.status.configure(text="Negli appunti non c'è una foto, un video o un file")
        return None

    def _ctrl_c(self, _e):
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry)):
            return None
        if self.detailed.get() and self.tree.selection():
            self._copy_tree_value()
        else:
            self.copy_all()
        return "break"

    def load(self, path: str):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            return
        self.status.configure(text=f"Analisi di {os.path.basename(path)}…")
        self.update_idletasks()
        res = extractor.extract(path)
        self.results[path] = res
        names = [os.path.basename(p) for p in self.results]
        self.files_combo.configure(values=names)
        self.files_combo.current(list(self.results).index(path))
        if len(self.results) > 1:
            self.files_bar.pack(fill="x", before=self.media_frame)
        self.show(res)

    def _on_pick(self, _e):
        path = list(self.results)[self.files_combo.current()]
        self.show(self.results[path])

    # ------------------------------------------------------------ visualizzazione
    def show(self, res: extractor.Result):
        self.current = res
        for b in (self.btn_export, self.btn_copy):
            b.state(["!disabled"])
        self.name_lbl.configure(text=os.path.basename(res.path))
        size = dict(res.sections.get("File", [])).get("Dimensione", "").split(" (")[0]
        self.kind_lbl.configure(text=f"{res.kind[:1].upper() + res.kind[1:]}  ·  {size}")
        if res.warning:
            self.warn.configure(text="⚠  " + res.warning)
            self.warn.pack(fill="x", pady=(0, 8), before=self.info_holder)
        else:
            self.warn.pack_forget()
        self._show_media(res)
        self._fill_normal()
        self._fill_tree()
        self._show_location(res)
        n = sum(len(v) for v in res.sections.values())
        self.status.configure(text=f"{n} informazioni trovate"
                                   + (" · posizione GPS" if res.gps else "")
                                   + (f" · velocità GPS ({res.telemetry_src})" if res.telemetry else ""))

    def _fill_normal(self):
        for w in self.normal.winfo_children():
            w.destroy()
        res = self.current
        summ = dict(res.summary)
        if res.address:
            summ["Luogo"] = res.address
        if res.kind == "immagine" and not summ.get("Dispositivo"):
            summ.pop("Posizione", None)
        if res.kind == "immagine":
            shot = summ.get("Scatto")
            if shot:
                summ["Obiettivo"] = (summ.get("Obiettivo", "") + "\n" + shot).strip()
        if res.kind == "video" and summ.get("Qualità"):
            summ["Durata"] = f"{summ.get('Durata', '')}  ·  {summ['Qualità']}"
        if res.gps and summ.get("Coordinate"):
            summ.pop("Posizione", None)
        if res.gps and not res.address:
            summ["Luogo"] = "ricerca indirizzo…" if res.address is None else "indirizzo non disponibile (offline)"
        row = 0
        shown = set()
        for key, label in NORMAL_KEYS:
            if key in summ and label not in shown:
                shown.add(label)
                v = summ[key]
                ttk.Label(self.normal, text=label, style="Key.TLabel").grid(row=row, column=0, sticky="nw",
                                                                         pady=5, padx=(0, 16))
                lb = ttk.Label(self.normal, text=v, style="Place.TLabel" if key == "Luogo" else "Val.TLabel",
                               wraplength=520, justify="left", cursor="hand2")
                lb.grid(row=row, column=1, sticky="w", pady=5)
                lb.bind("<Button-1>", lambda e, v=v: self._clip(v))
                row += 1
        if row == 0:
            ttk.Label(self.normal, text="Solo informazioni di base sul file: passa a Dettagliata per vederle.",
                      style="Sub.TLabel").grid(row=0, column=0, sticky="w")
        if res.errors:
            ttk.Label(self.normal, text="Errore: " + "; ".join(res.errors), foreground="#b91c1c",
                      background=CARD).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        self.normal.columnconfigure(1, weight=1)

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        res = self.current
        if not res:
            return
        q = self.search.get().strip().lower()
        sections = [("Riepilogo", ([("Luogo", res.address)] if res.address else []) + list(res.summary))]
        order = ["Posizione GPS", "Telemetria GPS", "File", "Immagine", "Video", "Audio", "Proprietà documento",
                 "Applicazione", "PDF"]
        for sec in sorted(res.sections, key=lambda s: order.index(s) if s in order else len(order)):
            sections.append((sec, res.sections[sec]))
        for sec, rows in sections:
            rows = [(k, v) for k, v in rows if not q or q in k.lower() or q in str(v).lower()]
            if not rows:
                continue
            node = self.tree.insert("", "end", text=f"{sec}  ({len(rows)})", open=True, tags=("sec",))
            for k, v in rows:
                self.tree.insert(node, "end", text=k, values=(v,))
        self.tree.yview_moveto(0)

    def _media_box(self):
        w = max(self.media_frame.winfo_width(), 320) - 8
        h = max(self.media_frame.winfo_height(), 240) - 8
        return w, h

    def _on_media_configure(self, e):
        size = (e.width, e.height)
        if abs(size[0] - self._box[0]) < 8 and abs(size[1] - self._box[1]) < 8:
            return
        self._box = size
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(200, self._media_resized)

    def _media_resized(self):
        self._resize_job = None
        if self._src_img is not None and not self.player:
            self._render_image()
        elif self.player:
            self.player.size = self._media_box()
            if not self.player.playing:
                self.player.seek(self.player.t)

    def _render_image(self):
        im = self._src_img.copy()
        im.thumbnail(self._media_box())
        self._img = ImageTk.PhotoImage(im)
        self.media.configure(image=self._img, text="")

    def _show_image(self, path):
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((1600, 1600))
                self._src_img = im.convert("RGB")
            self._render_image()
        except Exception:
            self._src_img = None
            self.media.configure(image="", text="Anteprima non disponibile")

    def _show_media(self, res):
        if self.player:
            self.player.close()
            self.player = None
        self.vctrl.pack_forget()
        self.live.pack_forget()
        self.nolive.pack_forget()
        self._src_img = None
        self.media.configure(cursor="hand2")
        if res.kind == "immagine":
            self._show_image(res.path)
        elif res.kind == "video":
            try:
                self.player = VideoPlayer(res.path, self._media_box())
            except Exception as e:
                self.media.configure(image="", text=f"Video non riproducibile\n{e}")
                return
            self.btn_play.configure(text="▶  Play")
            self.scale.configure(to=max(self.player.duration, 0.1))
            self.pos_var.set(0)
            self.vctrl.pack(fill="x", pady=(8, 0))
            if res.telemetry:
                st = video.stats(res.telemetry)
                self.gauge.set_max(st["max_kmh"])
                self.gauge.draw(res.telemetry[0].kmh)
                self.live_vals["Velocità max"].configure(text=f"{st['max_kmh']:.0f} km/h")
                self.live_vals["Velocità media"].configure(text=f"{st['media_kmh']:.0f} km/h")
                self.live_vals["Distanza"].configure(
                    text=f"{st['distanza_km']:.2f} km" if st["distanza_km"] >= 1 else f"{st['distanza_km'] * 1000:.0f} m")
                self.live_src.configure(text=f"Fonte: {res.telemetry_src} · {st['punti']} punti GPS")
                self.live.pack(fill="x")
            else:
                self.nolive.pack(fill="x", pady=(10, 0))
        else:
            self._img = None
            ext = os.path.splitext(res.path)[1].upper().lstrip(".") or "FILE"
            self.media.configure(image="", text=ext, font=(F, 40, "bold"), fg=ACCENT, cursor="arrow")
            return
        self.media.configure(font=(F, 13), fg=MUTED)

    # ------------------------------------------------------------ video loop
    def _toggle_play(self):
        if self.player:
            self.player.toggle()
            self.btn_play.configure(text="❚❚  Pausa" if self.player.playing else "▶  Play")

    def _on_scale(self, _v):
        if self._dragging and self.player:
            self.player.seek(self.pos_var.get())

    def _scale_release(self, _e):
        self._dragging = False
        if self.player:
            self.player.seek(self.pos_var.get())

    def _tick(self):
        while not self._ui_q.empty():
            try:
                self._ui_q.get_nowait()()
            except Exception:
                pass
        p = self.player
        if p:
            fr, t = p.get()
            if fr is not None and fr is not getattr(self, "_last_fr", None):
                self._last_fr = fr
                self._img = ImageTk.PhotoImage(fr)
                self.media.configure(image=self._img, text="")
            if not self._dragging:
                self.pos_var.set(t)
            self.time_lbl.configure(text=f"{video._fmt_dur(t)} / {video._fmt_dur(p.duration)}")
            if not p.playing and self.btn_play.cget("text").startswith("❚"):
                self.btn_play.configure(text="▶  Play")
            res = self.current
            if res and res.telemetry:
                s = video.speed_at(res.telemetry, t)
                if s:
                    self.gauge.draw(s.kmh)
                    self.live_vals["Altitudine ora"].configure(text=f"{s.alt:.0f} m" if s.alt is not None else "-")
                    if self.map and self._live_marker and (time.time() - getattr(self, "_mt", 0) > 0.25):
                        self._mt = time.time()
                        self._live_marker.set_position(s.lat, s.lon)
        self.after(30, self._tick)

    # ------------------------------------------------------------ mappa
    def _show_location(self, res):
        has = res.gps is not None
        if not has:
            self.map_box.pack_forget()
            return
        self.map_box.pack(fill="both", expand=True, pady=(10, 0))
        if not self.map:
            return
        # la mappa deve avere la sua misura reale prima di centrare
        self.update_idletasks()
        self.after(80, lambda r=res: r is self.current and self._place_on_map(r))
        if res.address is None:
            threading.Thread(target=self._geocode, args=(res,), daemon=True).start()

    def _place_on_map(self, res):
        for attr in ("_marker", "_live_marker", "_path"):
            obj = getattr(self, attr)
            if obj:
                obj.delete()
            setattr(self, attr, None)
        lat, lon = res.gps
        if res.telemetry:
            pts = [(p.lat, p.lon) for p in res.telemetry]
            step = max(1, len(pts) // 800)
            pts = pts[::step]
            if len(pts) >= 2:
                self._path = self.map.set_path(pts, color=ACCENT, width=4)
            lats, lons = [p[0] for p in pts], [p[1] for p in pts]
            if max(lats) - min(lats) > 1e-4 or max(lons) - min(lons) > 1e-4:
                self.map.fit_bounding_box((max(lats) + 1e-4, min(lons) - 1e-4), (min(lats) - 1e-4, max(lons) + 1e-4))
            else:
                self.map.set_position(lat, lon)
                self.map.set_zoom(18)
            self._live_marker = self.map.set_marker(lat, lon, text="Live")
        else:
            self.map.set_position(lat, lon)
            self.map.set_zoom(17)
            self._marker = self.map.set_marker(lat, lon)

    def _geocode(self, res):
        try:
            addr = extractor.reverse_geocode(*res.gps)
        except Exception:
            addr = None
        res.address = addr or ""
        if addr:
            res.sections.setdefault("Posizione GPS", []).insert(0, ("Indirizzo (OpenStreetMap)", addr))
        self._ui_q.put(lambda: res is self.current and (self._fill_normal(), self._fill_tree()))

    def _set_tiles(self):
        if not self.map:
            return
        if self.map_type.get() == "Satellite":
            self.map.set_tile_server(
                "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                max_zoom=19)
        else:
            self.map.set_tile_server("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")

    def _open_map(self, kind):
        lat, lon = self.current.gps
        webbrowser.open(f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}" if kind == "g" else
                        f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=17/{lat:.6f}/{lon:.6f}")

    # ------------------------------------------------------------ copia / esporta
    def _clip(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.configure(text=f"Copiato: {text[:80]}")

    def _copy_coords(self):
        lat, lon = self.current.gps
        self._clip(f"{lat:.6f}, {lon:.6f}")

    def _copy_tree_value(self, _e=None):
        sel = self.tree.selection()
        if sel:
            vals = self.tree.item(sel[0], "values")
            if vals:
                self._clip(vals[-1])

    def _as_text(self, res) -> str:
        lines = [f"METADAT - {res.path}", ""]
        if res.warning:
            lines += [f"ATTENZIONE: {res.warning}", ""]
        if res.address:
            lines.append(f"Luogo: {res.address}")
        lines += [f"{k}: {v}" for k, v in res.summary] + [""]
        for sec, rows in res.sections.items():
            lines.append(f"== {sec} ==")
            lines += [f"  {k}: {v}" for k, v in rows]
            lines.append("")
        return "\n".join(lines)

    def copy_all(self):
        if self.current:
            self._clip(self._as_text(self.current))
            self.status.configure(text="Tutte le informazioni copiate negli appunti")

    def export(self):
        res = self.current
        if not res:
            return
        base = os.path.splitext(os.path.basename(res.path))[0]
        out = filedialog.asksaveasfilename(title="Salva report", initialfile=f"{base}_metadati",
                                           defaultextension=".txt",
                                           filetypes=[("Testo", "*.txt"), ("JSON", "*.json")])
        if not out:
            return
        with open(out, "w", encoding="utf-8") as fh:
            if out.lower().endswith(".json"):
                d = res.to_dict()
                if res.telemetry:
                    d["telemetria"] = [vars(p) for p in res.telemetry]
                json.dump(d, fh, ensure_ascii=False, indent=2)
            else:
                fh.write(self._as_text(res))
        self.status.configure(text=f"Report salvato: {out}")

    def _quit(self):
        if self.player:
            self.player.close()
        self.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(sys.argv[1:]).mainloop()


if __name__ == "__main__":
    main()
