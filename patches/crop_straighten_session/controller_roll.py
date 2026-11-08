"""The roll part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes RollMixin as a base.
"""
from __future__ import annotations

import numpy as np
from qtpy import QtCore

from spektrafilm_gui.shell_layout import dialog_parent
from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state_bridge import collect_gui_state

QTimer = getattr(QtCore, 'QTimer')


class RollMixin:
    """GuiController's roll methods."""

    def roll_add_files(self, paths: list) -> None:
        was_empty = self._roll.current is None
        added = self._roll.add_files(list(paths))
        skipped = len(paths) - len(added)
        self._roll_refresh_view()
        self._roll_request_thumbnails(added)
        if was_empty and self._roll.current is not None:
            # First files of the roll: load frame 1. It has no stored look yet,
            # so it inherits whatever the controls currently show.
            self._roll_load_current(restore_state=False)
        message = f'Added {len(added)} file(s) to the roll'
        if skipped:
            message += f' ({skipped} skipped: duplicate or unsupported)'
        set_status(self._viewer, message)

    def roll_activate(self, index: int) -> None:
        if index == self._roll.current_index:
            return
        if self._active_simulation_worker is not None or self._active_export_worker is not None:
            set_status(self._viewer, 'Busy rendering; wait before switching frames')
            self._roll_refresh_view()
            return
        # a crop session belongs to its frame: leaving the frame applies it,
        # before the frame's look is stored
        self.apply_crop_session('Crop applied')
        # Phase 17D: leaving a frame with changes captures an auto attempt.
        self._capture_attempt_on_leave()
        # Snapshot the outgoing frame's look, switch, restore the incoming one.
        self._roll.store_state_for_current(collect_gui_state(widgets=self._widgets))
        if self._roll.select(index) is None:
            return
        self._roll_load_current(restore_state=True)
        self._roll_refresh_view()

    def roll_remove(self, index: int) -> None:
        previous = self._roll.current
        removed = self._roll.remove(index)
        if removed is None:
            return
        self._roll_refresh_view()
        if self._roll.current is None:
            set_status(self._viewer, f'Removed {removed.name}; roll is empty (loaded image kept)')
            return
        if previous is not None and removed.path == previous.path:
            self._roll_load_current(restore_state=True)
        set_status(self._viewer, f'Removed {removed.name} from the roll')

    def roll_remove_current(self) -> None:
        """Remove the CURRENT frame from the roll (Ctrl+Del). Same semantics
        as removing it from the film strip: the next frame (or the previous
        one at the end of the roll) becomes current; per-frame look dropped."""
        if not self._roll.files:
            set_status(self._viewer, 'The roll is empty')
            return
        if self._active_simulation_worker is not None or self._active_export_worker is not None:
            set_status(self._viewer, 'Busy rendering; wait before removing frames')
            return
        self.roll_remove(self._roll.current_index)

    def roll_clear(self) -> None:
        """Empty the whole roll (Clear button). The loaded image stays on the
        canvas; per-frame looks are dropped with their files."""
        if self._active_simulation_worker is not None or self._active_export_worker is not None:
            set_status(self._viewer, 'Busy rendering; wait before clearing the roll')
            return
        count = self._roll.clear()
        self._roll_refresh_view()
        if count:
            set_status(self._viewer, f'Cleared the roll ({count} file(s) removed; loaded image kept)')
        else:
            set_status(self._viewer, 'The roll is already empty')

    def roll_apply_look_to_all(self) -> None:
        from spektrafilm_gui.section_picker import pick_settings
        from spektrafilm_gui.state import PROJECT_DEFAULT_GUI_STATE

        if not self._roll.files:
            set_status(self._viewer, 'The roll is empty: nothing to apply to')
            return
        state = collect_gui_state(widgets=self._widgets)
        chosen = pick_settings(
            dialog_parent(self._viewer),
            title='Apply to all frames',
            meta=f'{len(self._roll.files)} frames',
            verb='Apply',
            state=state, default_state=PROJECT_DEFAULT_GUI_STATE)
        if chosen is None:
            return  # cancelled
        if not chosen:
            set_status(self._viewer, 'Nothing selected: no frames changed')
            return
        count = self._roll.apply_fields_to_all(
            state, chosen, default_state=PROJECT_DEFAULT_GUI_STATE)
        if count:
            set_status(self._viewer,
                       f'Applied the selected settings to all {count} frame(s)')

    def apply_paths_to_all_frames(self, field_paths, label: str = '') -> int:
        """Apply named settings to every frame, with no picker in the way.

        Phase 21F. The machinery is the picker's -- `apply_fields_to_all`
        already takes FIELD PATHS -- so this is the same call reached from
        the row menu instead of a dialog. Nothing new is computed; what is
        new is that "this one slider, on every frame" no longer costs a trip
        through a settings tree.

        Returns the number of frames touched (0 when there was nothing to do),
        and reports through the status line exactly as the picker path does:
        it changes every frame in the roll and there is no dialog to have
        confirmed it.
        """
        from spektrafilm_gui.state import PROJECT_DEFAULT_GUI_STATE

        paths = tuple(field_paths or ())
        if not paths:
            return 0
        if not self._roll.files:
            set_status(self._viewer, 'The roll is empty: nothing to apply to')
            return 0
        state = collect_gui_state(widgets=self._widgets)
        count = self._roll.apply_fields_to_all(
            state, paths, default_state=PROJECT_DEFAULT_GUI_STATE)
        if count:
            what = label or (paths[0] if len(paths) == 1 else f'{len(paths)} settings')
            set_status(self._viewer,
                       f'Applied {what} to all {count} frame(s)')
        return count

    def set_fx_prewarm(self, callback) -> None:
        self._fx_prewarm = callback if callable(callback) else None

    def _schedule_fx_prewarm(self) -> None:
        """Warm the FX preview's pipeline a moment after the first render:
        the engine is warm by then and nothing is waiting on the GUI, so the
        first popover opens instantly instead of pausing for ~300 ms."""
        if self._fx_prewarm_done or self._fx_prewarm is None:
            return
        self._fx_prewarm_done = True
        QTimer.singleShot(2500, lambda: self._fx_prewarm and self._fx_prewarm())

    def loaded_frame_shape(self):
        """The loaded photo's (h, w), or None. The FX preview asks: it
        renders its test target at THIS frame's film-plane pitch, so the halo
        it draws is the size the render gives (2026-09-20)."""
        image = self._current_input_image
        return None if image is None else tuple(np.asarray(image).shape[:2])

    def roll_frame_count(self) -> int:
        """How many frames an apply-to-all would touch. The row menu asks so
        it can disable itself with a reason rather than offering an action
        that does nothing."""
        try:
            return len(self._roll.files)
        except Exception:
            return 0
