"""The library panel's sections, built to the spec (Phase 18F).

These are VIEWS over the existing sections' editors. Each one takes the real
editor widgets out of a `DataclassSection` and re-presents them in the
design's shape -- but it never replaces them, so `.value`, `valueChanged`,
the state bridge, profile sync, undo and persistence all keep working
against the same objects they always did.

That indirection is the whole trick. The design's rows are a different SHAPE
from the app's (label above, value right, control full width), and a
stylesheet cannot change a widget's shape. Rebuilding the presentation while
keeping the editors is how the look changes without the app changing.
"""

from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets

from spektrafilm_gui.dyeworks_widgets import DyeworksSection
from spektrafilm_gui.dyeworks_buttons import (AttemptList, ButtonPair, ButtonRows, IconStrip,
                                              ToggleSwitch, WideButton)
from spektrafilm_gui.dyeworks_chips import ChipSelect, CONTACT_CHIP_MENU_PX, contact_chip_pixmap
from spektrafilm_gui.dyeworks_labels import NoteRule, FileName, FilePath, Hint, KeyValueTable
from spektrafilm_gui.roll_section import VERDICT_ROLE
from spektrafilm_gui.dyeworks_colour_controls import WB_POPOVER_WIDTH, WhiteBalancePad
from spektrafilm_gui.dyeworks_popover import Popover
from spektrafilm_gui.field_labels import option_label
from spektrafilm_gui.preset_store import preset_facts

Qt = QtCore.Qt


