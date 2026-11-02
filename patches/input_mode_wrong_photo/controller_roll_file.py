"""The roll file part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes RollFileMixin as a base.
"""
from __future__ import annotations

import os
from pathlib import Path

from qtpy import QtWidgets

from spektrafilm_gui import controller_runtime as runtime
from spektrafilm_gui import notices
from spektrafilm_gui.app_settings import app_settings
from spektrafilm_gui.persistence import load_dialog_dir
from spektrafilm_gui.persistence import save_dialog_dir
from spektrafilm_gui.shell_layout import dialog_parent
from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state_bridge import apply_gui_state
from spektrafilm_gui.state_bridge import collect_gui_state
import subprocess
import sys
import json as _json
from spektrafilm_gui.roll_file import (
    SFROLL_DIALOG_FILTER,
    SFROLL_EXTENSION,
    load_roll_dict,
    restore_session,
    save_roll,
)
from spektrafilm_gui.roll_thumbnails import load_thumbnail_array
from copy import deepcopy
from spektrafilm_gui.roll_session import robust_roll_ev

QFileDialog = QtWidgets.QFileDialog
QInputDialog = QtWidgets.QInputDialog


class RollFileMixin:
    """GuiController's roll file methods."""

    def roll_save_to_file(self) -> None:
        """Save the roll (photo locations + per-frame edits + roll EV +
        embedded thumbnails) as a .sfroll session file. Non-destructive:
        the sources are referenced by path, never copied."""

        if not self._roll.files:
            set_status(self._viewer, 'The roll is empty; add files before saving a roll')
            return
        # The file must carry what is on screen: snapshot the current frame.
        self._roll.store_state_for_current(collect_gui_state(widgets=self._widgets))
        start_dir = load_dialog_dir('roll_file') or load_dialog_dir('roll_input')
        filepath, _ = QFileDialog.getSaveFileName(
            dialog_parent(self._viewer), 'Save roll',
            os.path.join(start_dir, 'roll' + SFROLL_EXTENSION) if start_dir else 'roll' + SFROLL_EXTENSION,
            SFROLL_DIALOG_FILTER,
        )
        if not filepath:
            return
        if not filepath.lower().endswith(SFROLL_EXTENSION):
            filepath += SFROLL_EXTENSION
        save_dialog_dir('roll_file', str(Path(filepath).parent))

        # 2026-08-04; see design/DECISIONS.md#d-controller-251
        description, _entered = QInputDialog.getMultiLineText(
            dialog_parent(self._viewer), 'Save roll',
            'Describe this roll (optional):',
            self._roll_description)
        self._roll_description = str(description or '').strip()


        roll_settings = {
            'canvas_background': str(
                app_settings().value('canvas_background', '') or ''),
        }
        try:
            save_roll(self._roll, filepath,
                      description=self._roll_description,
                      roll_settings=roll_settings,
                      thumbnail_fn=load_thumbnail_array)
        except (OSError, ValueError) as exc:
            notices.fail(dialog_parent(self._viewer), 'Save roll',
                                 f'Failed to save the roll file.\n\n{exc}')
            return
        size_kb = os.path.getsize(filepath) / 1024.0
        set_status(self._viewer,
                   f'Saved roll ({len(self._roll.files)} frames, {size_kb:.0f} KB) to {filepath}')

    def roll_open_from_file(self) -> None:
        """Open a .sfroll file chosen in a dialog (replaces the current roll)."""

        filepath, _ = QFileDialog.getOpenFileName(
            dialog_parent(self._viewer), 'Open roll',
            load_dialog_dir('roll_file'), SFROLL_DIALOG_FILTER,
        )
        if filepath:
            save_dialog_dir('roll_file', str(Path(filepath).parent))
            self.open_roll_path(filepath)

    # 2026-10-07; see design/DECISIONS.md#d-controller-252
    def _reveal_file(self, target) -> None:
        """Show a file in the system's file browser, selected."""

        target = Path(target)
        try:
            if sys.platform == 'win32':
                subprocess.Popen(['explorer', '/select,', str(target)])
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', '-R', str(target)])
            else:
                subprocess.Popen(['xdg-open', str(target.parent)])
        except Exception:
            set_status(self._viewer, f'The file is at {target}')

    def _unreadable_keys(self) -> set:
        keys = getattr(self, '_unreadable_paths', None)
        if keys is None:
            keys = self._unreadable_paths = set()
        return keys

    def _mark_unreadable(self, path, unreadable: bool) -> None:
        """`.fr.is-unreadable`: the strip marks a frame whose RAW could not be
        read, until it reads."""
        key = os.path.normcase(os.path.abspath(str(path)))
        keys = self._unreadable_keys()
        if (key in keys) == bool(unreadable):
            return
        (keys.add if unreadable else keys.discard)(key)
        strip = getattr(self._widgets, 'film_strip', None)
        set_unreadable = getattr(strip, 'set_unreadable', None)
        if callable(set_unreadable):
            set_unreadable(self._unreadable_flags())

    def _unreadable_flags(self) -> list[bool]:
        keys = self._unreadable_keys()
        return [os.path.normcase(os.path.abspath(str(f.path))) in keys
                for f in self._roll.files]

    def _report_unreadable(self, path, exc) -> None:
        """`SYSMSG.fail`: '<name> cannot be read', why, and the way out; the
        engine's own sentence (raw_file_processor) as the copyable detail.
        Without a card (a test, a headless shell) the old notice stands."""
        self._mark_unreadable(path, True)
        card = notices.system_message_sink(dialog_parent(self._viewer))
        if card is None:
            notices.fail(dialog_parent(self._viewer), 'Load raw', f'Failed to load RAW image.\n\n{exc}')
            return
        card.show_message(
            'neg', f'{os.path.basename(str(path))} cannot be read',
            "Not a readable RAW file: it may be corrupt, truncated, still syncing, "
            "or a format this build's LibRaw does not support.",
            actions=[('Show in folder', lambda: self._reveal_file(path))],
            detail=str(exc))

    @staticmethod
    def _offline_names(missing) -> str:
        """'frame_04.raf and frame_05.raf', 'a, b and 3 more'."""
        names = [str(m.get('name', '?')) for m in missing]
        if len(names) <= 3:
            return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
        return ', '.join(names[:2]) + f' and {len(names) - 2} more'

    def _report_offline(self, filepath: str, data, roll_dir: str, missing) -> bool:
        """`SYSMSG.missing`: the roll opens with what it found, and a card says
        which frames are offline, with a way to point at their folder. False
        when there is no card (the caller asks the old question instead)."""
        card = notices.system_message_sink(dialog_parent(self._viewer))
        if card is None:
            return False
        count = len(missing)
        title = '1 frame is offline' if count == 1 else f'{count} frames are offline'
        verb = 'was' if count == 1 else 'were'

        def locate() -> None:
            directory = QFileDialog.getExistingDirectory(
                dialog_parent(self._viewer), 'Locate missing roll files',
                load_dialog_dir('roll_input'))
            if directory:
                self._open_roll_data(filepath, data, relink_dirs=[directory])

        card.show_message(
            'warn', title,
            f'{self._offline_names(missing)} {verb} not found beside the roll or at '
            f'{"its saved path" if count == 1 else "their saved paths"}. The roll links '
            'RAWs by path, so they were moved or renamed. Their edits are still in the '
            'roll file.',
            actions=[('Locate folder…', locate)])
        return True

    def open_roll_path(self, filepath: str) -> None:
        """Load a .sfroll roll session (also the double-click / argv entry).
        Sources are resolved relative to the roll file FIRST, then by their
        recorded absolute path; still-missing files get one relink-folder
        prompt and are otherwise skipped (reported, never a crash)."""


        if self._active_simulation_worker is not None or self._active_export_worker is not None:
            set_status(self._viewer, 'Busy rendering; wait before opening a roll')
            return
        try:
            data = load_roll_dict(filepath)
        except (OSError, ValueError, _json.JSONDecodeError) as exc:
            notices.fail(dialog_parent(self._viewer), 'Open roll',
                                 f'Failed to open the roll file.\n\n{exc}')
            return
        # Phase 17E: successful opens feed the Shelf's Rolls MRU.
        try:
            from spektrafilm_gui.shelf_store import record_recent_roll
            record_recent_roll(filepath)
        except Exception:
            pass
        self._open_roll_data(filepath, data)

    def _open_roll_data(self, filepath: str, data, relink_dirs=None) -> None:
        """Open a loaded roll, its sources searched in `relink_dirs` too.
        Offline frames are the page's card (`_report_offline`), whose Locate
        folder… comes back here; without a card, the old question."""

        roll_dir = os.path.dirname(os.path.abspath(filepath))
        session, missing = restore_session(data, roll_dir, relink_dirs=relink_dirs)
        reported = bool(missing) and bool(session.files) and self._report_offline(
            filepath, data, roll_dir, missing)
        if missing and not reported and relink_dirs is None:
            names = ', '.join(str(m.get('name', '?')) for m in missing[:8])
            if notices.confirm(
                    dialog_parent(self._viewer), 'Missing files',
                    f'{len(missing)} source file(s) of this roll were not '
                    f'found ({names}{", ..." if len(missing) > 8 else ""}).'
                    f'\n\nLocate a folder to search for them by filename?',
                    confirm_label='Locate folder…'):
                directory = QFileDialog.getExistingDirectory(
                    dialog_parent(self._viewer), 'Locate missing roll files',
                    load_dialog_dir('roll_input'))
                if directory:
                    session, missing = restore_session(
                        data, roll_dir, relink_dirs=[directory])
        if not session.files:
            notices.warn(dialog_parent(self._viewer), 'Open roll',
                                'None of the roll\'s source files could be found.')
            return
        self._roll = session
        # Film strip: seed instantly from the roll file's EMBEDDED thumbnails
        # (small unedited input previews), then fetch fresh ones for any frame
        # the file did not carry — opening a roll bypasses roll_add_files, the
        # only other place thumbnails were requested.
        self._seed_roll_thumbnails(data)
        self._roll_request_thumbnails(session.files)
        self._roll_refresh_view()
        self._roll_load_current(restore_state=True)
        background = (data.get('roll_settings') or {}).get('canvas_background')
        if background:
            self.set_canvas_background_color(str(background))
        # 2026-09-05; see design/DECISIONS.md#d-controller-253
        self._choose_mode_for_open(frames=0, from_roll=True)
        message = f'Opened roll "{data.get("name", "")}" ({len(session.files)} frames)'
        if missing:
            message += f'; {len(missing)} missing file(s) skipped'
        set_status(self._viewer, message)

    def roll_analyze(self) -> None:
        """B3 (NegPy Batch Analysis): measure every frame's auto-exposure EV off
        the UI thread and compute the roll average with outliers discarded.
        Read-only -- nothing is applied until roll_apply_exposure."""
        if self._active_export_worker is not None:
            if self._roll_analysis_running:
                self._active_export_worker.cancel()
                set_status(self._viewer, 'Roll analysis cancel requested...', timeout_ms=0)
            else:
                set_status(self._viewer, 'An export is running; wait before analyzing.')
            return
        if self._active_simulation_worker is not None:
            set_status(self._viewer, 'A render is already running; wait before analyzing.')
            return
        if not self._roll.files:
            notices.warn(dialog_parent(self._viewer), 'Analyze roll', 'The roll is empty. Add files first.')
            return


        current_state = collect_gui_state(widgets=self._widgets)
        self._roll.store_state_for_current(current_state)
        jobs = []
        for roll_file in self._roll.files:
            frame_state = self._roll.state_for(roll_file) or deepcopy(current_state)
            jobs.append(runtime.BatchFrameJob(
                name=roll_file.name, path=roll_file.path, kind=roll_file.kind, state=frame_state,
                saving_color_space='sRGB', saving_cctf_encoding=True, ext='', bit_depth=8,
            ))

        worker = runtime.RollAnalysisWorker(jobs, measure_fn=self._roll_measure_frame)
        worker.signals.progress.connect(self._on_export_progress)
        worker.signals.finished.connect(self._on_roll_analysis_finished)
        worker.signals.failed.connect(self._on_roll_analysis_failed)
        self._active_export_worker = worker
        self._roll_analysis_running = True
        self._set_export_controls_enabled(False)
        self._widgets.roll.set_analyzing(True)
        set_status(self._viewer, f'Analyzing roll: {len(jobs)} frame(s)...', timeout_ms=0)
        self._thread_pool.start(worker)

    def _on_roll_analysis_finished(self, payload) -> None:
        self._active_export_worker = None
        self._roll_analysis_running = False
        self._set_export_controls_enabled(True)
        self._widgets.roll.set_analyzing(False)
        evs = dict(payload.get('evs', {}))
        failures = list(payload.get('failures', []))
        roll_ev, used, rejected = robust_roll_ev(list(evs.values()))
        self._roll_analysis_ev = roll_ev if used else None
        self._sync_library_roll()
        pieces = [f'roll EV {roll_ev:+.2f} from {used} frame(s)']
        if rejected:
            pieces.append(f'{rejected} outlier(s) discarded')
        if failures:
            pieces.append(f'{len(failures)} failed ({failures[0]})')
        text = 'Roll analysis: ' + ', '.join(pieces)
        self._widgets.roll.set_analysis_result(text, apply_enabled=used > 0)
        set_status(self._viewer, text)
        self._replay_pending_auto_preview()

    def _on_roll_analysis_failed(self, message: str) -> None:
        self._active_export_worker = None
        self._roll_analysis_running = False
        self._set_export_controls_enabled(True)
        self._widgets.roll.set_analyzing(False)
        set_status(self._viewer, message)
        self._replay_pending_auto_preview()

    def roll_apply_exposure(self) -> None:
        if self._roll_analysis_ev is None:
            set_status(self._viewer, 'Run "Analyze roll" first.')
            return
        if self._active_simulation_worker is not None or self._active_export_worker is not None:
            set_status(self._viewer, 'Busy rendering; wait before applying the roll exposure.')
            return
        current_state = collect_gui_state(widgets=self._widgets)
        self._roll.store_state_for_current(current_state)
        count = self._roll.apply_roll_exposure(self._roll_analysis_ev, current_state)
        # The current frame's stored look changed: reflect it in the widgets.
        current = self._roll.current
        if current is not None:
            stored = self._roll.state_for(current)
            if stored is not None:
                apply_gui_state(stored, widgets=self._widgets)
                self._note_film_stock_synced()
                self._sync_canvas_background()
        set_status(
            self._viewer,
            f'Roll exposure {self._roll_analysis_ev:+.2f} EV applied to {count} frame(s) '
            f'(held: auto exposure stays on at this one shared exposure for the roll)',
        )
        self._request_auto_preview_if_enabled()

    def _roll_load_current(self, *, restore_state: bool) -> None:
        roll_file = self._roll.current
        if roll_file is None:
            return
        # 2026-07-25; see design/DECISIONS.md#d-controller-254
        if self._input_mode:
            self._current_input_path = roll_file.path
            self._input_mode_navigated = True
            # The frame's LOOK still comes with it (cheap: no decode), so the
            # panels always belong to the selected frame -- what a frame
            # switch stores, Copy settings copies and Apply to all applies.
            if restore_state:
                stored = self._roll.state_for(roll_file)
                if stored is not None:
                    apply_gui_state(stored, widgets=self._widgets)
                    self._note_film_stock_synced()
                    self._sync_canvas_background()
                    self._warn_if_missing_custom_stock()
            self._widgets.filepicker.cancel_scheduled_reprocess()
            self._widgets.load_raw.cancel_scheduled_reprocess()
            image = self._show_instant_input()
            if image is not None:
                self._update_banner_and_metadata(image)
            self._mark_roll_entry_look()
            self._refresh_attempts_view()
            return
        if restore_state:
            stored = self._roll.state_for(roll_file)
            if stored is not None:
                apply_gui_state(stored, widgets=self._widgets)
                self._note_film_stock_synced()
                self._sync_canvas_background()
                self._warn_if_missing_custom_stock()
        # Applying state may have armed the RGB picker's debounced reprocess
        # with the OUTGOING file's path; the explicit load below supersedes it.
        self._widgets.filepicker.cancel_scheduled_reprocess()
        self._widgets.load_raw.cancel_scheduled_reprocess()
        if roll_file.kind == 'raw':
            self._widgets.load_raw.set_path(roll_file.path)
            self._widgets.filepicker.set_path('')
            self.load_raw_image(roll_file.path)
        else:
            self._widgets.filepicker.set_path(roll_file.path)
            self.load_input_image(roll_file.path)
        # Phase 17D: remember the entry look (change detection on leave)
        # and show this frame's attempts.
        self._mark_roll_entry_look()
        self._refresh_attempts_view()
