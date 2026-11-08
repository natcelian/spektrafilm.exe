"""The history part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes HistoryMixin as a base.
"""
from __future__ import annotations

from qtpy import QtCore

from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state_bridge import apply_gui_state
from spektrafilm_gui.state_bridge import collect_gui_state
from spektrafilm_gui.persistence import gui_state_to_dict

QTimer = getattr(QtCore, 'QTimer')


class HistoryMixin:
    """GuiController's history methods."""

    def history_capture_baseline(self) -> None:
        """Record the startup state so the first undo has somewhere to go."""
        self._history_push_now()

    def _schedule_history_push(self) -> None:
        if self._history_applying:
            return
        # The crop session is ONE step, recorded at Done (controller_crop).
        if self.crop_session_active():
            return
        if self._history_timer is None:
            self._history_timer = QTimer()
            self._history_timer.setSingleShot(True)
            self._history_timer.timeout.connect(self._history_push_now)
        # Debounce: rapid slider/spinbox streams collapse into one snapshot.
        self._history_timer.start(700)

    def history_flush(self) -> None:
        """Record the look now, pending edit or not -- the crop session lands
        as one step on top of what was there before it (controller_crop)."""
        if self._history_timer is not None:
            self._history_timer.stop()
        self._history_push_now()

    def _history_push_now(self) -> None:
        snapshot = gui_state_to_dict(collect_gui_state(widgets=self._widgets))
        if self._history is None:
            self._history = []
        if (self._history and 0 <= self._history_index < len(self._history)
                and snapshot == self._history[self._history_index]):
            return
        del self._history[self._history_index + 1:]
        self._history.append(snapshot)
        if len(self._history) > self._HISTORY_LIMIT:
            del self._history[0]
        self._history_index = len(self._history) - 1

    def undo(self) -> None:
        if self.crop_session_active():         # in the crop session, undo = Cancel
            self.cancel_crop_session()
            return
        # Flush a pending debounce first so the current edit is undoable.
        if self._history_timer is not None and self._history_timer.isActive():
            self._history_timer.stop()
            self._history_push_now()
        if not self._history or self._history_index <= 0:
            set_status(self._viewer, 'Nothing to undo')
            return
        self._history_index -= 1
        self._history_apply(f'Undo ({self._history_index + 1}/{len(self._history)})')

    def redo(self) -> None:
        if self.crop_session_active():
            set_status(self._viewer, 'Cropping')
            return
        if not self._history or self._history_index >= len(self._history) - 1:
            set_status(self._viewer, 'Nothing to redo')
            return
        self._history_index += 1
        self._history_apply(f'Redo ({self._history_index + 1}/{len(self._history)})')

    def _history_apply(self, message: str) -> None:
        from spektrafilm_gui.persistence import gui_state_from_dict

        self._history_applying = True
        try:
            apply_gui_state(gui_state_from_dict(self._history[self._history_index]),
                            widgets=self._widgets)
            self._note_film_stock_synced()
        finally:
            self._history_applying = False
        set_status(self._viewer, message)
        self._request_auto_preview_if_enabled()
