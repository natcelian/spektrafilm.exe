"""The roll panel (`RollSection`: files, navigation, versions) and the film
strip of source thumbnails under the image.
"""

from __future__ import annotations

from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap

from spektrafilm_gui import theme_palette
from spektrafilm_gui.dyeworks_menu import add_star_row, DyeworksMenu
from spektrafilm_gui.persistence import load_dialog_dir, save_dialog_dir
from spektrafilm_gui.theme_palette import STATE_KEPT, STATE_REJECTED, TEXT_DIM, TEXT_HISTORY
from spektrafilm_gui.widget_primitives import (CollapsibleSection,
                                               normalize_ui_text as _normalize_ui_text)
from spektrafilm_gui.widget_sections import _build_button
from spektrafilm_gui.viewer_chrome import (
    FRAME_GAP,
    ROLE_DIMMED,
    ROLE_FLAGGED,
    ROLE_NUMBER,
    ROLE_RATING,
    ROLE_UNREADABLE,
    import_filter,
)

QFileDialog = QtWidgets.QFileDialog
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QSizePolicy = QtWidgets.QSizePolicy
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget
Qt = QtCore.Qt
Signal = QtCore.Signal


#: A version item's verdict ('live' | 'kept' | 'rejected'), stored on the
#: item so a VIEW of the list can colour its dot without parsing a tooltip.
VERDICT_ROLE = Qt.UserRole + 1


