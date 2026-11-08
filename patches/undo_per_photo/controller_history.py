"""The history part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes HistoryMixin as a base.
"""
from __future__ import annotations

import os

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
        if self._history_timer is None:
            self._history_timer = QTimer()
            self._history_timer.setSingleShot(True)
            self._history_timer.timeout.connect(self._history_push_now)
        # Debounce: rapid slider/spinbox streams collapse into one snapshot.
        self._history_timer.start(700)

    def _use_history_of_current(self) -> None:
        """Make `_history` the open image's own stack.

        One stack for everything recorded switching images as an edit: the
        incoming image's look lands in the panels like any change, so Ctrl+Z
        right after a switch put the PREVIOUS image's look on the new one.
        Keyed by the image, the switch is not a step at all, and coming back
        to an image brings its own undo and redo back. Checked lazily, at
        every push, undo and redo, so no load path has to remember it.
        """
        path = self._current_input_path
        key = os.path.normcase(os.path.abspath(path)) if path else None
        if key == self._history_owner:
            return
        if self._history_stacks is None:
            self._history_stacks = {}
        if self._history:
            # Re-inserted last: the dict's order is newest-last.
            self._history_stacks[self._history_owner] = (self._history, self._history_index)
            while len(self._history_stacks) > self._HISTORY_IMAGES:
                del self._history_stacks[next(iter(self._history_stacks))]
        self._history, self._history_index = self._history_stacks.pop(key, ([], -1))
        self._history_owner = key

    def _history_push_now(self) -> None:
        self._use_history_of_current()
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
        self._use_history_of_current()
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
        self._use_history_of_current()
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