class FileSection(DyeworksSection):
    """`File` — what this frame IS, and the decisions taken when it was read.

    The design groups the read-out (name, path, dimensions, backend) with
    the import-time choices (white balance, lens correction), because they
    are all answers to 'what am I looking at', not to 'how should it look'.
    """

    #: Armed/disarmed by the eyedropper. The controller listens, puts the
    #: canvas into sampling mode, and calls `sample_landed` with the result.
    sample_neutral_requested = QtCore.Signal(bool)

    # 2026-07-30; see design/DECISIONS.md#d-left_panel_sections-584
    _WB_STRIP = (
        ('as_shot', 'wb-cam', '相机拍摄设定: 使用相机记录的原始白平衡'),
        ('daylight', 'wb-sun', '日光 (5600K)'),
        ('tungsten', 'wb-bulb', '白炽灯 (3800K)'),
        (None, 'wb-pick', '画面中性灰吸管取样 (设为自定义)'),
        ('custom', 'wb-tune', '自定义: 手动微调色温与色调'),
    )
    #: position of the eyedropper within _WB_STRIP
    _EYEDROPPER_INDEX = 3

    def __init__(self, load_raw) -> None:
        super().__init__('文件', expanded=True)
        self._load_raw = load_raw

        self.name_label = FileName('未载入底片')
        self.add(self.name_label)
        self.path_label = FilePath('')
        # 2026-08-03; see design/DECISIONS.md#d-left_panel_sections-585
        self.path_label.setVisible(False)
        self.add(self.path_label)

        self.info = KeyValueTable((
            ('图像尺寸', '—'),
            ('文件格式', '—'),
            ('计算后端', '—'),
        ))
        self.add(self.info)

        # -- white balance -------------------------------------------------
        # A GROUP, not a label. `renderFileImport` builds `#file-import` as a
        # `.grp` with its own twisty, meta and top border -- so White balance
        # collapses, and the import-time decisions read as one block rather
        # than as loose rows under a heading.
        self.wb_group = DyeworksSection('白平衡', variant='group',
                                     rule='above')
        self.wb_summary = self.wb_group.meta_label
        self.add(self.wb_group)

        # ONE row of five, in the design's order. The eyedropper is at
        # position 3 but outside the exclusive group, because it samples
        # rather than selects.
        self.wb_strip = IconStrip(
            tuple((glyph, tooltip) for _state, glyph, tooltip in self._WB_STRIP),
            standalone=(self._EYEDROPPER_INDEX,))
        self.wb_strip.changed.connect(self._on_wb_picked)
        self.wb_strip.standalone_toggled.connect(self._on_eyedropper_toggled)
        self.wb_group.add(self.wb_strip)

        self.wb_caption = Hint('')
        self.wb_group.add(self.wb_caption)

        # 2026-08-03; see design/DECISIONS.md#d-left_panel_sections-586

        # The page's note, both sentences. The second is the one that
        # explains the eyedropper, which is the only control in the row
        # whose behaviour is not obvious from its icon.
        self.wb_group.add(Hint(
            '属于载入期设置而非后置外观：修改此项会重新解码载入 RAW。'
            '吸管工具可在画面上吸取中性灰并设为自定义白平衡。'))

        self.show_button = WideButton('定位文件', glyph='folder')
        self.show_button.setToolTip('在资源管理器中定位当前照片文件')
        self.reprocess_button = WideButton('重新处理 RAW')
        self.add(ButtonPair(self.show_button, self.reprocess_button))

        self._bind()

    # ------------------------------------------------------------- binding
    def _bind(self) -> None:
        """Mirror the real editors both ways.

        The editors stay authoritative -- these controls write INTO them, and
        follow when anything else (a preset, undo, a roll load) writes to
        them instead. Without the second direction the strip would go stale
        the first time state was applied from outside.
        """
        editor = self._load_raw.white_balance
        editor.currentTextChanged.connect(self._sync_from_editor)
        # The caption reads the temperature and tint back, so it follows them
        # wherever they are changed -- the pad, its sliders, a preset, undo.
        for editor in (self._load_raw.temperature, self._load_raw.tint):
            signal = getattr(editor, 'valueChanged', None)
            if signal is not None:
                signal.connect(lambda *_: self._refresh_caption())
        # The section's own reprocess button carries the wiring; this one
        # just presses it, so the debounce stays put. Its ENABLED state has
        # to be mirrored as well -- the original is disabled until a RAW is
        # loaded, and a button you can press that does nothing is worse than
        # a disabled one. Qt has no signal for it, hence the event filter.
        self.reprocess_button.clicked.connect(self._load_raw.reprocess_button.click)
        self._load_raw.reprocess_button.installEventFilter(self)
        self._sync_reprocess_enabled()
        self._sync_from_editor()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API name
        if (watched is self._load_raw.reprocess_button
                and event.type() == QtCore.QEvent.EnabledChange):
            self._sync_reprocess_enabled()
        return super().eventFilter(watched, event)

    def _sync_reprocess_enabled(self) -> None:
        self.reprocess_button.setEnabled(self._load_raw.reprocess_button.isEnabled())

    def _state_at(self, index: int) -> str | None:
        if 0 <= index < len(self._WB_STRIP):
            return self._WB_STRIP[index][0]
        return None

    def _index_of(self, state: str) -> int | None:
        for index, (value, _glyph, _tip) in enumerate(self._WB_STRIP):
            if value == state:
                return index
        return None

    def _on_wb_picked(self, index: int) -> None:
        state = self._state_at(index)
        if state is not None:
            self._load_raw.white_balance.value = state
        self._refresh_caption()
        # Custom is the only preset with something to SET, so picking it
        # opens the pad -- the design's `#wb-pop`. The other four are named
        # illuminants and have nothing to adjust.
        if state == 'custom':
            self.open_wb_pad()

    def open_wb_pad(self) -> None:
        """`#wb-pop` — temperature and tint on one pad, film neutral centred.

        Kept and reused, so it holds wherever it was dragged to.
        """

        popover = getattr(self, '_wb_popover', None)
        try:
            alive = popover is not None and popover.objectName() is not None
        except RuntimeError:
            alive = False

        if not alive:
            popover = Popover('Custom white balance', width=WB_POPOVER_WIDTH,
                              parent=self)
            pad = WhiteBalancePad(self._load_raw.temperature,
                                  self._load_raw.tint)
            # The pad FILLED the body, as it does on the page; the stretches
            # that used to centre a fixed-width pad made it a different
            # shape from the design's. 20.3 widened the popover (323) for its
            # sliders and the pad keeps its 258, centred, so its grid stays even.
            holder = QtWidgets.QWidget()
            centre = QtWidgets.QHBoxLayout(holder)
            centre.setContentsMargins(0, 0, 0, 0)
            centre.addStretch(1)
            centre.addWidget(pad)
            centre.addStretch(1)
            popover.add(holder)
            # 2026-08-28; see design/DECISIONS.md#d-left_panel_sections-587
            from spektrafilm_gui.dyeworks_colour_controls import WB_PAD_TO_ROWS_GAP
            from spektrafilm_gui.dyeworks_popover import BODY_GAP

            popover.add_spacing(max(0, WB_PAD_TO_ROWS_GAP - BODY_GAP))
            # 2026-08-02; see design/DECISIONS.md#d-left_panel_sections-588
            from spektrafilm_gui.dyeworks_editors import slider_row

            popover.add(slider_row('Temperature', self._load_raw.temperature,
                                   step=50.0, decimals=0, unit=' K',
                                   on_changed=self._refresh_wb_meta))
            popover.add(slider_row('Tint', self._load_raw.tint,
                                   step=0.01, decimals=2,
                                   on_changed=self._refresh_wb_meta))
            popover.add(Hint(
                'Drag the pad or the sliders: they stay in sync. The centre '
                'mark is the film neutral, 5600 K and tint 1.00. Applies on '
                're-import.'))
            pad.changed.connect(self._refresh_wb_meta)
            for editor in (self._load_raw.temperature, self._load_raw.tint):
                signal = getattr(editor, 'valueChanged', None)
                if signal is not None:
                    signal.connect(self._refresh_wb_meta)
            self._wb_popover = popover
            self._wb_pad = pad

        self._refresh_wb_meta()
        popover.show_beside(self.wb_strip)

    def _refresh_wb_meta(self, *_args) -> None:
        popover = getattr(self, '_wb_popover', None)
        if popover is None:
            return
        try:
            popover.set_meta('%.0f K, %.2f'
                             % (float(self._load_raw.temperature.value),
                                float(self._load_raw.tint.value)))
        except (RuntimeError, TypeError, ValueError):
            pass

    def _on_eyedropper_toggled(self, _index: int, armed: bool) -> None:
        self.sample_neutral_requested.emit(bool(armed))

    def sample_landed(self, temperature: float, tint: float) -> None:
        """The controller calls this with what the click sampled. Landing on
        Custom is the spec's own wording, and it is what makes the sample
        editable afterwards instead of a one-shot."""
        self._load_raw.temperature.value = float(temperature)
        self._load_raw.tint.value = float(tint)
        self._load_raw.white_balance.value = 'custom'
        self.wb_strip.set_standalone_armed(self._EYEDROPPER_INDEX, False)
        # It landed on Custom, so show WHERE it landed and let it be
        # adjusted -- which is what makes the sample a start, not a result.
        self.open_wb_pad()

    def _sync_from_editor(self, *_args) -> None:
        index = self._index_of(str(self._load_raw.white_balance.value))
        if index is not None:
            self.wb_strip.set_index(index)
        self._refresh_caption()

    #: `WB_META` — the caption is `name · sub()`, and the sub is what that
    #: illuminant actually IS, not the word 'preset'.
    _WB_SUB = {
        'as_shot': '来自 EXIF',
        'daylight': '5600 K, 1.00',
        'tungsten': '3800 K, 1.00',
    }

    def _refresh_caption(self) -> None:
        current = str(self._load_raw.white_balance.value)
        ZH_WB_NAMES = {
            'as_shot': '相机实拍',
            'daylight': '日光',
            'tungsten': '白炽灯',
            'custom': '自定义',
        }
        label = ZH_WB_NAMES.get(current, current.replace('_', ' '))
        # the group's meta is the name, lowercased (`fileImportMeta`)
        self.wb_summary.setText(label)
        if current == 'custom':
            sub = ('%.0f K, %.2f'
                   % (float(self._load_raw.temperature.value),
                      float(self._load_raw.tint.value)))
        else:
            sub = self._WB_SUB.get(current, label)
        self.wb_caption.setText(f'{label}, {sub}')

    # -------------------------------------------------------------- public
    def set_frame(self, name: str, path: str = '') -> None:
        self.name_label.setText(name or '未载入底片')
        self.path_label.setText(path)
        self.path_label.setVisible(bool(path))

    def set_info(self, dimensions: str = '—', file_format: str = '—',
                 backend: str = '—') -> None:
        self.info.set_value('Dimensions', dimensions)
        self.info.set_value('Format', file_format)
        self.info.set_value('Backend', backend)


