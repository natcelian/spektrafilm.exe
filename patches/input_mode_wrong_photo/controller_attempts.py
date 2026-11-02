"""The attempts part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes AttemptsMixin as a base.
"""
from __future__ import annotations

import numpy as np

from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state_bridge import apply_gui_state
from spektrafilm_gui.state_bridge import collect_gui_state
from spektrafilm_gui.persistence import gui_state_to_dict
from spektrafilm_gui.roll_file import encode_thumbnail


class AttemptsMixin:
    """GuiController's attempts methods."""

    # ------------------------------------------------------------------
    # Attempts (Phase 17D): the coarse, curated history tier — an exposure,
    # not a knob touch (the fine tier is the undo stack above). Attempts
    # live per frame in RollSession and persist in .sfroll v2.
    # ------------------------------------------------------------------
    def _connect_attempts(self) -> None:
        """Wire the roll section's attempts signals (controller-side, like
        the 17A live-WB wiring, so the protected app.py stays untouched)."""
        roll = getattr(self._widgets, 'roll', None)
        for signal_name, slot in (
            ('attempt_keep_requested', self.attempt_keep),
            ('attempt_selected', self.attempt_reload),
            ('attempt_verdict_changed', self.attempt_set_verdict),
            ('attempt_label_changed', self.attempt_set_label),
            ('attempt_delete_requested', self.attempt_delete),
        ):
            signal = getattr(roll, signal_name, None)
            if signal is not None and hasattr(signal, 'connect'):
                signal.connect(slot)

    def _current_look_dict(self) -> dict:
        import json


        # JSON-normalized (tuples -> lists) to match the stored-attempt form,
        # so zero-diff checks hold across a .sfroll save/load.
        return json.loads(json.dumps(
            gui_state_to_dict(collect_gui_state(widgets=self._widgets)),
            default=str))

    def _attempt_thumbnail(self):
        """Small JPEG of the current input preview (best-effort, never
        fatal), per the 17D capture rule."""

        image = self._input_display_cached
        if image is None or not self._loaded_frame_is_current():
            return None     # that preview is another frame's
        array = np.asarray(image)
        if array.dtype != np.uint8:
            array = (np.clip(array, 0.0, 1.0) * 255).astype(np.uint8)
        return encode_thumbnail(array)

    def _versions_target(self):
        """Versions are bound to IMAGES (NAT 2026-07-29): the current roll
        frame when there is one, else the loaded image's path — Keep works
        without a roll, and the versions ride into any roll the image
        later joins (same normalized path key)."""
        current = self._roll.current
        if current is not None:
            return current
        return self._current_input_path or None

    def attempt_keep(self) -> None:
        """Keep with no version selected: store the current look as a new
        kept version (NAT 2026-07-29 version-function semantics)."""
        target = self._versions_target()
        if target is None:
            set_status(self._viewer, 'Load an image before keeping a version')
            return
        attempt = self._roll.add_attempt(
            target,
            look=self._current_look_dict(),
            verdict='kept',
            origin='manual',
            thumbnail=self._attempt_thumbnail(),
        )
        self._capture_kept()
        self._refresh_attempts_view()
        set_status(self._viewer, f'Version kept: {attempt.label}')

    def attempt_discard(self) -> None:
        """Store the current look as a REJECTED version.

        Phase 18D: the top bar's Discard sits beside Keep and must work the
        same way -- on the live look, with nothing selected. The mirror of
        `attempt_keep`; the roll's own list still offers per-version delete.
        Rejected versions are stored and dimmed, never deleted (17D).
        """
        target = self._versions_target()
        if target is None:
            set_status(self._viewer, 'Load an image before discarding a version')
            return
        attempt = self._roll.add_attempt(
            target,
            look=self._current_look_dict(),
            verdict='rejected',
            origin='manual',
            thumbnail=self._attempt_thumbnail(),
        )
        self._refresh_attempts_view()
        set_status(self._viewer, f'Version discarded: {attempt.label}')

    def set_crop_mode(self, active: bool) -> None:
        """Top-bar crop toggle. Arms the persistent Phase 9 crop frame, and
        disarming APPLIES it -- the Lightroom semantics `set_crop_tool`
        already implements."""
        self.set_crop_tool('frame' if active else 'off')

    def _auto_capture_attempt(self) -> None:
        """Promote a snapshot to an attempt at a natural expose moment
        (explicit render, leaving a changed frame). Zero-diff vs the
        image's latest attempt is skipped — slider ticks are the undo
        stack's job."""
        target = self._versions_target()
        if target is None:
            return
        look = self._current_look_dict()
        latest = self._roll.latest_attempt(target)
        if latest is not None and latest.look == look:
            return
        self._roll.add_attempt(
            target,
            look=look,
            verdict='live',
            origin='auto',
            thumbnail=self._attempt_thumbnail(),
        )
        self._refresh_attempts_view()

    def _capture_attempt_on_leave(self) -> None:
        """Leaving a frame with changes since it was entered captures an
        auto attempt (the zero-diff check above still dedups against a
        render-action attempt made moments before)."""
        if self._roll.current is None or self._roll_entry_look is None:
            return
        if self._current_look_dict() != self._roll_entry_look:
            self._auto_capture_attempt()

    def _mark_roll_entry_look(self) -> None:
        current = self._roll.current
        self._roll_entry_look = self._current_look_dict() if current is not None else None

    def attempt_reload(self, attempt_id: str) -> None:
        """Click an attempt: reload its exact state, one render to see it."""
        from spektrafilm_gui.persistence import gui_state_from_dict

        target = self._versions_target()
        attempt = self._roll.find_attempt(target, attempt_id) if target is not None else None
        if attempt is None:
            set_status(self._viewer, 'Version not found')
            return
        apply_gui_state(gui_state_from_dict(attempt.look), widgets=self._widgets)
        self._note_film_stock_synced()
        self._sync_canvas_background()
        set_status(self._viewer, f'Version reloaded: {attempt.label}')
        self._request_auto_preview_if_enabled()

    def attempt_set_verdict(self, attempt_id: str, verdict: str) -> None:
        target = self._versions_target()
        if target is not None and self._roll.set_attempt_verdict(target, attempt_id, verdict):
            self._refresh_attempts_view()

    def attempt_set_label(self, attempt_id: str, label: str) -> None:
        target = self._versions_target()
        if target is not None and self._roll.set_attempt_label(target, attempt_id, label):
            self._refresh_attempts_view()

    def attempt_delete(self, attempt_id: str) -> None:
        target = self._versions_target()
        if target is not None and self._roll.delete_attempt(target, attempt_id):
            self._refresh_attempts_view()
            set_status(self._viewer, 'Version removed')

    def _not_kept_flags(self) -> list[bool]:
        """Per frame: is its LIVE version -- the newest, the one in use when
        nothing is selected -- not kept? Discard stores the look as a
        rejected version, so a frame you discarded reads as not kept until a
        newer version (a Keep, a render) takes its place (QA 2026-10-07)."""
        flags = []
        for roll_file in self._roll.files:
            latest = self._roll.latest_attempt(roll_file)
            flags.append(bool(latest is not None and latest.verdict == 'rejected'))
        return flags

    def _refresh_attempts_view(self) -> None:
        # Runs on Keep and on every frame change: the two moments whether
        # this image has a kept print can change (B8).
        self._sync_kept_state()
        # ...and the strip greys a frame whose live version is not kept
        strip = getattr(self._widgets, 'film_strip', None)
        set_dimmed = getattr(strip, 'set_dimmed', None)
        if callable(set_dimmed):
            set_dimmed(self._not_kept_flags())
        roll_section = getattr(self._widgets, 'roll', None)
        set_attempts = getattr(roll_section, 'set_attempts', None)
        if not callable(set_attempts):
            return
        target = self._versions_target()
        attempts = self._roll.attempts_for(target) if target is not None else []
        set_attempts([
            {
                'id': a.id,
                'label': a.label,
                'verdict': a.verdict,
                'origin': a.origin,
                'timestamp': a.timestamp,
            }
            for a in reversed(attempts)      # newest first for display
        ])