class RollSection(QWidget):
    """Multi-file roll panel (Phase 5 / B1): file list, position, navigation,
    and reference-look propagation. Pure view -- the controller owns the
    RollSession and reacts to these signals."""

    files_added = Signal(list)          # list[str] absolute paths
    file_activated = Signal(int)        # index the user wants to switch to
    remove_requested = Signal(int)      # index to remove
    clear_requested = Signal()          # empty the whole roll
    apply_look_requested = Signal()     # stamp current look onto every file
    analyze_requested = Signal()        # measure every frame's auto EV (roll average)
    apply_roll_exposure_requested = Signal()  # stamp the roll-average EV onto all frames
    save_roll_requested = Signal()      # save the roll session to a .sfroll file
    open_roll_requested = Signal()      # open a .sfroll roll session file
    # Phase 17D attempts (curated history tier; wired controller-side)
    attempt_keep_requested = Signal()        # manual Keep of the current look
    attempt_selected = Signal(str)           # attempt id: reload its state
    attempt_verdict_changed = Signal(str, str)  # (id, 'live'|'kept'|'rejected')
    attempt_label_changed = Signal(str, str)    # (id, new label)
    attempt_delete_requested = Signal(str)      # attempt id

    @property
    def _DIALOG_FILTER(self) -> str:            # noqa: N802 - kept as it was
        """The `+` tile's own filter, not a second copy of it.

        This was a hand-written literal listing every extension, which is
        how the app came to hold three lists of what it can open and
        disagree with itself about `.exr`. One builder, one answer.
        """

        return import_filter()

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self) -> None:
        nav_container = self._build_navigation()
        analysis_container = self._build_analysis_row()
        session_container = self._build_session_row()

        self.analysis_label = QLabel('')
        self.analysis_label.setWordWrap(True)
        self.analysis_label.setProperty('role', 'note')
        self.analysis_label.setVisible(False)
        self.analysis_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)
        content_layout.setAlignment(Qt.AlignTop)
        content_layout.addWidget(nav_container)
        content_layout.addWidget(self.file_list)
        content_layout.addWidget(self.apply_look_button)
        content_layout.addWidget(analysis_container)
        content_layout.addWidget(session_container)
        content_layout.addWidget(self.analysis_label)
        content = QWidget()
        content.setLayout(content_layout)
        content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        attempts_content = self._build_versions()

        # 2026-07-29; see design/DECISIONS.md#d-widget_sections-901
        content_layout.addWidget(
            CollapsibleSection(_normalize_ui_text('Versions'), attempts_content, expanded=True))

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(CollapsibleSection(_normalize_ui_text('Roll'), content, expanded=True))
        self.setLayout(root)

    def _build_navigation(self):
        """The frame list, the position readout, the step and add / remove / clear buttons, Apply look."""
        self.file_list = QtWidgets.QListWidget()
        self.file_list.setObjectName('rollFileList')
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.file_list.itemActivated.connect(self._emit_activated)
        self.file_list.itemClicked.connect(self._emit_activated)
        self._update_list_height()

        self.position_label = QLabel(_normalize_ui_text('0 / 0'))
        self.position_label.setObjectName('rollPositionLabel')

        add_button = _build_button(
            'Add files', self._choose_files,
            tooltip='Add RAW or RGB images to the roll (multi-select). RAWs go through Import Raw, the rest through Import RGB.',
            role='compactAction',
        )
        remove_button = _build_button(
            'Remove', self._emit_remove,
            tooltip='Remove the selected file from the roll (the file on disk is untouched)',
            role='compactAction',
        )
        clear_button = _build_button(
            'Clear', self.clear_requested.emit,
            tooltip='Empty the whole roll (files on disk are untouched; per-frame looks are dropped)',
            role='compactAction',
        )
        # 2026-07-22; see design/DECISIONS.md#d-widget_sections-902
        def _arrow_button(arrow, callback, tip):
            button = QtWidgets.QToolButton()
            button.setArrowType(arrow)
            button.setToolTip(tip)
            button.setFixedSize(20, 24)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(callback)
            return button

        prev_button = _arrow_button(Qt.LeftArrow, lambda: self._step(-1), 'Previous frame')
        next_button = _arrow_button(Qt.RightArrow, lambda: self._step(+1), 'Next frame')
        self.apply_look_button = _build_button(
            'Apply look to all', self.apply_look_requested.emit,
            tooltip='Stamp the current settings (the reference look) onto every file in the roll. '
                    'Frames without a stamped look inherit whatever the controls currently show.',
            role='compactAction',
        )
        self.apply_look_button.setEnabled(False)

        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(4)
        nav_row.addWidget(prev_button)
        nav_row.addWidget(self.position_label)
        nav_row.addWidget(next_button)
        nav_row.addStretch(1)
        nav_row.addWidget(add_button)
        nav_row.addWidget(remove_button)
        nav_row.addWidget(clear_button)
        nav_container = QWidget()
        nav_container.setLayout(nav_row)
        # Fixed vertical policies + a top-aligned stack: otherwise any extra
        # vertical space handed to the section is distributed INTO the rows,
        # which reads as empty gaps around the frame counter.
        nav_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.file_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.apply_look_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        return nav_container

    def _build_analysis_row(self):
        """Analyze roll and Apply roll exposure (NegPy batch analysis)."""
        # ---- roll normalization (NegPy Batch Analysis) -----------------
        self.analyze_button = _build_button(
            'Analyze roll', self.analyze_requested.emit,
            tooltip='Measure every frame\'s auto-exposure EV (off the UI thread) and compute the '
                    'roll average with outliers discarded. Nothing is changed until you apply it.',
            role='compactAction',
        )
        self.apply_roll_exposure_button = _build_button(
            'Use roll exposure', self.apply_roll_exposure_requested.emit,
            tooltip='Stamp the roll-average EV onto every frame: per-frame auto exposure OFF, one '
                    'shared exposure for the whole roll (consistent brightness relationship, like '
                    'printing the roll with one enlarger setting). Re-applying after a new analysis '
                    'replaces the previous roll EV, it never compounds. Your own exposure '
                    'compensation on top of it is preserved.',
            role='compactAction',
        )
        self.apply_roll_exposure_button.setEnabled(False)
        analysis_row = QHBoxLayout()
        analysis_row.setContentsMargins(0, 0, 0, 0)
        analysis_row.setSpacing(4)
        analysis_row.addWidget(self.analyze_button)
        analysis_row.addWidget(self.apply_roll_exposure_button)
        analysis_container = QWidget()
        analysis_container.setLayout(analysis_row)
        analysis_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        return analysis_container

    def _build_session_row(self):
        """Save roll and Open roll (.sfroll)."""
        # ---- roll session file (Phase 8: .sfroll save/open) -------------
        self.save_roll_button = _build_button(
            'Save roll', self.save_roll_requested.emit,
            tooltip='Save the roll as a .sfroll session file: the photo locations, every '
                    'frame\'s edits and the roll exposure, never the photos themselves. '
                    'Double-click the file later to reopen the roll exactly as left.',
            role='compactAction',
        )
        self.open_roll_button = _build_button(
            'Open roll', self.open_roll_requested.emit,
            tooltip='Open a .sfroll roll session file (replaces the current roll; source '
                    'images are found relative to the roll file first, so a synced or '
                    'moved folder keeps working).',
            role='compactAction',
        )
        session_row = QHBoxLayout()
        session_row.setContentsMargins(0, 0, 0, 0)
        session_row.setSpacing(4)
        session_row.addWidget(self.save_roll_button)
        session_row.addWidget(self.open_roll_button)
        session_container = QWidget()
        session_container.setLayout(session_row)
        session_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        return session_container

    def _build_versions(self):
        """The versions list with its Keep and Remove buttons."""
        # 2026-07-29; see design/DECISIONS.md#d-widget_sections-903
        self.attempts_list = QtWidgets.QListWidget()
        self.attempts_list.setObjectName('rollVersionsList')
        self.attempts_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.attempts_list.setFixedHeight(96)
        self.attempts_list.itemActivated.connect(self._emit_attempt_selected)
        self.attempts_list.itemClicked.connect(self._emit_attempt_selected)
        self.attempts_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.attempts_list.customContextMenuRequested.connect(self._attempts_context_menu)
        self.keep_attempt_button = _build_button(
            'Keep', self._on_keep_version_clicked,
            tooltip='Keep the selected version (protected from history pruning). '
                    'With nothing selected, the current settings are stored as a '
                    'new kept version. Click a version to reload it exactly.',
            role='compactAction',
        )
        self.remove_attempt_button = _build_button(
            'Remove', self._on_remove_version_clicked,
            tooltip='Flag the selected version as removed. It stays stored in the '
                    'image\'s history (dimmed, magenta dot) and can still be '
                    'reloaded or kept again; nothing is ever deleted.',
            role='compactAction',
        )
        version_buttons = QHBoxLayout()
        version_buttons.setContentsMargins(0, 0, 0, 0)
        version_buttons.setSpacing(4)
        version_buttons.addWidget(self.keep_attempt_button)
        version_buttons.addWidget(self.remove_attempt_button)
        version_buttons_container = QWidget()
        version_buttons_container.setLayout(version_buttons)
        version_buttons_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        attempts_layout = QVBoxLayout()
        attempts_layout.setContentsMargins(0, 0, 0, 0)
        attempts_layout.setSpacing(4)
        attempts_layout.addWidget(self.attempts_list)
        attempts_layout.addWidget(version_buttons_container)
        attempts_content = QWidget()
        attempts_content.setLayout(attempts_layout)
        attempts_content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        return attempts_content

    # ---- attempts (Phase 17D) -------------------------------------------
    # The film's own vocabulary: cyan live, yellow kept, magenta rejected.
    # Phase 18C: the TOKENS, not a restatement of their values — these three
    # hexes were already the Dyeworks dyes and would have drifted on the next
    # retune.
    _ATTEMPT_VERDICT_COLORS = {
        'live': theme_palette.STATE_LIVE,
        'kept': STATE_KEPT,
        'rejected': STATE_REJECTED,
    }

    def _attempt_verdict_icon(self, verdict: str):
        cache = getattr(self, '_attempt_icon_cache', None)
        if cache is None:
            cache = self._attempt_icon_cache = {}
        if verdict not in cache:
            pixmap = QPixmap(10, 10)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            color = QColor(self._ATTEMPT_VERDICT_COLORS.get(verdict, TEXT_DIM))
            painter.setBrush(color)
            painter.setPen(color)
            painter.drawEllipse(1, 1, 8, 8)
            painter.end()
            cache[verdict] = QIcon(pixmap)
        return cache[verdict]

    def set_attempts(self, entries: list) -> None:
        """Rebuild the versions list. ``entries`` are dicts {id, label,
        verdict, origin, timestamp}, newest first (controller-provided).
        Removed versions (verdict 'rejected') stay listed — stored, dimmed,
        magenta dot — never deleted."""

        self.attempts_list.clear()
        for entry in entries:
            verdict = entry.get('verdict', 'live')
            # 2026-08-03; see design/DECISIONS.md#d-widget_sections-904
            item = QtWidgets.QListWidgetItem(
                self._attempt_verdict_icon(verdict),
                str(entry.get('label', '')))
            item.setData(Qt.UserRole, str(entry.get('id', '')))
            # The VERDICT, readable without parsing the tooltip. The library
            # panel's version rows colour their dot from it (18F rebuild).
            item.setData(VERDICT_ROLE, verdict)
            tooltip = f"{verdict}, {entry.get('origin', 'auto')}"
            timestamp = entry.get('timestamp')
            if timestamp:
                tooltip += f', {timestamp}'
            if verdict == 'rejected':
                tooltip += ', removed (stored; Keep restores it)'
                # tokens.md: ink-4 is for struck-through rejected history ONLY
                item.setForeground(QBrush(QColor(TEXT_HISTORY)))
            item.setToolTip(tooltip)
            self.attempts_list.addItem(item)

    def _emit_attempt_selected(self, item) -> None:
        attempt_id = item.data(Qt.UserRole)
        if attempt_id:
            self.attempt_selected.emit(str(attempt_id))

    def _selected_version_id(self) -> str:
        item = self.attempts_list.currentItem()
        return str(item.data(Qt.UserRole) or '') if item is not None else ''

    def _on_keep_version_clicked(self) -> None:
        """Keep = mark the selected version kept; with no selection, store
        the current settings as a new kept version (NAT 2026-07-29)."""
        version_id = self._selected_version_id()
        if version_id:
            self.attempt_verdict_changed.emit(version_id, 'kept')
        else:
            self.attempt_keep_requested.emit()

    def _on_remove_version_clicked(self) -> None:
        """Remove = FLAG as removed (NAT 2026-07-29): the version is stored
        with the 'rejected' verdict, never deleted. Keep restores it."""
        version_id = self._selected_version_id()
        if version_id:
            self.attempt_verdict_changed.emit(version_id, 'rejected')

    def _attempts_context_menu(self, pos) -> None:
        # Verdicts are the two buttons' job (Keep / Remove); the menu only
        # offers the label edit.
        item = self.attempts_list.itemAt(pos)
        if item is None:
            return
        attempt_id = str(item.data(Qt.UserRole) or '')
        if not attempt_id:
            return

        menu = DyeworksMenu(self.attempts_list)
        rename = menu.addAction(_normalize_ui_text('Rename...'))
        rename.triggered.connect(lambda: self._rename_attempt(attempt_id, item.text()))
        menu.exec(self.attempts_list.mapToGlobal(pos))

    def _rename_attempt(self, attempt_id: str, current_label: str) -> None:
        label, accepted = QtWidgets.QInputDialog.getText(
            self, _normalize_ui_text('Rename attempt'),
            _normalize_ui_text('Label:'), text=current_label)
        if accepted and label.strip():
            self.attempt_label_changed.emit(attempt_id, label.strip())

    # ------------------------------------------------------------- slots
    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, _normalize_ui_text('Add files to roll'),
            load_dialog_dir('roll_input'), self._DIALOG_FILTER,
        )
        if not paths:
            return
        save_dialog_dir('roll_input', str(Path(paths[0]).parent))
        self.files_added.emit(list(paths))

    def _emit_activated(self, item) -> None:
        row = self.file_list.row(item)
        if row >= 0:
            self.file_activated.emit(row)

    def _emit_remove(self) -> None:
        row = self.file_list.currentRow()
        if row >= 0:
            self.remove_requested.emit(row)

    def _step(self, delta: int) -> None:
        count = self.file_list.count()
        if count == 0:
            return
        row = self.file_list.currentRow()
        new_row = max(0, min(count - 1, (row if row >= 0 else 0) + delta))
        if new_row != row:
            self.file_activated.emit(new_row)

    # ------------------------------------------------------------- view API
    def _update_list_height(self) -> None:
        # Size the list to its contents (1..8 rows) so a short roll does not
        # leave a block of empty space inside the section.
        count = self.file_list.count()
        row_h = self.file_list.sizeHintForRow(0) if count > 0 else self.file_list.fontMetrics().height() + 6
        rows = max(1, min(count, 8))
        frame = 2 * self.file_list.frameWidth()
        self.file_list.setFixedHeight(rows * max(row_h, 12) + frame + 2)

    def set_files(self, names: list[str], current_index: int, position: str) -> None:
        """Rebuild the list without re-emitting selection signals."""
        was_blocked = self.file_list.blockSignals(True)
        try:
            self.file_list.clear()
            for name in names:
                self.file_list.addItem(_normalize_ui_text(name))
            if 0 <= current_index < len(names):
                self.file_list.setCurrentRow(current_index)
        finally:
            self.file_list.blockSignals(was_blocked)
        self._update_list_height()
        self.position_label.setText(_normalize_ui_text(position))
        self.apply_look_button.setEnabled(len(names) > 0)

    def set_current(self, index: int, position: str) -> None:
        was_blocked = self.file_list.blockSignals(True)
        try:
            self.file_list.setCurrentRow(index)
        finally:
            self.file_list.blockSignals(was_blocked)
        self.position_label.setText(_normalize_ui_text(position))

    def set_analyzing(self, analyzing: bool) -> None:
        self.analyze_button.setText(_normalize_ui_text('Cancel analysis' if analyzing else 'Analyze roll'))

    def set_analysis_result(self, text: str, apply_enabled: bool) -> None:
        self.analysis_label.setText(_normalize_ui_text(text))
        self.analysis_label.setVisible(bool(text))
        self.apply_roll_exposure_button.setEnabled(apply_enabled)