class _MirrorsEnabledState:
    """Keeps each mirror button's ENABLED state in step with the original
    it presses: a button you can press that does nothing is worse than a
    disabled one. Qt has no signal for it, hence an event filter. The
    section fills `_mirrors` (id(original) -> mirror) and installs itself
    on each original."""

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API name
        if event.type() == QtCore.QEvent.EnabledChange:
            mirror = self._mirrors.get(id(watched))
            if mirror is not None:
                mirror.setEnabled(watched.isEnabled())
        return super().eventFilter(watched, event)


class RollSectionView(_MirrorsEnabledState, DyeworksSection):
    """`Roll` — the roll this frame belongs to, and what applies to all of it.

    The header carries the frame count, so a collapsed section still says how
    big the roll is.
    """

    def __init__(self, roll) -> None:
        super().__init__('胶卷', meta='未载入', expanded=False)
        self._roll = roll

        self.info = KeyValueTable((
            ('胶卷名称', '—'),
            ('底片张数', '0'),
            ('整卷曝光补偿', '—'),
        ))
        self.add(self.info)

        # `style="border-color:var(--ink-2);font-weight:600"` — the page
        # picks this one out of the four. It is the destructive-ish one (it
        # writes this frame's look over the others), so it should not look
        # like Save roll.
        self.apply_look_button = WideButton('应用此外观至全部')
        self.apply_look_button.setProperty('emphasis', 'strong')
        self.use_exposure_button = WideButton('应用整卷曝光补偿')
        self.open_button = WideButton('打开胶卷…')
        self.save_button = WideButton('保存胶卷')
        self.add(ButtonRows(
            ButtonPair(self.apply_look_button, self.use_exposure_button),
            ButtonPair(self.open_button, self.save_button)))

        self.add(NoteRule(
            "“应用此外观至全部”将当前底片的调色风格复制给所有选中的底片；"
            "“应用整卷曝光补偿”统一设置全卷共享的 EV 曝光档位。"
            "全程无损，胶卷按文件路径引用原始 RAW。"))

        # Press the section's own buttons: they carry the wiring, the guards
        # and the enabled state, so nothing is duplicated here.
        for mine, theirs in (
                (self.apply_look_button, roll.apply_look_button),
                (self.use_exposure_button, roll.apply_roll_exposure_button),
                (self.open_button, roll.open_roll_button),
                (self.save_button, roll.save_roll_button)):
            mine.clicked.connect(theirs.click)
            theirs.installEventFilter(self)
            mine.setEnabled(theirs.isEnabled())
            mine.setToolTip(theirs.toolTip())
        self._mirrors = {
            id(roll.apply_look_button): self.apply_look_button,
            id(roll.apply_roll_exposure_button): self.use_exposure_button,
            id(roll.open_roll_button): self.open_button,
            id(roll.save_roll_button): self.save_button,
        }

    def set_frame_count(self, count: int, culled: int = 0) -> None:
        self.set_meta(f'{count} 张底片' if count else '无胶卷')
        self.info.set_value(
            '底片张数', f'{count} 张 (剔除 {culled} 张)' if culled else str(count))

    def set_roll_name(self, name: str) -> None:
        """`Name` — the page's first kv row, and the only one that says
        WHICH roll is open."""
        self.info.set_value('胶卷名称', name or '—')

    def set_roll_exposure(self, text: str) -> None:
        self.info.set_value('整卷曝光补偿', text)


