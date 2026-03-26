"""
core/session.py — Session-wide shared state.

:class:`SessionState` is the single source of truth for all mutable runtime
data (queue, measurement counter, running flag, current runner …).  It is
created once in ``gui/app.py`` and injected into every tab that needs it.

This means tabs never import each other — they communicate exclusively through
the shared ``SessionState`` object.
"""

import itertools
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import matplotlib.pyplot as plt

from .method_registry import MethodRegistry
from .opentrons_registry import OpentronsRegistry
from .pump_step_utils import default_collection_warn_ml
from .runner import SerialMeasurementRunner
from config import COLLECTION_SYRINGE_CAPACITY_ML


class SessionState:
    """Holds all mutable state for one application session.

    Parameters
    ----------
    log_callback:
        Callable ``(str) → None`` wired to the GUI log panel.
    status_callback:
        Callable ``(str) → None`` wired to the GUI status bar.
    """

    def __init__(
        self,
        log_callback:    Callable[[str], None] = print,
        status_callback: Callable[[str], None] = print,
    ):
        self._log    = log_callback
        self._status = status_callback
        # NEW — wired by app.py after construction
        self.session_manager = None
        # ── Queue ─────────────────────────────────────────────────────────────
        self.measurement_queue: List[dict] = []
        self.is_running  = False
        self.current_runner: Optional[SerialMeasurementRunner] = None
        self.current_stop_callback: Optional[Callable[[], None]] = None

        # ── Queue status (for external status polling) ────────────────────────
        self._queue_status_lock = threading.Lock()
        self._queue_status: Dict[str, Optional[str]] = {
            "state": "idle",
            "current_index": None,
            "total": None,
            "current_label": None,
            "started_at": None,
            "updated_at": None,
        }

        # ── Measurement tagging ───────────────────────────────────────────────
        self.measurement_counter = 0

        # ── Script registry (deduplication) ───────────────────────────────────
        self.registry = MethodRegistry(log_callback=log_callback)
        self.opentrons_registry = OpentronsRegistry(log_callback=log_callback)

        # ── Queue clipboard (copy / paste) ────────────────────────────────────
        self.queue_clipboard: List[dict] = []

        # ── Live plot helpers ─────────────────────────────────────────────────
        _colors = (
            plt.rcParams.get("axes.prop_cycle", plt.cycler(color=["#1f77b4"]))
            .by_key()
            .get("color", ["#1f77b4"])
        )
        self._plot_color_cycle = itertools.cycle(_colors)
        self.last_live_plot_color: Optional[str]  = None
        self.last_live_plot_label: Optional[str]  = None

        # —— Execution options ———————————————————————————————————————————————————
        self.save_raw_packets: bool = False
        self.simulate_measurements: bool = False
        self.step_delay: float = 1.0
        self.device_port: Optional[str] = None

        # Syringe collection tracking (queue-run scoped)
        self.collection_steps: int = 0
        self.collection_volume_ul: float = 0.0
        self.collection_capacity_ul: float = COLLECTION_SYRINGE_CAPACITY_ML * 1000.0
        self.collection_warn_ul: float = default_collection_warn_ml(COLLECTION_SYRINGE_CAPACITY_ML) * 1000.0
        self.collection_warned: bool = False

    # ── Measurement tag ───────────────────────────────────────────────────────

    def next_meas_tag(self) -> str:
        """Increment counter and return the next sequential measurement tag.

        Format: ``meas_NNN`` (grows beyond 999 automatically).
        """
        self.measurement_counter += 1
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        return f"meas_{ts}_{self.measurement_counter:03d}"

    def next_meas_tag_with_mux(self, mux_channel: Optional[int] = None) -> str:
        """Return a timestamped measurement tag with optional MUX channel suffix."""
        tag = self.next_meas_tag()
        if mux_channel in (None, 0, "0", ""):
            return tag
        return f"{tag}_ch{int(mux_channel)}"

    def reset_counter(self):
        """Reset measurement counter to zero."""
        self.measurement_counter = 0
        self._log("[Session] Measurement counter reset to 0.")

    def reset_collection_tracking(
        self,
        *,
        capacity_ul: Optional[float] = None,
        warn_ul: Optional[float] = None,
    ) -> None:
        self.collection_steps = 0
        self.collection_volume_ul = 0.0
        self.collection_capacity_ul = (
            float(capacity_ul) if capacity_ul is not None else COLLECTION_SYRINGE_CAPACITY_ML * 1000.0
        )
        self.collection_warn_ul = (
            float(warn_ul)
            if warn_ul is not None
            else default_collection_warn_ml(self.collection_capacity_ul / 1000.0) * 1000.0
        )
        self.collection_warned = False

    def add_collection_volume(
        self,
        *,
        volume_ul: float,
        capacity_ul: Optional[float] = None,
        warn_ul: Optional[float] = None,
    ) -> None:
        self.collection_steps += 1
        self.collection_volume_ul += max(0.0, float(volume_ul))
        if capacity_ul is not None and capacity_ul > 0:
            self.collection_capacity_ul = float(capacity_ul)
        if warn_ul is not None and warn_ul > 0:
            self.collection_warn_ul = float(warn_ul)

    # ── Plot colour ───────────────────────────────────────────────────────────

    def next_plot_color(self) -> str:
        """Return the next colour from the matplotlib colour cycle."""
        color = next(self._plot_color_cycle)
        self.last_live_plot_color = color
        return color

    # ── Convenience passthrough ───────────────────────────────────────────────

    def log(self, msg: str):
        self._log(msg)

    def set_status(self, msg: str):
        self._status(msg)

    def require_session(self):
        """Return session path if available, otherwise None.

        Delegates to SessionManager when wired by app.py.
        """
        if self.session_manager is None:
            return None
        return self.session_manager.require_session()

    def stop_current_runner(self):
        """Signal the active runner (if any) to stop."""
        if self.current_runner is not None:
            self.current_runner.stop()
        if self.current_stop_callback is not None:
            try:
                self.current_stop_callback()
            except Exception:
                pass

    def update_queue_status(self, **updates):
        """Update queue status fields in a threadsafe way."""
        with self._queue_status_lock:
            self._queue_status.update(updates)
            self._queue_status["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def get_queue_status(self) -> Dict[str, Optional[str]]:
        """Return a copy of the current queue status."""
        with self._queue_status_lock:
            return dict(self._queue_status)