class FilmStripWidget(QWidget):
    """Horizontal strip of small UNEDITED source thumbnails under the image
    (like frames on a contact sheet). Pure view: the controller feeds entries
    and thumbnails, clicking a frame asks the controller to switch. Hidden
    while the roll is empty so single-image workflows are unchanged."""

    file_activated = Signal(int)
    #: the `+` tile: paths the user picked, of any kind (Phase 18G)
    import_requested = Signal(list)
    #: right-click on a frame: (index, stars) (Phase 18G)
    rate_requested = Signal(int, int)
    # 2026-08-25; see design/DECISIONS.md#d-widget_sections-905
    copy_look_requested = Signal(int)
    paste_look_requested = Signal(int)
    reference_requested = Signal(int)
    #: and the scanner popover, which belongs to no frame in particular
    export_options_requested = Signal()
    #: right-click: stamp the current look onto every frame in the roll
    apply_look_requested = Signal()
    #: right-click: drop the frame from the roll. The FILE IS NOT TOUCHED --
    #: NAT asked for it in those words ("doesn't delete the file, just
    #: remove the image from the roll"), and that is already what
    #: `RollSession.remove` does. Like copy/paste/reference before it, the
    #: action existed (Ctrl+Del, `roll_remove_current`) everywhere except the
    #: one place you are already pointing at the frame.
    remove_from_roll_requested = Signal(int)
    #: put a frame back to its baseline -- the stock's own values for
    #: profile-synced fields, the project defaults for everything else
    reset_look_requested = Signal(int)

    # Phase 18G: `.fr { width: 104px; height: 68px }` — the design's frame.
    THUMB_W = 104
    THUMB_H = 68
    #: `.viewer { grid-template-rows: minmax(0,1fr) 96px }` — the strip is a
    #: ROW OF THE VIEWER, a full-bleed 96px band, not a card floating in the
    #: panel's margins. 96 - 68 leaves 14 above and below the frames.
    BAND_H = 96
    BAND_PAD_X = 16            # `.filmstrip { padding: 0 16px }`
    BAND_PAD_TOP = (BAND_H - THUMB_H) // 2      # 14
    # 2026-07-22; see design/DECISIONS.md#d-widget_sections-906
    SCROLLBAR_H = 12

    def __init__(self):
        super().__init__()
        self._build_strip_list()

        row = self._build_strip_row()

        # `.filmstrip { padding: 0 16px }` in a 96px viewer row.
        layout = QVBoxLayout()
        layout.setContentsMargins(self.BAND_PAD_X, self.BAND_PAD_TOP,
                                  self.BAND_PAD_X, 0)
        layout.setSpacing(0)
        layout.addLayout(row)
        layout.addStretch(1)
        self.setLayout(layout)
        self.setFixedHeight(self.BAND_H)
        # Phase 18G: the strip is ALWAYS shown now -- the `+` tile is the
        # app's way in, so hiding the strip on an empty roll would hide the
        # only control that fills it. The LIST starts hidden, because an
        # empty roll is where every session begins.
        self.setVisible(True)
        self.strip.setVisible(False)
        # an empty roll opens with the tail holding the row, so the list
        # (now empty of content either way) does not fight it for space
        row.setStretch(0, 0)
        row.setStretch(1, 1)

        pm = QtGui.QPixmap(self.THUMB_W, self.THUMB_H)
        pm.fill(QtGui.QColor(60, 60, 60))
        self._placeholder = QtGui.QIcon(pm)
        # the `+` tile is not laid out (see the comment above `row.addStretch`
        # in `__init__`) -- place it once, at the empty-roll position, so it
        # is not sitting at (0, 0) before the first `set_entries`
        self.add_tile.show()
        self._reposition_add_tile()

    def _build_strip_list(self):
        self.strip = QtWidgets.QListWidget()
        self.strip.setObjectName('filmStripList')
        self.strip.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.strip.setFlow(QtWidgets.QListView.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setMovement(QtWidgets.QListView.Movement.Static)
        self.strip.setIconSize(QtCore.QSize(self.THUMB_W, self.THUMB_H))
        self.strip.setUniformItemSizes(True)
        # Phase 18G: the frames are PAINTED to the design -- number, current
        # ring, culled strike-through, reference flag. A delegate rather
        # than a widget per frame, so `set_entries`, `set_thumbnail`,
        # `set_current` and the activation signal are untouched.
        from spektrafilm_gui.viewer_chrome import FrameDelegate

        self.strip.setItemDelegate(FrameDelegate(self.strip))
        # Phase 18G: `.addtile` — one way in for everything, at the end of
        # the strip where the next frame would go.
        from spektrafilm_gui.viewer_chrome import AddFramesTile

        self.add_tile = AddFramesTile()
        self.add_tile.setParent(self)
        self.add_tile.clicked.connect(self._choose_frames)
        self.strip.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.strip.horizontalScrollBar().valueChanged.connect(
            lambda *_: self._reposition_add_tile())
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 2026-07-22; see design/DECISIONS.md#d-widget_sections-907
        self.strip.setFixedHeight(self.THUMB_H + self.SCROLLBAR_H)
        self.strip.itemClicked.connect(self._emit_activated)
        self.strip.itemActivated.connect(self._emit_activated)
        self.strip.setContextMenuPolicy(Qt.CustomContextMenu)
        self.strip.customContextMenuRequested.connect(self._frame_menu)

    def _build_strip_row(self):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        # SPACING 0: `.filmstrip { gap: 8px }` is carried by each frame's own
        # trailing gap (see FrameDelegate.paint), so the last frame already
        # brings the 8px that separates it from the `+` tile. A row spacing on
        # top of that made the gap 16.
        row.setSpacing(0)
        # ALIGNED TO THE TOP, not the centre. The list is taller than a frame
        # by the scrollbar's allowance, so centring it in the 96px band put
        # every frame 6px high of the page's 14px inset and left the `+` tile
        # and the hint sitting on a different line from the frames beside
        # them. Top-aligning a 68px tile against a 68px frame is exact.
        row.addWidget(self.strip, 1, Qt.AlignTop)
        # 2026-08-02, 2026-09-14; see design/DECISIONS.md#d-widget_sections-908
        row.addStretch(0)          # index 1 — raised to 1 on an empty roll
        # 2026-08-04; see design/DECISIONS.md#d-widget_sections-909
        from spektrafilm_gui.dyeworks_labels import _ElidingLabel

        self.tail = _ElidingLabel(_normalize_ui_text(
            '右键点击底片可进行星级评分、复制调色参数、设为参考图或导出'))
        self.tail.setObjectName('filmStripTail')
        self.tail.setProperty('role', 'caption')
        # PREFERRED, not the eliding label's own `Ignored`: `Ignored` means
        # "give me whatever the stretch says", and at stretch 0 that is
        # nothing -- the hint vanished at every width. `Preferred` asks for
        # its natural width; `_refresh_tail` then takes it away again when
        # the frames want the room, which is what `margin-left: auto` does
        # on the page (the tail is simply pushed out).
        self.tail.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.tail.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # a frame's height, top-aligned with them, centring its own text —
        # so the hint sits on the frames' midline, as it does on the page
        self.tail.setFixedHeight(self.THUMB_H)
        row.addWidget(self.tail, 0, Qt.AlignTop)
        self._strip_row = row
        return row

    def _refresh_tail(self) -> None:
        """Show the hint only while the frames do not need its room.

        `.filmstrip .tail` is pushed right by `margin-left: auto`, so on the
        page a strip with too many frames simply pushes it out of view. Held
        as a ONE-WAY test against the room the frames would want with the
        tail already gone -- asking "is the list scrolling?" oscillates,
        because hiding the tail stops the scrolling that hid it.
        """
        tail = getattr(self, 'tail', None)
        if tail is None:
            return
        row_width = self.width()
        if row_width <= 0:
            return
        frames = self.strip.count() * (self.THUMB_W + 8)
        # the band's 16px padding either side, the tile, and a gap before the
        # hint (the frames' 8px trailing gaps are already inside `frames`)
        chrome = self.add_tile.width() + 2 * self.BAND_PAD_X + 8
        tail.setVisible(frames + chrome + tail.sizeHint().width() <= row_width)

    def _reposition_add_tile(self) -> None:
        """Put the `+` tile one frame slot past the last item, in the
        SAME coordinate space `row`'s layout already positions `self.strip`
        in -- so it sits right after the last frame regardless of whether
        `self.strip` is scrolled, exactly like the design's flex row lets its
        own overflow content reach the tile at its end.

        `self.strip.x()` anchors it to wherever the band's padding actually
        put the list (a plain constant would drift if that ever changes);
        the item count times a frame's own span (see `FrameDelegate.paint`,
        `.filmstrip { gap: 8px }` as each item's trailing gap) is the content
        position of the slot after the last frame; the scrollbar's value is
        how far that position has scrolled out of view.
        """

        # 2026-10-07; see design/DECISIONS.md#d-widget_sections-910
        shown = sum(1 for i in range(self.strip.count()) if not self.strip.isRowHidden(i))
        content_x = shown * (self.THUMB_W + FRAME_GAP)
        x = self.strip.x() + content_x - self.strip.horizontalScrollBar().value()
        self.add_tile.move(x, self.strip.y())
        self.add_tile.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        self._refresh_tail()
        self._reposition_add_tile()

    def _choose_frames(self) -> None:
        """`.addtile` — one dialog for every way in.

        RAW or RGB, one frame or many, or a saved roll. What was picked is
        classified afterwards and reported as a list; the controller decides
        what each kind means. Adding a frame does NOT lock the film stock --
        it is not a decision about how to develop it.
        """

        paths, _ = QFileDialog.getOpenFileNames(
            self, _normalize_ui_text('Add frames'),
            load_dialog_dir('raw_input'), import_filter())
        if not paths:
            return
        save_dialog_dir('raw_input', str(Path(paths[0]).parent))
        self.import_requested.emit([str(p) for p in paths])

    def _frame_menu(self, point) -> None:
        """`right-click a frame for rate · settings · reference · export`.

        2026-07-31, 2026-08-25; see design/DECISIONS.md#d-widget_sections-924
        """
        item = self.strip.itemAt(point)
        if item is None:
            return
        index = self.strip.row(item)
        if index < 0:
            return
        menu = self.build_frame_menu(index)
        menu.exec(self.strip.viewport().mapToGlobal(point))

    def build_frame_menu(self, index: int):
        """The menu for one frame, BUILT but not shown.

        Split out so its contents can be asserted without exec()ing a modal
        menu, which is a test that never returns.

        Three groups, separated: what this frame IS (its rating), what to do
        with its settings, and what to do with the roll. NAT 2026-08-25:
        "when right clicking on an image in edit mode, copy settings, paste
        settings, set as reference, export options should appear in the
        popup" -- all four already existed as keyboard shortcuts, top-bar
        buttons or panels, and none of them could be reached from the one
        place you are already pointing at the frame.

        Every frame action carries the INDEX, so it works on the frame you
        clicked rather than the one that happens to be open -- which is what
        `rate_requested` has always done, and what a contact sheet is for.
        """

        menu = DyeworksMenu(self)
        # 2026-09-26; see design/DECISIONS.md#d-widget_sections-911
        ratings = getattr(self, '_ratings', None) or []
        current = ratings[index] if 0 <= index < len(ratings) else 0
        add_star_row(menu, _normalize_ui_text('Rate'), current,
                     lambda stars, i=index: self.rate_requested.emit(i, stars))

        menu.addSeparator()
        action = menu.addAction(_normalize_ui_text('Copy settings'))
        action.setToolTip('Pick which settings to copy from this frame '
                          '(Ctrl+Shift+C on the open one)')
        action.triggered.connect(
            lambda *_, i=index: self.copy_look_requested.emit(i))
        action = menu.addAction(_normalize_ui_text('Paste settings'))
        action.setToolTip('Paste the copied settings onto this frame '
                          '(Ctrl+Shift+V on the open one)')
        action.triggered.connect(
            lambda *_, i=index: self.paste_look_requested.emit(i))
        action = menu.addAction(_normalize_ui_text('Set as reference'))
        action.setToolTip('Pin this frame as the print to judge the others '
                          'against')
        action.triggered.connect(
            lambda *_, i=index: self.reference_requested.emit(i))

        action = menu.addAction(_normalize_ui_text('Reset settings'))
        action.setToolTip('Put this frame back to its baseline: the stock`s '
                          'own values where a stock sets them, the defaults '
                          'everywhere else')
        action.triggered.connect(
            lambda *_, i=index: self.reset_look_requested.emit(i))

        menu.addSeparator()
        action = menu.addAction(_normalize_ui_text('Apply look to all'))
        action.setToolTip('Stamp the current look onto every frame in the roll')
        action.triggered.connect(lambda *_: self.apply_look_requested.emit())
        action = menu.addAction(_normalize_ui_text('Export options...'))
        action.setToolTip('Export settings - format, encoding, colour '
                          'profile, and the scan itself')
        action.triggered.connect(lambda *_: self.export_options_requested.emit())

        menu.addSeparator()
        action = menu.addAction(_normalize_ui_text('Remove from roll'))
        action.setToolTip('Take this frame out of the roll. The file on disk '
                          'is not touched.')
        action.triggered.connect(
            lambda *_, i=index: self.remove_from_roll_requested.emit(i))
        return menu

    def _emit_activated(self, item) -> None:
        row = self.strip.row(item)
        if row >= 0:
            self.file_activated.emit(row)

    # ------------------------------------------------------------- view API
    def set_entries(self, names: list[str], pixmaps: list, current_index: int,
                    dimmed: list | None = None,
                    flagged_index: int | None = None,
                    ratings: list | None = None,
                    hidden: list | None = None,
                    unreadable: list | None = None) -> None:
        """Rebuild the strip (signal-blocked). ``pixmaps`` holds a QPixmap or
        None (placeholder) per entry; ``flagged_index`` is the REFERENCE
        frame, which the design draws with a small pennant in `kept`.

        2026-10-07; see design/DECISIONS.md#d-widget_sections-925
        """

        self._ratings = list(ratings or [])
        was_blocked = self.strip.blockSignals(True)
        try:
            self.strip.clear()
            for index, (name, pm) in enumerate(zip(names, pixmaps)):
                item = QtWidgets.QListWidgetItem()
                item.setIcon(QtGui.QIcon(pm) if pm is not None else self._placeholder)
                item.setToolTip(_normalize_ui_text(name))
                item.setSizeHint(QtCore.QSize(self.THUMB_W + 8, self.THUMB_H))
                item.setData(ROLE_NUMBER, index + 1)
                item.setData(ROLE_FLAGGED, index == flagged_index)
                item.setData(ROLE_DIMMED, bool(dimmed and index < len(dimmed) and dimmed[index]))
                item.setData(ROLE_RATING, int(self._ratings[index]) if index < len(self._ratings) else 0)
                item.setData(ROLE_UNREADABLE, bool(unreadable and index < len(unreadable) and unreadable[index]))
                self.strip.addItem(item)
            for index in range(len(names)):
                self.strip.setRowHidden(index, bool(hidden and index < len(hidden) and hidden[index]))
            if 0 <= current_index < len(names):
                self.strip.setCurrentRow(current_index)
        finally:
            self.strip.blockSignals(was_blocked)
        self._refresh_tail()
        # 2026-08-02; see design/DECISIONS.md#d-widget_sections-912
        has_frames = len(names) > 0
        self.strip.setVisible(has_frames)
        self._strip_row.setStretch(0, 1 if has_frames else 0)   # the strip
        self._strip_row.setStretch(1, 0 if has_frames else 1)   # the tail
        # 2026-08-04, 2026-09-14; see design/DECISIONS.md#d-widget_sections-913
        self._reposition_add_tile()

    def set_dimmed(self, dimmed: list) -> None:
        """Re-grey the frames whose live version is not kept, without a
        rebuild (a Keep or a Discard on the open frame)."""

        for index in range(self.strip.count()):
            item = self.strip.item(index)
            item.setData(ROLE_DIMMED, bool(index < len(dimmed) and dimmed[index]))

    def set_unreadable(self, unreadable: list) -> None:
        """Mark the frames whose RAW could not be read (`.fr.is-unreadable`),
        without a rebuild."""

        for index in range(self.strip.count()):
            self.strip.item(index).setData(
                ROLE_UNREADABLE, bool(index < len(unreadable) and unreadable[index]))

    def set_thumbnail(self, index: int, pixmap) -> None:
        item = self.strip.item(index)
        if item is not None:
            item.setIcon(QtGui.QIcon(pixmap))

    def set_current(self, index: int) -> None:
        was_blocked = self.strip.blockSignals(True)
        try:
            self.strip.setCurrentRow(index)
        finally:
            self.strip.blockSignals(was_blocked)