class PresetsSectionView(_MirrorsEnabledState, DyeworksSection):
    """`Presets` — the saved look, as a chip rather than a dropdown row.

    The design shows the ACTIVE preset the way it shows a film stock: name
    over a caption, with a caret. What it is matters more than the fact that
    it came from a list.
    """

    def __init__(self, gui_config) -> None:
        super().__init__('调色预设', meta='无', expanded=False)
        self._config = gui_config

        # `#preset-row` is a `.select`, not a row: a contact-print chip, the
        # preset's name over the stock and paper it was saved on, and a
        # caret. The combo stays the model and the popup underneath.
        self.picker = gui_config.preset_combo
        self.picker.setVisible(False)
        self.chip = ChipSelect(self.picker, chip='contact',
                               facts=self._preset_facts)
        self.add(self.chip)
        self.add(self.picker)

        self.save_button = WideButton('保存预设…')
        self.delete_button = WideButton('删除预设')
        self.add(ButtonPair(self.save_button, self.delete_button))

        self.save_button.clicked.connect(gui_config.save_preset_button.click)
        self.delete_button.clicked.connect(gui_config.delete_preset_button.click)
        for mine, theirs in ((self.save_button, gui_config.save_preset_button),
                             (self.delete_button, gui_config.delete_preset_button)):
            theirs.installEventFilter(self)
            mine.setEnabled(theirs.isEnabled())
        self._mirrors = {
            id(gui_config.save_preset_button): self.save_button,
            id(gui_config.delete_preset_button): self.delete_button,
        }
        self.picker.currentTextChanged.connect(self._sync_meta)
        # 2026-08-04; see design/DECISIONS.md#d-left_panel_sections-589
        model = self.picker.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            signal.connect(self._queue_refresh)
        # A card changed on the Shelf (gradient, palette card, reset) is read
        # by the chip and every row of the open menu; the list itself did not
        # change, so no model signal says so.
        from spektrafilm_gui import card_store

        card_store.subscribe(self._on_card_changed)
        self._refresh_from_model()

    def _on_card_changed(self) -> None:
        self._queue_refresh()

    def _queue_refresh(self, *_args) -> None:
        """Refresh AFTER the rebuild, not during it.

        `refresh_presets` clears, re-adds every name and only THEN sets the
        current index -- so a handler on the model's own signals runs while
        the list is half-built and reads the placeholder. Deferring to the
        next turn also collapses one signal per item into one refresh.
        """
        if getattr(self, '_refresh_queued', False):
            return
        self._refresh_queued = True
        QtCore.QTimer.singleShot(0, self._refresh_from_model)

    def _refresh_from_model(self, *_args) -> None:
        self._refresh_queued = False
        self._refresh_item_chips()
        self.chip.refresh()
        self._sync_meta()

    def _refresh_item_chips(self, *_args) -> None:
        for index in range(self.picker.count()):
            # index 0 is the '(preset)' placeholder -- not a preset, so it
            # has no contact print to show
            if index == 0:
                self.picker.setItemIcon(index, QtGui.QIcon())
                continue
            gradient, _caption = self._preset_facts(self.picker.itemText(index))
            self.picker.setItemIcon(index, QtGui.QIcon(
                contact_chip_pixmap(gradient, CONTACT_CHIP_MENU_PX)))

    @staticmethod
    def _preset_facts(name: str) -> tuple[str, str]:
        """(the stock's own chip art, 'stock · paper') for the caption.

        Both come from the preset's CLEAR metadata block, which
        `save_preset` already writes -- the page's own
        'portra 400 · supra endura'. Nothing is guessed: a preset with no
        recorded stock simply has no art and no caption.
        """

        facts = preset_facts(name)
        stock, paper = facts.get('film_stock', ''), facts.get('print_paper', '')
        caption = ' on '.join(option_label(slug, slug).lower()
                             for slug in (stock, paper) if slug)
        # THE SHARED RESOLVER (2026-09-03). This used to inline
        # `swatch or stock`, and the Shelf's card resolved something else
        # entirely, so a preset looked like two different things depending on
        # where you saw it. `preset_art` is now the single answer and the
        # card is a wide version of exactly this.
        from spektrafilm_gui.preset_chips import preset_art

        return preset_art(name), caption

    def _sync_meta(self, *_args) -> None:
        current = self._config.current_preset()
        self.set_meta(current or '无')


