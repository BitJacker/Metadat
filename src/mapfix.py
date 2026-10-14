"""Corregge il consumo di CPU di tkintermapview (loop di thread senza pausa)."""

import threading
import time
import types

import requests

_local = threading.local()


def _session() -> requests.Session:
    # una connessione HTTPS riutilizzata per thread: niente handshake + certificati a ogni riquadro
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
        s.headers["User-Agent"] = "Metadat/1.0 (desktop metadata viewer)"
    return s


def patch(tkintermapview) -> None:
    cls = tkintermapview.TkinterMapView
    shim = types.SimpleNamespace(**{k: getattr(requests, k) for k in dir(requests) if not k.startswith("__")})

    def get(url, *a, **kw):
        kw.pop("headers", None)  # usa lo User-Agent della sessione
        kw.setdefault("timeout", 10)
        return _session().get(url, *a, **kw)

    shim.get = get
    tkintermapview.map_widget.requests = shim

    def load_images_background(self):
        fails: dict = {}
        while self.running:
            if not self.image_load_queue_tasks:
                time.sleep(0.1)
                continue
            try:
                task = self.image_load_queue_tasks.pop()
            except IndexError:
                continue
            zoom, x, y = task[0]
            if zoom != round(self.zoom):
                continue  # riquadro di uno zoom vecchio: inutile
            image = self.get_tile_image_from_cache(zoom, x, y)
            if image is False:
                image = self.request_image(zoom, x, y, db_cursor=None)
                if image is None:
                    n = fails.get(task[0], 0) + 1
                    fails[task[0]] = n
                    if n < 3:
                        time.sleep(0.5)
                        self.image_load_queue_tasks.insert(0, task)
                    continue
            self.image_load_queue_results.append((task[0], task[1], image))

    def pre_cache(self):
        # pre-caching disattivato: scaricava riquadri all'infinito
        while self.running:
            time.sleep(0.5)

    def update_canvas_tile_images(self):
        while self.image_load_queue_results and self.running:
            (zoom, x, y), canvas_tile, image = self.image_load_queue_results.pop(0)
            if zoom == round(self.zoom):
                canvas_tile.set_image(image)
        if self.running:
            self.after(40, self.update_canvas_tile_images)

    cls.load_images_background = load_images_background
    cls.pre_cache = pre_cache
    cls.update_canvas_tile_images = update_canvas_tile_images
