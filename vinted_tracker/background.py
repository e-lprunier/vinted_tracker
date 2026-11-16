"""Scan en arrière-plan pour l'app Streamlit (un seul scan à la fois, partagé entre visiteurs)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .config import Config
from .pipeline import run_scan
from .storage import save_scan


@dataclass
class ScanState:
    running: bool = False
    message: str = ""
    fraction: float = 0.0
    last_attempt: float | None = None
    last_error: str | None = None
    last_source: str | None = None


class ScanManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.state = ScanState()

    def _progress(self, msg: str, frac: float):
        self.state.message, self.state.fraction = msg, max(0.0, min(1.0, frac))

    def start(self, cfg: Config, source: str = "auto", search_names: list[str] | None = None) -> bool:
        with self._lock:
            if self.state.running:
                return False
            self.state = ScanState(running=True, message="Connexion à Vinted…", last_attempt=time.time())

        def job():
            try:
                df, used = run_scan(cfg, source, search_names, progress=self._progress)
                save_scan(df, used)
                self.state.last_source, self.state.last_error = used, None
            except Exception as e:
                self.state.last_error = str(e)
            finally:
                self.state.running = False

        threading.Thread(target=job, daemon=True, name="vinted-scan").start()
        return True

    def should_auto_start(self, data_age_min: float | None, every_min: int) -> bool:
        if every_min <= 0 or self.state.running:
            return False
        if data_age_min is not None and data_age_min < every_min:
            return False
        # pas plus d'une tentative automatique par intervalle (évite de marteler Vinted si bloqué)
        return self.state.last_attempt is None or (time.time() - self.state.last_attempt) / 60 >= every_min