class VersionsSectionView(DyeworksSection):
    """`Versions` — every attempt, kept forever.

    2026-10-07; see design/DECISIONS.md#d-left_panel_sections-605
    """

    def __init__(self, roll) -> None:
        super().__init__('历史版本', meta='无', expanded=False)
        self._roll = roll

        # `.attempts` — three-column rows on no card of their own. The
        # section's QListWidget stays alive as the MODEL (the controller
        # still feeds it through `set_attempts`, and its context menu and
        # selection API are unchanged); this view reads it and draws the
        # page's rows. Re-homing the list itself put a bordered, filled well
        # where the design has nothing at all (NAT 2026-08-03).
        self.source = roll.attempts_list
        self.attempts = AttemptList()
        self.attempts.selected.connect(self._on_selected)
        self.attempts.context_requested.connect(self._on_context_menu)
        self.add(self.attempts)

        self.keep_button = WideButton('保留版本')
        self.keep_button.setShortcutHint('K')
        self.keep_button.setToolTip(roll.keep_attempt_button.toolTip())
        self.remove_button = WideButton('删除')
        self.remove_button.setToolTip(roll.remove_attempt_button.toolTip())
        # 2026-08-04; see design/DECISIONS.md#d-left_panel_sections-590
        self.add(ButtonPair(self.keep_button, self.remove_button))
        self.keep_button.clicked.connect(self._on_keep)
        self.remove_button.clicked.connect(self._on_remove)

        self.add(NoteRule(
            '所有调色版本均会被完整保留，绝不覆写。每次执行“预览”、“扫描渲染”或采纳试条时都会自动捕捉生成新版本。'))

        model = self.source.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset,
                       model.dataChanged):
            signal.connect(self._rebuild)
        self._rebuild()

    # ------------------------------------------------------------- internals
    def _on_selected(self, attempt_id: str) -> None:
        """Selecting a row selects it in the model too, so the section's own
        buttons and its rename menu act on what is highlighted here."""
        for index in range(self.source.count()):
            item = self.source.item(index)
            if str(item.data(Qt.UserRole) or '') == attempt_id:
                self.source.setCurrentItem(item)
                break
        self.source.itemClicked.emit(self.source.currentItem())

    def _on_context_menu(self, _attempt_id: str, point) -> None:
        self.source.customContextMenuRequested.emit(
            self.source.mapFromGlobal(self.attempts.mapToGlobal(point)))

    def _on_keep(self) -> None:
        self._roll.keep_attempt_button.click()

    def _on_remove(self) -> None:
        self._roll.remove_attempt_button.click()

    def _rebuild(self, *_args) -> None:
        entries = []
        try:
            count = self.source.count()
        except RuntimeError:
            # The model outlives its signals by a moment on teardown; a view
            # rebuilding from a deleted list is not an error, it is nothing
            # left to show.
            return
        for index in range(count):
            item = self.source.item(index)
            label, note = _split_version_label(item.text())
            entries.append({
                'id': str(item.data(Qt.UserRole) or ''),
                'label': label,
                'note': note,
                'verdict': str(item.data(VERDICT_ROLE) or 'live'),
            })
        # the version in use: the one selected (clicking reloads it), else
        # the newest -- the model's own selection is left alone, because
        # Keep with nothing selected stores the current look as new
        current = self.source.currentItem()
        current_id = str(current.data(Qt.UserRole) or '') if current is not None else ''
        self.attempts.set_entries(entries, current_id)
        count = len(entries)
        self.set_meta(f'{count} 个版本' if count else '无')


def _split_version_label(text: str) -> tuple[str, str]:
    """`V3 · 12.5 s` + `Y68 M42` — the page's id and its note.

    The stored label is one string, auto-built as `A3 - <what changed>` and
    then user-editable. The 'A' is the internal ATTEMPT vocabulary; the UI
    says version, so it reads as V (NAT's version-function decision, and the
    page's own `V1 … V4`). A renamed label with no separator is all id, which
    is the honest reading of a name someone typed.
    """
    label = str(text or '')
    head, separator, tail = label.partition(' - ')
    if not separator:
        head, tail = label, ''
    # case-insensitive: the section runs every label through
    # `_normalize_ui_text`, which lower-cased until 20.2, so a stored text
    # may be 'a3' as well as 'A3'.
    if head[:1] in 'aA' and head[1:2].isdigit():
        head = 'V' + head[1:]
    return head.strip(), tail.strip()
