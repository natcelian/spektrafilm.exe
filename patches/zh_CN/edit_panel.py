"""Build the edit panel from the spec (Phase 18F).

Walks `edit_panel_spec.STAGES` and assembles the design's right panel:
groups as `DyeworksSection`s whose headers carry a live value summary, and rows
as `LabelledRow`s -- label left, value right, control on its own line.

THE EDITORS ARE THE APP'S. A row does not build a control; it FINDS the one
the section already owns (`widgets.grain.amount`) and re-homes it. So
`.value`, `valueChanged`, the state bridge, profile sync, undo, presets and
persistence all keep working against the same objects, and this module can
be thrown away without taking any of that with it.

Two deliberate refusals:

  - A row naming a field the app does not have is REPORTED, not skipped
    silently and not crashed on. `missing_rows()` returns them, and a test
    asserts the list is empty; a spec/app divergence has to be visible.

  - A Special row whose widget does not exist yet renders as nothing and is
    reported the same way, rather than as a placeholder box that looks like
    a real control.
"""

from __future__ import annotations

from typing import Callable

import weakref as _weakref

from qtpy import QtCore, QtWidgets

from spektrafilm_gui import card_store, edit_panel_spec as spec, film_formats, library_menus
from spektrafilm_gui import field_labels
from spektrafilm_gui import theme_palette as palette
from spektrafilm_gui import row_menu
from spektrafilm_gui.dyeworks_widgets import DyeworksSection, hug_chip
from spektrafilm_gui.dyeworks_buttons import (ToggleSwitch, ButtonPair, IconStrip, SegmentedEnum,
                                              WideButton)
from spektrafilm_gui.dyeworks_chips import (as_chip, ChipSelect, GateSelect, MethodSelect,
                                            decorate_menu_items)
from spektrafilm_gui.dyeworks_editors import (KnobRow, EDIT_COLUMNS, SIDE_COLUMNS,
                                              double_click_resets, on_editor_changed,
                                              FilterHeadRow, HueSatRow, LevelsRow, MiniSlider)
from spektrafilm_gui.dyeworks_labels import Hint, LabelledRow, NoteRule, Disclosure
from spektrafilm_gui.dyeworks_pads import CropPad, SpectrumBar, XYPad
from spektrafilm_gui.theme_tokens import TYPE_CAPTION
from spektrafilm_gui.dyeworks_panels import FX_POPOVER_WIDTH, FxPreviewPanel, prewarm_fx_simulator
from spektrafilm_gui.dyeworks_colour_controls import CouplerMatrix, ToningWheel
from spektrafilm.model.color_filters import (
    COLOR_FILTER_LIBRARY,
    ND_DENSITIES,
    nd_filter_density,
    nd_filter_key,
)
from spektrafilm_gui.library_popover import LibraryPopover
from spektrafilm_gui.library_index import glass_for
from spektrafilm_gui.dyeworks_popover import CompositeButton, Popover, dye_hue_colour
from spektrafilm_gui.curve_widget import CurveEditorView
from spektrafilm_gui.edit_row_helpers import (_WIDGET_ROW_LABELS, _alive, _apply_saturation_ramp,
                                              _baseline_of, _decimals_for, _dye_row,
                                              _ends_with_note, _fit_value_box, _is_signed,
                                              _label_for, _mini_cell_grid, _row_owners,
                                              _scalar_slider_stack, _typed_bound, _unit_for,
                                              _widget_spec)

Qt = QtCore.Qt

#: Live panels, so the controller can hand one a render without the panel
#: having to live in the WidgetBundle -- which is a slots dataclass that
#: several subsystems walk field by field, and is the wrong place for a
#: view. Weak, and matched by BUNDLE identity, so a second window feeds
#: only its own panel.
_LIVE_PANELS: list = []


def feed_preview_image(widgets, image) -> None:
    """Give every live panel built over `widgets` the current render."""
    alive = []
    for reference in _LIVE_PANELS:
        panel = reference()
        if panel is None:
            continue
        alive.append(reference)
        if panel._widgets is widgets:
            try:
                panel.set_preview_image(image)
            except RuntimeError:
                pass          # the C++ side went away between the two lines
    _LIVE_PANELS[:] = alive


#: Rows the design shows that the app keeps on a TOOL rather than in state.
#: `spec.WIDGET_ROWS` says they resolve to no field; this says where the
#: control actually lives, so the row is placed rather than dropped.
_WIDGET_ROW_EDITORS = {
    'scan_finishing.crop_ratio': ('scan_frame', 'ratio_combo'),
}

#: Labels for the widget-only rows, which have no `WidgetSpec` to read one
#: from. Taken from the design page.
#: Rows the design shows as a named choice where the app stores a tuple.
#: `{seg:1, cols:5}` — the only segmented enum the page wraps, because ten
#: aspect ratios do not fit on one line. Everything else is a single row.
_SEG_COLUMNS = {'scan_finishing.crop_ratio': 5}

_PERMUTATION_ROWS = frozenset({'special.film_channel_swap',
                               'special.print_channel_swap'})

#: `.advbody.is-open { gap: 12px }` -- the same rhythm as `.gbody`.
DISCLOSURE_ROW_GAP = 12

# 2026-08-02; see design/DECISIONS.md#d-edit_panel-480
SAT_RAMP_FIELDS = frozenset({
    'grain.saturation',
    'couplers.inhibition_interlayer',
    'scan_finishing.saturation',
    'scan_finishing.vibrance',
})


# 2026-09-24; see design/DECISIONS.md#d-edit_panel-481
DYE_POPOVER_ROWS = (('c', 'Cyan', palette.CHANNEL_C),
                    ('m', 'Magenta', palette.CHANNEL_M),
                    ('y', 'Yellow', palette.CHANNEL_Y))


def _differs_from_baseline(editor) -> bool:
    """Whether an editor's value has left its baseline: numbers within a
    hair, tuples component by component, anything else by equality. An
    editor with no baseline (never synced) reads as unchanged."""
    baseline = getattr(editor, 'baseline', None)
    if not callable(baseline):
        return False
    try:
        reference, current = baseline(), editor.value
    except Exception:
        return False
    if reference is None:
        return False
    try:
        if isinstance(reference, (tuple, list)):
            return any(abs(float(a) - float(b)) > 1e-9 for a, b in zip(current, reference))
        if isinstance(reference, (int, float)) and not isinstance(reference, bool):
            return abs(float(current) - float(reference)) > 1e-9
    except (TypeError, ValueError):
        pass
    return current != reference


def _reset_editors(editors):
    """The reset of a row over several editors: each back to its baseline."""
    def reset() -> None:
        for editor in editors:
            restore = getattr(editor, 'reset_to_baseline', None)
            if callable(restore):
                restore()
    return reset


def _wire_reset(row, editors) -> None:
    """20.3 (NAT 2026-10-07, the gaps): a choice row, a mini-slider row and a
    composite row carry the reset the page gives them, lit while any of
    their editors has left its baseline -- which is also what their group
    counts."""
    def refresh(*_args) -> None:
        row.set_modified(any(_differs_from_baseline(e) for e in editors))
    for editor in editors:
        # a combo box has no `valueChanged`, which is all `on_editor_changed` hears
        if not on_editor_changed(editor, refresh):
            text_changed = getattr(editor, 'currentTextChanged', None)
            if text_changed is not None:
                text_changed.connect(refresh)
    refresh()


class _MenuMixin:
    """Right-click opens the row menu, wherever the row came from."""

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API name
        panel = self._owning_panel()
        menu = row_menu.RowContextMenu(
            self.row, self._editor,
            section=self._owning_section(),
            on_pin_changed=(panel.refresh_pins if panel is not None else None),
            # Phase 21F: the panel carries the controller's apply, because a
            # row has no business knowing there is a controller. Same seam
            # `refresh_pins` already uses.
            apply_to_all=(getattr(panel, 'apply_to_all_callback', None)
                          if panel is not None else None),
            frame_count=(panel.roll_frame_count()
                         if panel is not None else 0),
            parent=self)
        menu.exec(event.globalPos())

    def _owning_panel(self):
        widget = self.parent()
        while widget is not None and not isinstance(widget, EditPanel):
            widget = widget.parent()
        return widget

    def _owning_section(self):
        widget = self.parent()
        while widget is not None and not isinstance(widget, DyeworksSection):
            widget = widget.parent()
        return widget


# 2026-10-07; see design/DECISIONS.md#d-edit_panel-485
DYE_POPOVER_WIDTH = 307
TONE_POPOVER_WIDTH = 333

#: How far a row fades when its control does not apply to the selected film.
UNAVAILABLE_OPACITY = 0.35


class EditRow(_MenuMixin, LabelledRow):
    """One parameter, in the design's shape, driving the app's own editor."""

    def __init__(self, row: str, editor: QtWidgets.QWidget,
                 on_changed: Callable[[], None] | None = None, *,
                 columns: tuple[int, int] | None = EDIT_COLUMNS) -> None:
        self.row = row
        self._decimals = _decimals_for(row)
        self._signed = _is_signed(row)
        self._unit = _unit_for(row)
        control: QtWidgets.QWidget | None = editor
        self._toggle: ToggleSwitch | None = None

        # A boolean is a SWITCH in this design, wherever it appears. The
        # app's checkbox stays the editor; the switch drives it and follows.
        if hasattr(editor, 'toggled') and hasattr(editor, 'setChecked'):
            self._toggle = ToggleSwitch()
            control = None

        # A slider row shows its number ONCE, at the right of the label, with
        # a bare track underneath. The composite editor's own spin box and
        # reset are hidden rather than removed -- the spin box is still the
        # authoritative value the slider maps onto, and the reset still
        # carries the baseline logic; this row just drives them.
        self._inner_spin = getattr(editor, '_inner', None)
        self._inner_reset = getattr(editor, '_reset', None)
        reset_callback = None
        if self._inner_spin is not None and self._inner_reset is not None:
            self._inner_spin.setVisible(False)
            self._inner_reset.setVisible(False)
            reset_callback = getattr(editor, 'reset_to_baseline', None)

        # `.enum` — a dropdown sits INLINE at the right of the label, as a
        # chip, not on a line of its own. Only the value belongs on the
        # label's line, and for an enum the control IS the value.
        self._inline_enum = isinstance(editor, QtWidgets.QComboBox)
        if self._inline_enum:
            control = None

        # 20.3: a SLIDER row is one line (label | slider | value); a switch or
        # an enum row already is, with its control in the value's place
        slider_row = reset_callback is not None and control is not None
        super().__init__(_label_for(row), control, reset=reset_callback,
                         columns=columns if slider_row else None)
        self._editor = editor
        if self._inline_enum:
            as_chip(editor)
            hug_chip(editor)
            head = self.layout().itemAt(0).widget().layout()
            head.insertWidget(head.count() - 1, editor)
            self.value.setVisible(False)
        if self._toggle is not None:
            head = self.layout().itemAt(0).widget().layout()
            head.insertWidget(head.count() - 1, self._toggle)
            self.value.setVisible(False)
            self._toggle.toggled.connect(self._push_bool)
            editor.toggled.connect(self._pull_bool)
            self._pull_bool()
            # 2026-09-20; see design/DECISIONS.md#d-edit_panel-486
            if isinstance(editor, QtWidgets.QWidget):
                editor.installEventFilter(self)
                self._mirror_enabled(editor)

        if not self.is_inline():          # a one-line row's value column is fixed
            _fit_value_box(self.value, row)
        # 2026-08-05; see design/DECISIONS.md#d-edit_panel-487
        if (self._toggle is None and not self._inline_enum
                and self._inner_spin is not None):
            low, high = self._typed_range()
            self.set_value_input(self._apply_typed, minimum=low, maximum=high)
        self._on_changed = on_changed
        for name in ('valueChanged', 'currentTextChanged', 'toggled'):
            signal = getattr(editor, name, None)
            if signal is not None and hasattr(signal, 'connect'):
                signal.connect(self._refresh)
                break
        self._refresh()

    # ---------------------------------------------------------- typed value
    def _typed_range(self) -> tuple[float | None, float | None]:
        """The field's own bounds -- the SPIN BOX's, which is where the
        science's limits already live."""
        box = self._inner_spin
        low, high = box.minimum(), box.maximum()
        return (_typed_bound(low), _typed_bound(high))

    def _apply_typed(self, value: float) -> None:
        """A typed number goes in through the editor, exactly as a drag
        does -- so profile sync, undo, presets and the render all see one
        kind of change."""
        box = self._inner_spin
        self._editor.value = (int(round(value))
                              if isinstance(box, QtWidgets.QSpinBox)
                              else float(value))

    # ------------------------------------------------------------- booleans
    def eventFilter(self, watched, event):  # noqa: N802 - Qt API name
        if (watched is self._editor and self._toggle is not None
                and event.type() in (QtCore.QEvent.EnabledChange,
                                     QtCore.QEvent.ToolTipChange)):
            self._mirror_enabled(watched)
        return super().eventFilter(watched, event)

    def _mirror_enabled(self, editor) -> None:
        enabled = editor.isEnabled()
        self._toggle.setEnabled(enabled)
        self._toggle.setToolTip(editor.toolTip())
        self._dim(not enabled)

    def _dim(self, dimmed: bool) -> None:
        """GREYED OUT has to read at a glance. A disabled switch that is off
        paints within a few levels of an enabled one that is off (measured
        2026-09-20), so the whole row fades instead -- name and control."""
        if not dimmed:
            self.setGraphicsEffect(None)
            return
        effect = QtWidgets.QGraphicsOpacityEffect(self)
        effect.setOpacity(UNAVAILABLE_OPACITY)
        self.setGraphicsEffect(effect)

    def _push_bool(self, checked: bool) -> None:
        if bool(self._editor.value) != bool(checked):
            self._editor.value = bool(checked)

    def _pull_bool(self, *_args) -> None:
        checked = bool(self._editor.value)
        was = self._toggle.blockSignals(True)
        try:
            self._toggle.setChecked(checked)
        finally:
            self._toggle.blockSignals(was)

    # -------------------------------------------------------------- display
    def _refresh(self, *_args) -> None:
        if self._toggle is None:
            self.set_value_text(self._format())
        self.set_modified(self._is_modified())
        if self._on_changed is not None:
            self._on_changed()

    def _is_modified(self) -> bool:
        """Whether the value has moved off its baseline.

        The page hides the reset until then (`.reset { visibility: hidden }`,
        shown only under `.is-modified`), and Dyeworks does the same. The
        baseline is the editor's own -- last profile sync for a synced
        field, project default otherwise -- so this asks rather than
        deciding.
        """
        baseline = getattr(self._editor, 'baseline', None)
        if not callable(baseline):
            return False
        try:
            reference = baseline()
            if reference is None:
                return False
            return self._editor.value != reference
        except Exception:
            return False

    def _format(self) -> str:
        try:
            value = self._editor.value
        except Exception:
            return ''
        if isinstance(value, str):
            return value
        try:
            return spec.fmt_one(value, decimals=self._decimals,
                                signed=self._signed) + self._unit
        except (TypeError, ValueError):
            return str(value)


class MirrorRow(_MenuMixin, LabelledRow):
    """A row that DRIVES an editor living somewhere else.

    The design puts eight fields on two stages at once -- exposure
    compensation is on MAIN and on FILM -- and Pin to Main adds more on
    demand. A widget can only have one parent, so a second `EditRow` would
    steal the control from the first and leave a label with nothing under
    it. This builds its own control instead and binds it to the editor both
    ways, so the editor stays the single source of truth and the state
    bridge, profile sync, undo and presets keep seeing exactly one value.

    Floats, booleans and enums only -- the three shapes the duplicated rows
    take. Anything else falls back to showing the value, read-only, rather
    than to a control that half works.
    """

    #: how many slider positions a float mirror gets when the spec has no
    #: step to follow
    STEPS = 1000

    def __init__(self, row: str, editor: QtWidgets.QWidget,
                 on_changed: Callable[[], None] | None = None) -> None:
        self.row = row
        self._editor = editor
        self._decimals = _decimals_for(row)
        self._signed = _is_signed(row)
        self._unit = _unit_for(row)
        self._syncing = False
        self._kind = self._classify(editor)

        control: QtWidgets.QWidget | None = None
        inline: QtWidgets.QWidget | None = None
        if self._kind == 'bool':
            inline = ToggleSwitch()
        elif self._kind == 'enum':
            inline = QtWidgets.QComboBox()
            for index in range(editor.count()):
                inline.addItem(editor.itemText(index))
        elif self._kind == 'float':
            from spektrafilm_gui.dyeworks_labels import SLIDER_ROW_PX

            control = QtWidgets.QSlider(Qt.Horizontal)
            control.setFixedHeight(SLIDER_ROW_PX)     # `.slider` is 14px
            low, high = self._range()
            control.setRange(0, self.STEPS)
            # 2026-08-01; see design/DECISIONS.md#d-edit_panel-488
            from spektrafilm_gui.precision import PrecisionSliderFilter

            PrecisionSliderFilter(control)

        reset = getattr(editor, 'reset_to_baseline', None)
        super().__init__(_label_for(row), control,
                         reset=reset if callable(reset) else None,
                         columns=EDIT_COLUMNS if self._kind == 'float' else None)
        self.control = control
        self.inline = inline

        if inline is not None:
            head = self.layout().itemAt(0).widget().layout()
            head.insertWidget(head.count() - 1, inline)
            if self._kind == 'enum':
                as_chip(inline)
                self.value.setVisible(False)
                inline.currentTextChanged.connect(self._push)
            else:
                self.value.setVisible(False)
                inline.toggled.connect(self._push)
        if control is not None:
            control.valueChanged.connect(self._push)
            # Phase 21E on MAIN too: its own slider, so its own gesture
            double_click_resets(control, reset)

        if not self.is_inline():
            _fit_value_box(self.value, row)
        if self._kind == 'float':
            low, high = self._range()
            self.set_value_input(self._apply_typed, minimum=low, maximum=high)
        self._on_changed = on_changed
        for name in ('valueChanged', 'currentTextChanged', 'toggled'):
            signal = getattr(editor, name, None)
            if signal is not None and hasattr(signal, 'connect'):
                signal.connect(self._pull)
                break
        self._pull()

    def _apply_typed(self, value: float) -> None:
        """Straight into the editor -- NOT through this row's own slider,
        which has only 1000 positions and would round the number you typed."""
        self._editor.value = float(value)
        if self._on_changed is not None:
            self._on_changed()

    # ------------------------------------------------------------- plumbing
    @staticmethod
    def _classify(editor) -> str:
        if isinstance(editor, QtWidgets.QComboBox):
            return 'enum'
        if hasattr(editor, 'toggled') and hasattr(editor, 'setChecked'):
            return 'bool'
        if hasattr(editor, 'valueChanged'):
            return 'float'
        return 'text'

    def _range(self) -> tuple[float, float]:
        entry = _widget_spec(self.row)
        low = getattr(entry, 'min_value', None) if entry else None
        high = getattr(entry, 'max_value', None) if entry else None
        inner = getattr(self._editor, '_inner', None)
        if low is None and inner is not None:
            low = inner.minimum()
        if high is None and inner is not None:
            high = inner.maximum()
        low = 0.0 if low is None else float(low)
        high = 1.0 if high is None else float(high)
        return (low, high) if high > low else (low, low + 1.0)

    def _push(self, *_args) -> None:
        if self._syncing:
            return
        try:
            if self._kind == 'bool':
                self._editor.value = bool(self.inline.isChecked())
            elif self._kind == 'enum':
                self._editor.value = self.inline.currentText()
            elif self._kind == 'float':
                low, high = self._range()
                fraction = self.control.value() / self.STEPS
                self._editor.value = low + fraction * (high - low)
        except (AttributeError, TypeError, ValueError):
            return
        if self._on_changed is not None:
            self._on_changed()

    def _pull(self, *_args) -> None:
        self._syncing = True
        try:
            value = self._editor.value
            if self._kind == 'bool':
                self.inline.setChecked(bool(value))
            elif self._kind == 'enum':
                self.inline.setCurrentText(str(value))
            elif self._kind == 'float':
                low, high = self._range()
                fraction = (float(value) - low) / (high - low)
                self.control.setValue(
                    int(round(max(0.0, min(1.0, fraction)) * self.STEPS)))
                self.set_value_text(spec.fmt_one(
                    float(value), decimals=self._decimals,
                    signed=self._signed) + self._unit)
            else:
                self.set_value_text(str(value))
            self.set_modified(self._is_modified())
        except (AttributeError, TypeError, ValueError):
            pass
        finally:
            self._syncing = False

    def _is_modified(self) -> bool:
        baseline = getattr(self._editor, 'baseline', None)
        if not callable(baseline):
            return False
        try:
            reference = baseline()
            return reference is not None and self._editor.value != reference
        except Exception:
            return False


class EditPanel(QtWidgets.QWidget):
    """The five stage tabs, built from the spec."""

    def __init__(self, widgets) -> None:
        super().__init__()
        self.setObjectName('editPanel')
        self._widgets = widgets
        #: rows the spec names that could not be built, by reason
        self.missing: dict[str, list[str]] = {'field': [], 'special': []}
        self._groups: list[tuple[spec.Group, DyeworksSection]] = []
        #: levels plots, which need the render to draw against
        self._levels_rows: list = []
        #: crop pads, so the controller can hand them the loaded frame
        self._crop_pads: list = []
        #: one popover per kind, kept so it holds its position
        self._popovers: dict = {}
        #: live FX plates, refreshed when any parameter moves
        self._fx_panels: list = []
        self._frame_shape_provider = None
        _LIVE_PANELS.append(_weakref.ref(self))

        # 2026-08-02; see design/DECISIONS.md#d-edit_panel-489
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QtWidgets.QVBoxLayout(self)
        # `.stagewrap { padding: 7px 15px }` — the stage box is inset from
        # BOTH panel edges by the panel's own gutter. Set on the layout
        # because a `margin` on a QTabBar inside a QTabWidget is ignored.
        from spektrafilm_gui.theme_styles import STAGE_STRIP_INSET

        # 2026-09-03; see design/DECISIONS.md#d-edit_panel-490
        from spektrafilm_gui.dyeworks_labels import VIEW_SELECTOR_TOP

        # 2026-09-10, 2026-09-29; see design/DECISIONS.md#d-edit_panel-491
        layout.setContentsMargins(STAGE_STRIP_INSET, VIEW_SELECTOR_TOP, 0, 0)
        layout.setSpacing(0)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName('controlsTabWidget')
        # `.stages` paints its own well -- Qt will not draw a stylesheet
        # background on a QTabBar. Set BEFORE any tab is added.
        from spektrafilm_gui.dyeworks_buttons import StageTabBar

        self.tabs.setTabBar(StageTabBar())
        # 2026-08-02; see design/DECISIONS.md#d-edit_panel-492
        self.tabs.setDocumentMode(False)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.tabBar().setDrawBase(False)
        # 2026-08-02; see design/DECISIONS.md#d-edit_panel-493
        self.tabs.tabBar().setExpanding(True)
        # 2026-08-05; see design/DECISIONS.md#d-edit_panel-494
        self.tabs.tabBar().setElideMode(Qt.ElideRight)
        # 2026-08-02; see design/DECISIONS.md#d-edit_panel-495
        self._owner = _row_owners()
        self._stage_key = ''
        #: MAIN's pinned shortlist -- built empty, filled by refresh_pins
        self._pinned_section: DyeworksSection | None = None
        ZH_STAGE_LABELS = {
            'Main': '主控',
            'Film': '底片',
            'Print': '印相',
            'Scan': '扫描',
            'Adv': '高级',
        }
        for stage in spec.STAGES:
            self._stage_key = stage.key
            stage_title = ZH_STAGE_LABELS.get(stage.label, stage.label)
            self.tabs.addTab(self._build_stage(stage), stage_title)
        self.refresh_pins()
        layout.addWidget(self.tabs, 1)
        # 2026-08-02; see design/DECISIONS.md#d-edit_panel-496
        self._wire_applicability()
        self.refresh_summaries()

    # ---------------------------------------------------------------- build
    def _widget_only_control(self, row: str) -> QtWidgets.QWidget | None:
        where = _WIDGET_ROW_EDITORS.get(row)
        if where is None:
            return None
        return getattr(getattr(self._widgets, where[0], None), where[1], None)

    def _composite_row(self, name: str, targets):
        """One row over SEVERAL scalar editors, as the page declares it.

        `RGB(...)` is a knob triplet; `T(...)` is stacked mini sliders. The
        app already had the machinery for the first -- `LinkedKnobRow` names
        'chemistry RGB gammas' in its own docstring -- and this panel simply
        never reached for it, which is the same re-homing gap that cost the
        reset convention and the applicability pass.
        """
        editors = [self._editor_for(target) for target in targets]
        if any(editor is None for editor in editors):
            self.missing['field'].extend(
                target for target, editor in zip(targets, editors)
                if editor is None)
            return None

        if name in field_labels.TUPLE_LABELS:
            captions = field_labels.TUPLE_LABELS[name].split(',')
            row = LabelledRow(_label_for(name), reset=_reset_editors(editors))
            row.row = name
            row.value.setVisible(False)
            row.add_control(
                _scalar_slider_stack(editors, captions, row,
                                     self.refresh_summaries,
                                     full_rows=EDIT_COLUMNS))   # 20.3
            _wire_reset(row, editors)
            return row

        # RGB: a knob per channel, writing through to the scalar editors,
        # which stay the model exactly as they do everywhere else.
        from spektrafilm_gui.widget_slider import CHANNEL_TINTS, LinkedKnobRow

        units = []
        for index, editor in enumerate(editors):
            box = getattr(editor, '_inner', editor)
            units.append((editor, CHANNEL_TINTS[index % len(CHANNEL_TINTS)],
                          None,
                          float(box.minimum()), float(box.maximum()),
                          float(box.singleStep()), int(box.decimals())))
        linked = LinkedKnobRow(units)
        # LinkedKnobRow has no signal of its own -- it WRITES THROUGH to the
        # scalar editors, so they are what the summaries listen to.
        for editor in editors:
            on_editor_changed(editor, self.refresh_summaries)
        return KnobRow(_label_for(name), linked, name)

    def _tuple_row(self, name: str, editor) -> LabelledRow:
        """`rowTuple` — one line per component, each draggable.

        Ranges, steps and decimals come from the EDITOR's own spin boxes,
        not from the page: those are the science's business and
        `widget_specs.py` stays their source. Only the captions and the
        shape are the page's.
        """
        captions = field_labels.TUPLE_LABELS[name].split(',')
        boxes = list(editor._editors)
        specs = []
        for index, box in enumerate(boxes):
            caption = captions[index] if index < len(captions) else str(index + 1)
            specs.append((caption, index, float(box.minimum()),
                          float(box.maximum()), float(box.singleStep()),
                          int(box.decimals())))
        row = LabelledRow(_label_for(name), reset=_reset_editors([editor]))
        row.row = name
        row.value.setVisible(False)
        editor.setParent(row)
        editor.hide()
        _wire_reset(row, [editor])
        # `grid-template-columns: 34px 1fr 52px` — the tuple row's value
        # column is wider than the filter cells' 30, because these carry
        # units ('30.0 nm') where those carry a bare amplitude.
        grid = _mini_cell_grid(editor, specs, columns=1, value_width=52,
                               value_px=11,      # `vv.style.fontSize='11px'`
                               on_changed=self.refresh_summaries,
                               row_name=name, full_rows=EDIT_COLUMNS)   # 20.3
        row.add_control(grid)
        return row

    @staticmethod
    def _stacked_row(label: str, control: QtWidgets.QWidget, editor=None) -> LabelledRow:
        """A row whose control sits BELOW the label, as `.segrow` does.

        2026-08-03; see design/DECISIONS.md#d-edit_panel-517
        """
        # 2026-08-03, 2026-10-07; see design/DECISIONS.md#d-edit_panel-497
        reset = _reset_editors([editor]) if editor is not None else None
        if len(control.findChildren(QtWidgets.QPushButton)) <= 2:
            row = LabelledRow(label, control, columns=EDIT_COLUMNS, reset=reset)
            row.value.setVisible(False)
        else:
            row = LabelledRow(label, control, reset=reset)
        if editor is not None:
            _wire_reset(row, [editor])
        return row

    @staticmethod
    def _inline_row(label: str, control: QtWidgets.QWidget,
                    name: str = '') -> LabelledRow:
        """A row whose control sits ON the label's line, as an enum does.

        `name` is the field it edits. Rows that omitted it were invisible to
        applicability and to every by-name lookup -- the channel swaps and
        the crop ratio, on top of the four grain rows that gap already cost.
        """
        row = LabelledRow(label)
        row.row = name
        as_chip(control)
        head = row.layout().itemAt(0).widget().layout()
        head.insertWidget(head.count() - 1, control)
        row.value.setVisible(False)
        return row

    # ---- Phase 21F: apply-to-all, handed in rather than reached for -----
    #: Set by `app.build_main_window_for_app`, which is the one place that
    #: holds both this panel and the controller. None in a bare panel (every
    #: test that builds one without an app), and the menu greys out.
    apply_to_all_callback = None
    _frame_count_callback = None

    def set_frame_shape_provider(self, provider) -> None:
        """() -> the loaded frame's (h, w): the FX preview renders its test
        target at THAT frame's pixel pitch, so the film format and the
        photo's own size move the halo the way they do in the render
        (2026-09-20)."""
        self._frame_shape_provider = provider if callable(provider) else None
        for panel in self._fx_panels:
            panel.plate.frame_shape_provider = self._frame_shape_provider

    def prewarm_fx_previews(self) -> None:
        """Build the FX preview's pipeline before anyone opens the popover
        (2026-09-20). The controller calls this once the app has rendered:
        ~300 ms that would otherwise be the first popover's opening pause."""

        try:
            prewarm_fx_simulator(self._widgets, self._frame_shape_provider)
        except Exception:
            pass                                # a preview is never worth a crash

    def set_apply_to_all(self, callback, frame_count=None) -> None:
        self.apply_to_all_callback = callback if callable(callback) else None
        self._frame_count_callback = (
            frame_count if callable(frame_count) else None)

    def roll_frame_count(self) -> int:
        if self._frame_count_callback is None:
            return 0
        try:
            return int(self._frame_count_callback())
        except Exception:
            return 0

    def _editor_for(self, row: str) -> QtWidgets.QWidget | None:
        section_name, _, field = row.partition('.')
        section = getattr(self._widgets, section_name, None)
        if section is None:
            return None
        return getattr(section, field, None)

    def _build_stage(self, stage: spec.Stage) -> QtWidgets.QWidget:
        body = QtWidgets.QWidget()
        # 2026-08-02; see design/DECISIONS.md#d-edit_panel-498
        from spektrafilm_gui.dyeworks_labels import paint_as_panel

        paint_as_panel(body)
        body_layout = QtWidgets.QVBoxLayout(body)
        # 2026-08-26; see design/DECISIONS.md#d-edit_panel-499
        from spektrafilm_gui.theme_styles import PANEL_BODY_RIGHT_PX

        # 8, not 12: strip bottom to the first title is 28 on the page (item 18)
        body_layout.setContentsMargins(0, 8, PANEL_BODY_RIGHT_PX, 0)
        body_layout.setSpacing(0)
        body_layout.setAlignment(Qt.AlignTop)

        for group in stage.groups:
            if not group.name and group.note:
                # a bare note closing the stage
                note = Hint(group.note)
                note.setContentsMargins(15, 14, 15, 15)
                body_layout.addWidget(note)
                continue
            ZH_GROUP_NAMES = {
                'Film': '胶片模拟',
                'Camera filtration': '拍摄滤镜',
                'Input': '输入图像',
                'Enlarger': '放大机',
                'Scan': '扫描成片',
                'Grain': '胶片颗粒',
                'Emulsion': '乳剂特性',
                'Camera': '机身参数',
                'Push / pull': '迫冲 / 减冲',
                'Chemistry': '显影化学',
                'Base': '片基色调',
                'Aging': '胶片老化',
                'DIR couplers': 'DIR 抑制偶联剂',
                'Halation': '高光光晕',
                'Bleach bypass': '留银冲洗',
                'Camera diffusion': '机身柔光',
                'Paper': '相纸介质',
                'Enlarger head': '放大机滤色灯头',
                'Shaping': '阶调与反差塑形',
                'Glare': '相纸眩光',
                'Preflash': '相纸预曝光',
                'Print diffusion': '印相柔光',
                'Frame': '画幅裁剪',
                'Scanner': '底片扫描仪',
                'Exposure': '扫描曝光',
                'Levels': '色阶调整',
                'Response': '感光响应',
                'Curve': '特征曲线',
                'Colour': '色彩与饱和度',
                'Toning': '暗房调色',
                'Input image': '输入图像',
                'Spectral upsampling': '光谱上采样',
                'Input gamut compress': '输入色域压缩',
                'Output gamut compress': '输出色域压缩',
                'Tune': '通道微调',
                'Paper texture': '相纸物理纹理',
            }
            display_group_name = ZH_GROUP_NAMES.get(group.name, group.name)
            section = DyeworksSection(display_group_name, expanded=not group.collapsed,
                                   variant='group')
            for row in group.rows:
                self._add_row(section, row)
            if group.note:
                section.add(NoteRule(group.note))
            body_layout.addWidget(section)
            self._groups.append((group, section))

        if stage.key == 'main':
            # 2026-08-03; see design/DECISIONS.md#d-edit_panel-500
            self._pinned_section = DyeworksSection('', expanded=True,
                                                variant='group')
            index = body_layout.count() - (1 if _ends_with_note(stage) else 0)
            body_layout.insertWidget(max(0, index), self._pinned_section)

        body_layout.addStretch(1)

        # CLAMPED to its viewport -- see `panel_scroll`. NAT's report names
        # both panels ("left and right panel still getting larger and hidding
        # half of their settings"), and the mechanism is the same on each.
        from spektrafilm_gui.panel_scroll import PanelScrollArea

        area = PanelScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        paint_as_panel(area)
        area.setWidget(body)
        return area

    def _add_row(self, section: DyeworksSection, row) -> None:
        if isinstance(row, spec.Special):
            builder = getattr(self, f'_special_{row.kind}', None)
            if builder is not None:
                widget = builder(row.options)
                if widget is not None:
                    section.add(widget)
                return
            if row.kind == 'adv':
                section.add(self._advanced_disclosure(row.options))
                return
            if row.kind == 'note':
                section.add(Hint(row.options.get('text', '')))
                return
            self.missing['special'].append(row.kind)
            return

        targets = spec.resolve_row(row)
        if not targets:
            # UI-only in this app: no stored field, but the control exists
            # and the design gives it a row, so place it rather than drop it.
            widget = self._widget_only_control(row)
            if widget is not None and row == 'scan_finishing.crop_ratio'                     and isinstance(widget, QtWidgets.QComboBox):
                # the page's big `.select` (icon, name, caption), not a labelled chip (case 03)
                from spektrafilm_gui.dyeworks_widgets import RatioSelect

                section.add(RatioSelect(widget))
                return
            if widget is not None:
                label = _WIDGET_ROW_LABELS.get(row) or _label_for(row)
                section.add(self._inline_row(label, widget, row))
            return
        # 2026-08-03; see design/DECISIONS.md#d-edit_panel-501
        if len(targets) > 1:
            composite = self._composite_row(row, targets)
            if composite is not None:
                section.add(composite)
                return

        for name in targets:
            editor = self._editor_for(name)
            if editor is None:
                self.missing['field'].append(name)
                continue
            # 2026-08-03; see design/DECISIONS.md#d-edit_panel-502
            if (name in field_labels.SEG_SHORT
                    and isinstance(editor, QtWidgets.QComboBox)):
                from spektrafilm_gui.dyeworks_buttons import segmented_for_combo

                # SLUG-keyed: our combos do not share the page's option
                # order (ours runs production/preview/rms/synthesis where
                # the page runs rms/production/preview/synthesis), so a
                # positional map labelled every grain button wrongly.
                short = dict(
                    pair.split('=', 1)
                    for pair in field_labels.SEG_SHORT[name].split(',')
                    if '=' in pair)
                seg_row = self._stacked_row(
                    _label_for(name),
                    segmented_for_combo(editor, short,
                                        _SEG_COLUMNS.get(name, 0),
                                        field_labels.SEG_ORDER.get(name)),
                    editor)
                # NAMES ITS FIELD, so applicability and any by-name lookup
                # can reach it. Without this the four segmented rows were
                # invisible to every pass that walks rows by name.
                seg_row.row = name
                section.add(seg_row)
                editor.currentIndexChanged.connect(
                    self._on_summary_source_changed)
                continue
            if name in _PERMUTATION_ROWS:
                from spektrafilm_gui.dyeworks_editors import PermutationSelect

                section.add(self._inline_row(_label_for(name),
                                             PermutationSelect(editor), name))
                continue
            # `rowTuple` — a tuple with no channel colours is ONE LINE PER
            # COMPONENT: a 34px caption, a mini slider, a 52px value. The
            # app drew N spin boxes side by side instead, which is a row of
            # anonymous numbers you cannot drag (NAT 2026-08-03: "dual text
            # zones instead of dual small sliders").
            if (name in field_labels.TUPLE_LABELS
                    and not getattr(editor, '_knobs', None)
                    and len(getattr(editor, '_editors', ())) > 1):
                section.add(self._tuple_row(name, editor))
                continue
            if getattr(editor, '_knobs', None):
                section.add(KnobRow(_label_for(name), editor, name))
                signal = getattr(editor, 'valueChanged', None)
                if signal is not None:
                    signal.connect(self._on_summary_source_changed)
                continue
            # Only the field's HOME stage gets the real editor; anywhere
            # else it is mirrored, because a widget has one parent and the
            # second row would otherwise take the control off the first.
            # Mirror or real, the ramp is applied to BOTH -- `grain.saturation`
            # is on MAIN as well as FILM, and the mirrored copy took the
            # early return and stayed accent blue on one of the two tabs.
            if self._owner.get(name, self._stage_key) != self._stage_key:
                built = MirrorRow(name, editor, self.refresh_summaries)
            else:
                built = EditRow(name, editor, self.refresh_summaries)
            if name in SAT_RAMP_FIELDS:
                _apply_saturation_ramp(built)
            section.add(built)

    # ------------------------------------------------------------- specials
    # One builder per `Special` kind, named for the PAGE's own `t:` value so
    # the two files grep alike. Each drives an existing editor; none owns
    # state. A kind with no builder here is reported, not faked.

    def _special_bigsel(self, options: dict):
        """`{t:'bigsel'}` — the film-stock / paper chip."""
        combo = self._editor_for(options.get('id', ''))
        if combo is None:
            self.missing['field'].append(options.get('id', 'bigsel'))
            return None

        # THE CAPTION IS THE PROFILE'S, not one string for all of them. The
        # page hardcodes `C-41 · colour negative` / `lustre · neutral base`
        # because its mockup only ever showed Portra 400 and Supra Endura;
        # most of the app's stocks are ECN-2, E-6 or black and white, and
        # three of the papers are print film (NAT 2026-08-04).
        from spektrafilm_gui.profile_captions import process_caption

        kind = {'simulation.film_stock': 'stock',
                'simulation.print_paper': 'paper'}.get(options.get('id', ''))
        chip = ChipSelect(combo, caption=options.get('sub', ''),
                          chip=options.get('chip', 'film'),
                          caption_for=process_caption,
                          menu_builder=self._library_menu(kind) if kind else None)
        # 2026-08-04; see design/DECISIONS.md#d-edit_panel-503
        decorate_menu_items(combo, 'chip')
        combo.currentTextChanged.connect(self._on_summary_source_changed)
        return chip

    def _special_pp(self, _options: dict):
        """`{t:'pp'}` — push/pull as -2 -1 N +1 +2 rather than a slider."""
        editor = self._editor_for('push_pull.stops')
        if editor is None:
            self.missing['field'].append('push_pull.stops')
            return None

        segmented = SegmentedEnum(
            ((-2.0, '-2'), (-1.0, '-1'), (0.0, 'N'), (1.0, '+1'), (2.0, '+2')))

        def push(value) -> None:
            editor.value = float(value)

        def pull(*_args) -> None:
            segmented.set_value(float(editor.value))
            self.refresh_summaries()

        # `.pp button { padding: 4px 0 }`, not `.segrow`'s 6: as short as the metering buttons
        # (29.5 against 32; panel-interiors case 12)
        segmented.setStyleSheet(
            'QPushButton[role="segmented"], QToolButton[role="segmented"] '
            '{ padding: 4px 0px; min-height: 19px; }')
        segmented.setMinimumHeight(29)      # it pinned 32 from the buttons' hints before this applied
        row = LabelledRow('迫冲 / 减冲', segmented,      # the page's capital P
                          reset=_reset_editors([editor]))
        _wire_reset(row, [editor])                       # 20.3: its reset, counted

        def pull_with_value(*_args) -> None:
            pull()
            stops = float(editor.value)
            row.set_value_text('无' if stops == 0 else
                               f"{'+' if stops > 0 else ''}"
                               f"{int(stops) if float(stops).is_integer() else stops}"
                               f" 档 (EV)")

        segmented.changed.connect(push)
        editor.valueChanged.connect(pull_with_value)
        pull_with_value()
        return row

    def _special_meter(self, _options: dict):
        """`{t:'meter'}` — metering as camera glyphs, not a dropdown."""
        editor = self._editor_for('simulation.auto_exposure_method')
        if editor is None:
            self.missing['field'].append('simulation.auto_exposure_method')
            return None

        # the app's seven modes, in its own order, against the page's glyphs
        glyphs = {'center_weighted': 'met-cw', 'matrix': 'met-matrix',
                  'multi_zone': 'met-mz', 'partial': 'met-partial',
                  'highlight_weighted': 'met-hl', 'median': 'met-med',
                  'average': 'met-avg'}
        values = [editor.itemText(i) for i in range(editor.count())]
        strip = IconStrip(tuple((glyphs.get(v, 'met-cw'), v.replace('_', ' '))
                                for v in values), height=32, gap=4)

        row = LabelledRow('测光模式', strip, reset=_reset_editors([editor]))
        _wire_reset(row, [editor])                       # 20.3: its reset, counted
        # 2026-08-03; see design/DECISIONS.md#d-edit_panel-504
        from spektrafilm_gui.dyeworks_labels import _mono_font

        row.value.setFont(_mono_font(TYPE_CAPTION))
        # Set ON THE WIDGET, not by a `role` property: the sheet styles this
        # label by its object name, and an ID selector outranks an attribute
        # one however it is polished. Same trap as the saturation ramp.
        # (and the size with it: the page's caption is 11px, the sheet's value rule made it 13)
        row.value.setStyleSheet(
            f'color: {palette.TEXT_SOFT}; font-size: {int(round(TYPE_CAPTION))}px;')
        strip.changed.connect(lambda index: setattr(editor, 'value', values[index]))

        def pull(*_args) -> None:
            current = str(editor.value)
            if current in values:
                strip.set_index(values.index(current))
            try:
                from spektrafilm_gui.zh_translation import translate_option
                val_text = translate_option(current, current.replace('_', ' ').capitalize())
            except Exception:
                val_text = current.replace('_', ' ').capitalize()
            row.set_value_text(val_text)
            self.refresh_summaries()

        editor.currentTextChanged.connect(pull)
        pull()
        return row

    def _special_sectitle(self, options: dict):
        """`{t:'sectitle'}` -- a small title inside a group (`.sectitle`:
        10px, --ink-3; QA 09-12 item 6). Sentence case since 20.2 (R3)."""
        title = QtWidgets.QLabel(str(options.get('text', '')))
        title.setObjectName('dyeworksSectTitle')
        return title

    def _special_filters(self, _options: dict):
        """`{t:'filters'}` -- the taking filters as glass, not a list.

        THREE ROWS since 2026-09-26 (old-plans trace C2, the OFX's 24A; the
        phase-20 filter page's `FLT_SLOTS`): colour / effect filter 1, a
        second one stacked on it, and a neutral density -- then the
        filter-factor toggle NAT approved with C2, which the page has no row
        for. The first row is the one that has always been here."""
        first = self._filter_chip('input_image.color_filter')
        if first is None:
            return None
        second = self._filter_chip('input_image.color_filter_2')
        neutral = self._nd_chip()
        factor = self._editor_for('input_image.filter_factor_compensation')
        holder = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        for chip in (first, second, neutral):
            if chip is not None:
                column.addWidget(chip)
        if factor is not None:
            from spektrafilm_gui.dyeworks_buttons import ToggleSwitch
            from spektrafilm_gui.dyeworks_labels import LabelledRow

            row = LabelledRow('滤镜曝光补偿因数')
            row.value.setVisible(False)
            switch = ToggleSwitch()
            switch.setChecked(bool(factor.value))
            switch.setToolTip(
                "按滤色镜的进光损失自动补偿曝光，仅保留色彩倾向（中性灰减光镜不参与此补偿）。")
            switch.toggled.connect(lambda on: setattr(factor, 'value', bool(on)))
            _connect_value_signal(factor, lambda *_: switch.setChecked(bool(factor.value)))
            head = row.layout().itemAt(0).widget().layout()
            head.insertWidget(head.count() - 1, switch)
            row.switch = switch
            column.addWidget(row)
        holder.first_chip = first
        return holder

    def _nd_chip(self):
        """The ND row: Wratten No.96 as grey glass, darker as it gets denser,
        captioned in the photographer's units (the page's `2x · 1.0 stops`)."""
        editor = self._editor_for('input_image.nd_filter')
        if editor is None:
            self.missing['field'].append('input_image.nd_filter')
            return None

        def _label(value: str) -> str:
            density = nd_filter_density(value)
            return '无减光镜 (None)' if density is None else f'No.96 ND {density:.1f}'

        def _caption(value: str) -> str:
            density = nd_filter_density(value)
            if density is None:
                return '中性灰减光镜 (ND)'
            stops = density / 0.301
            return f'{2 ** stops:.0f}× · 减光 {stops:.1f} 档'

        glass = _nd_glass()
        chip = ChipSelect(editor, chip='lens', label_for=_label, caption_for=_caption,
                          menu_builder=self._library_menu('nd'))

        def _paint_glass(*_args) -> None:
            designed = card_store.glass_override(str(editor.value))
            chip.set_glass(designed if designed is not None
                           else glass.get(str(editor.value), ''))

        _paint_glass()
        editor.currentTextChanged.connect(_paint_glass)
        chip._card_repaint = _paint_glass     # the chip owns the only strong ref
        card_store.subscribe(chip._card_repaint)
        decorate_menu_items(editor, 'lens', glass)
        return chip

    # ------------------------------------------------ the library (C1)
    def _library_menu(self, kind: str):
        """A `ChipSelect.menu_builder` for one library: the row's dropdown
        (favourites or defaults, "More…") and, behind More…, the library
        popover. Filter navigation v6 (old-plans trace C1, 2026-09-26)."""

        def build(chip):
            pool = _library_pool(kind, chip.combo)
            more = lambda: self.open_library(kind, chip)
            if kind in ('stock', 'paper'):
                return library_menus.big_select_menu(chip, kind, pool, more)
            return library_menus.row_menu(chip, kind, pool, more)

        return build

    def open_library(self, kind: str, chip):
        """`openFilterPop`: one popover per library and chip, kept and
        re-shown (its narrowing survives; its query does not)."""

        key = ('library', kind, id(chip))
        popover = self._popovers.get(key)
        if popover is None or not _alive(popover):
            rows = lambda: {chip.combo.itemText(i): i for i in range(chip.combo.count())}
            popover = LibraryPopover(
                kind, chip, lambda: _library_pool(kind, chip.combo),
                icon_for=lambda v: chip._pool_icon(rows().get(v, -1), v),
                parent=self)
            self._popovers[key] = popover
        popover.open_for(chip)
        return popover

    def _filter_chip(self, path: str):
        editor = self._editor_for(path)
        if editor is None:
            self.missing['field'].append(path)
            return None

        # The design shows this as a COMPOSITE row -- a single glass swatch
        # with the filter's name and what it is -- rather than a strip of
        # every filter. A taking filter is one object on the lens.
        from spektrafilm_gui.profile_captions import FILTER_CAPTION

        # The pool rows read as names, not as stored keys. The label is the
        # model's own (`ColorFilterSpec.label`), which writes it as
        # 'Hoya X0 (yellow-green)'; the design's pool writes the same fact
        # with a middot, so the brackets become one. 'none' is title-cased,
        # since the reference heads that row 'None'.
        def _filter_label(value: str) -> str:
            key = str(value).lower()
            if key in ('', 'none'):
                return '无滤镜 (None)'
            for spec in COLOR_FILTER_LIBRARY:
                if spec.key == key:
                    label = str(spec.label)
                    if label.endswith(')') and '(' in label:
                        head, _, tail = label.partition('(')
                        return f'{head.strip()}, {tail[:-1].strip()}'
                    return label
            return str(value)

        chip = ChipSelect(editor, caption=FILTER_CAPTION, chip='lens',
                          label_for=_filter_label,
                          menu_builder=self._library_menu('filter'))


        def _paint_glass(*_args) -> None:
            chip.set_glass(glass_for(editor.value))

        _paint_glass()
        editor.currentTextChanged.connect(_paint_glass)
        chip._card_repaint = _paint_glass     # the chip owns the only strong ref
        card_store.subscribe(chip._card_repaint)
        # ...and a disc of that glass beside every filter in the menu
        from spektrafilm_gui.dyeworks_chips import decorate_menu_items

        decorate_menu_items(editor, 'lens', glass_for)
        return chip

    def _special_head(self, _options: dict):
        """`{t:'head'}` — the enlarger head: dye dots and 'Y12 M-4 C0'."""
        return self._filter_head('放大机滤色灯头', 'simulation.print_{}_filter_shift')

    def _special_preflashhead(self, _options: dict):
        """`{t:'preflashhead'}` — the same control for the preflash lamp."""
        return self._filter_head('预曝光灯头', 'preflashing.{}_filter_shift')

    def _special_filmbase(self, _options: dict):
        """`{t:'filmbase'}` — the film base / orange mask, as three dyes."""
        return self._base_head('胶片片基色调', 'film_base')

    def _special_printbase(self, _options: dict):
        """`{t:'printbase'}` — the paper base, as three dyes."""
        return self._base_head('相纸纸基色调', 'print_base')

    def _base_head(self, label: str, prefix: str):
        """The base densities in the enlarger head's own language.

        NAT 2026-08-31 asked for "a popover like the ones of preflash and
        print filter", and the parameters fit it exactly: three per-channel
        densities at the status-M (film) / status-A (print) dye peaks. So
        this is `_filter_head`'s shape over `{prefix}.cyan/magenta/yellow`
        rather than a new control.

        Two differences, both because these are DENSITIES and not detented
        filter shifts: the values are continuous (two decimals, no rounding)
        and neutral is 1.0, not 0.

        `scale` and `tilt` stay as ordinary rows in the group -- they are one
        number each and have no dye to be tinted with.
        """

        # Keyed y/m/c because that is what FilterHeadRow's dye dots are; the
        # FIELDS are named for the dyes they move.
        fields = {'c': f'{prefix}.cyan', 'm': f'{prefix}.magenta',
                  'y': f'{prefix}.yellow'}
        editors = {}
        for channel, name in fields.items():
            editor = self._editor_for(name)
            if editor is None:
                self.missing['field'].append(name)
                return None
            editors[channel] = editor

        def _summary(current: dict) -> str:
            def value(channel: str) -> str:
                editor = current.get(channel)
                try:
                    return f'{float(editor.value):.2f}'
                except (AttributeError, TypeError, ValueError):
                    return '1.00'
            # 2026-09-05, 2026-09-24; see design/DECISIONS.md#d-edit_panel-505
            return f"C{value('c')} M{value('m')} Y{value('y')}"

        row = FilterHeadRow(
            label, editors, summary=_summary,
            row_label=lambda channel: {'c': 'Cyan', 'm': 'Magenta',
                                       'y': 'Yellow'}[channel])
        key = f'base-{prefix}'

        def _open() -> None:
            popover = self._popover(key, label, meta=row.value.text(),
                                    width=DYE_POPOVER_WIDTH)   # 20.3: 268 -> 307
            if popover.body.layout().count() == 0:
                # ...and the popover rows in that order too, so opening a
                # base does not re-shuffle the dyes you just read.
                for channel, dye, tint in DYE_POPOVER_ROWS:
                    popover.add(_dye_row(dye, tint, fields[channel],
                                         editors[channel],
                                         self.refresh_summaries,
                                         decimals=2, signed=True))   # the page prints '+1.00'
                popover.add_spacing(4)      # `.wide { margin-top }`, as the head popovers (case 14)
                neutral = WideButton('Neutral base')
                neutral.setToolTip(
                    "Every dye back to 1.00: the profile's own base, "
                    "untouched.")

                def _neutral() -> None:
                    for editor in editors.values():
                        editor.value = 1.0
                    self.refresh_summaries()

                neutral.clicked.connect(_neutral)
                popover.add(neutral)
            popover.set_meta(row.value.text())
            popover.show_beside(row)

        row.set_opener(_open)

        def _sync(*_args) -> None:
            popover = self._popovers.get(key)
            if popover is not None and _alive(popover):
                popover.set_meta(row.value.text())
            self.refresh_summaries()

        for editor in editors.values():
            editor.valueChanged.connect(_sync)
        return row

    def _filter_head(self, label: str, pattern: str):
        """A composite row over the three subtractive filter shifts.

        Opens the design's popover: one tinted slider per dye, and a
        Neutral balance button that returns all three to zero -- which is
        what a darkroom head's own detent does.
        """

        editors = {}
        names = {}
        for channel in ('c', 'm', 'y'):
            name = pattern.format(channel)
            editor = self._editor_for(name)
            if editor is None:
                self.missing['field'].append(name)
                return None
            editors[channel] = editor
            names[channel] = name

        row = FilterHeadRow(label, editors)
        key = f'head-{pattern}'

        def _open() -> None:
            popover = self._popover(key, label, meta=row.value.text(),
                                    width=DYE_POPOVER_WIDTH)   # 20.3: 268 -> 307
            if popover.body.layout().count() == 0:
                # `openHeadPop` — one row per DYE: a colour chip, the dye's
                # name, its value, and a slider filled in that dye. The
                # field's own label ('Y filter shift') is not what the page
                # shows here.
                for channel, dye, tint in DYE_POPOVER_ROWS:
                    popover.add(_dye_row(dye, tint, names[channel],
                                         editors[channel],
                                         self.refresh_summaries))
                # `.wide { margin-top: 11px }` on top of the grid's gap: the page seats the
                # button 4px lower than the gap alone (case 14)
                popover.add_spacing(4)
                neutral = WideButton('Neutral balance')
                neutral.setToolTip('Every filter back to zero.')

                def _neutral() -> None:
                    for editor in editors.values():
                        editor.value = 0.0
                    self.refresh_summaries()

                neutral.clicked.connect(_neutral)
                popover.add(neutral)
            popover.set_meta(row.value.text())
            popover.show_beside(row)

        row.set_opener(_open)

        def _sync(*_args) -> None:
            popover = self._popovers.get(key)
            if popover is not None and _alive(popover):
                popover.set_meta(row.value.text())
            self.refresh_summaries()

        for editor in editors.values():
            editor.valueChanged.connect(_sync)
        return row

    def _special_method(self, _options: dict):
        """`{t:'method'}` — the spectral upsampling method, as a SELECTOR.

        2026-08-01; see design/DECISIONS.md#d-edit_panel-518
        """
        editor = self._editor_for('input_image.spectral_upsampling_method')
        if editor is None:
            self.missing['field'].append('input_image.spectral_upsampling_method')
            return None

        select = MethodSelect(editor)
        # the same mini curve beside every method in the menu
        from spektrafilm_gui.dyeworks_chips import decorate_menu_items

        decorate_menu_items(editor, 'method')
        editor.currentTextChanged.connect(self._on_summary_source_changed)
        return select

    def _special_curve(self, _options: dict):
        """`{t:'curve'}` — the SCAN finishing point curve.

        The app already has this editor (Phase 10A); the design just gives
        it a home in the Curve group instead of a section of its own.
        """
        section = getattr(self._widgets, 'scan_curve', None)
        view = getattr(section, 'curve_view', None)
        if view is None:
            self.missing['special'].append('curve')
            return None
        return view

    def _special_huesat(self, _options: dict):
        """`{t:'huesat'}` — six hue dots sharing one slider."""
        finishing = getattr(self._widgets, 'scan_finishing', None)
        triplets = {name: getattr(finishing, f'hue_saturation_{name}', None)
                    for name in ('ryg', 'cbm')}
        if any(editor is None for editor in triplets.values()):
            self.missing['special'].append('huesat')
            return None

        # 2026-08-02; see design/DECISIONS.md#d-edit_panel-506
        row = HueSatRow(triplets, {
            'r': palette.CHANNEL_R, 'y': palette.CHANNEL_Y,
            'g': palette.CHANNEL_G, 'c': palette.CHANNEL_C,
            'b': palette.CHANNEL_B, 'm': palette.CHANNEL_M},
            master=getattr(finishing, 'saturation', None))
        return row

    #: The anamorphic desqueeze is DRAGGED over this, not over its legal
    #: range. See `SliderFieldEditor.set_slider_travel` for why: the
    #: parameter is legal over [-2, 2], but 0 and +-1 are all identity and
    #: everything in (0, 1) compresses, so a slider across the legal range
    #: would sit one step away from squeezing the image to a hundredth of
    #: its width. Over [1, 2] there is no cliff, one identity instead of
    #: three, and the travel is exactly the anamorphic factors -- 1.33, 1.5,
    #: 1.8, 2.0 -- that the control exists for.
    DESQUEEZE_TRAVEL = (1.0, 2.0)

    def _special_desq(self, _options: dict):
        """`{t:'desq'}` — anamorphic desqueeze.

        2026-08-25; see design/DECISIONS.md#d-edit_panel-519

        The row is the ordinary one every other float field gets, so the
        reset arrow, the modified dot, the precision modifier and the
        drag-render coalescing all come with it rather than being
        re-implemented here.
        """
        editor = self._editor_for('scan_finishing.desqueeze')
        if editor is None:
            self.missing['field'].append('scan_finishing.desqueeze')
            return None
        travel = getattr(editor, 'set_slider_travel', None)
        if callable(travel):
            travel(*self.DESQUEEZE_TRAVEL)
        return EditRow('scan_finishing.desqueeze', editor,
                       self.refresh_summaries)

    def _special_format(self, _options: dict):
        """`{t:'format'}` — the film format, picked by gate size.

        The picker drives the app's existing preset combo, which owns the
        mm mapping; the mm box appears underneath ONLY when the choice is
        Custom, exactly as the page does it.
        """
        camera = getattr(self._widgets, 'camera', None)
        combo = getattr(camera, 'film_format_preset', None)
        mm_editor = self._editor_for('simulation.film_format_mm')
        if combo is None or mm_editor is None:
            self.missing['special'].append('format')
            return None


        holder = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        select = GateSelect(combo)
        column.addWidget(select)

        mm_row = EditRow('simulation.film_format_mm', mm_editor,
                         on_changed=self.refresh_summaries)
        column.addWidget(mm_row)

        def _follow(*_args) -> None:
            custom = combo.currentText() == film_formats.CUSTOM_LABEL
            mm_row.setVisible(custom)
            select._sync()

        combo.currentTextChanged.connect(_follow)
        mm_editor.valueChanged.connect(_follow)
        _follow()
        return holder

    def _special_levels(self, _options: dict):
        """`{t:'levels'}` — the two master levels, placed on the render.

        The per-channel offsets stay as ordinary rows underneath; this is the
        master pair only, which is what the page shows.
        """
        black = self._editor_for('scan_finishing.black_point')
        white = self._editor_for('scan_finishing.white_point')
        if black is None or white is None:
            self.missing['special'].append('levels')
            return None


        row = LevelsRow(black, white)
        self._levels_rows.append(row)
        return row

    def _special_specuv(self, _options: dict):
        """`{t:'specuv'}` — the UV filter as a spectrum bar."""
        return self._spectrum_row('input_image.filter_uv', 'low', 'specuv')

    def _special_specir(self, _options: dict):
        """`{t:'specir'}` — the IR filter, blocking the far side."""
        return self._spectrum_row('input_image.filter_ir', 'high', 'specir')

    def _spectrum_row(self, name: str, side: str, kind: str):
        editor = self._editor_for(name)
        if editor is None:
            self.missing['special'].append(kind)
            return None


        holder = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(7)

        bar = SpectrumBar(editor, side=side)
        row = LabelledRow(_label_for(name), bar,
                          reset=getattr(editor, 'reset_to_baseline', None))

        def _show(*_args) -> None:
            row.set_value_text(f'{bar.cutoff():.0f} nm')
            bar.update()

        bar.changed.connect(_show)
        # The bar IS a slider, so its nanometres are typable like any other
        # number -- through the bar, which owns the clamp to the visible
        # range and writes the cutoff back into the tuple.
        from spektrafilm_gui.dyeworks_pads import SPECTRUM_NM

        row.set_value_input(bar.set_cutoff, minimum=SPECTRUM_NM[0],
                            maximum=SPECTRUM_NM[1], decimals=0)
        signal = getattr(editor, 'valueChanged', None)
        if signal is not None:
            signal.connect(_show)
        _show()

        column.addWidget(row)
        # 'Amplitude + sigma stay as compact sliders beneath' -- the bar
        # owns the cutoff, so its box goes, and the two that remain get the
        # captions the design gives them. Three unlabelled numbers under a
        # spectrum say nothing about which is which.
        boxes = list(getattr(editor, '_editors', ()))
        if len(boxes) == 3:
            editor.setParent(holder)
            editor.hide()
            # 2026-08-03; see design/DECISIONS.md#d-edit_panel-507
            column.addWidget(_mini_cell_grid(editor, (
                ('amp', 0, 0.0, 1.0, 0.05, 2),
                ('\u03c3 nm', 2, 0.0, 30.0, 0.5, 1),
            ), on_changed=_show, value_px=10, full_rows=EDIT_COLUMNS))   # 20.3: one per line
        else:
            column.addWidget(editor)
        return holder

    def _special_glarepad(self, _options: dict):
        """`{t:'glarepad'}` — where the lamp sits on the print (Phase 19).

        2026-08-01; see design/DECISIONS.md#d-edit_panel-520
        """
        toggle = self._editor_for('glare.position_active')
        position = self._editor_for('glare.position')
        if toggle is None or position is None:
            self.missing['special'].append('glarepad')
            return None


        holder = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        column.addWidget(EditRow('glare.position_active', toggle,
                                 self.refresh_summaries))

        pad = XYPad(position)          # a POINT: a lamp has no extent
        # 2026-08-03; see design/DECISIONS.md#d-edit_panel-508
        pad.set_lamp_params(amount=self._editor_for('glare.percent'),
                            roughness=self._editor_for('glare.roughness'),
                            blur=self._editor_for('glare.blur'))
        pad.setToolTip('Drag to place the lamp. The pool is an indication of '
                       'amount, roughness and blur, not the render.')
        row = LabelledRow('Light position', pad,
                          reset=getattr(position, 'reset_to_baseline', None))

        def _show(*_args) -> None:
            x, y = pad.centre()
            row.set_value_text(f'{x:.2f}, {y:.2f}')

        def _follow(*_args) -> None:
            on = bool(toggle.value)
            row.setEnabled(on)
            pad.setEnabled(on)
            _show()

        pad.changed.connect(_show)
        signal = getattr(position, 'valueChanged', None)
        if signal is not None:
            signal.connect(_show)
        toggle.toggled.connect(_follow)
        _follow()

        column.addWidget(row)
        # 2026-08-03; see design/DECISIONS.md#d-edit_panel-509
        position.setParent(holder)
        position.hide()
        return holder

    def _special_prevcrop(self, _options: dict):
        """`{t:'prevcrop'}` — the preview crop, placed rather than typed."""
        centre = self._editor_for('input_image.crop_center')
        size = self._editor_for('input_image.crop_size')
        active = self._editor_for('input_image.crop')
        if centre is None or size is None:
            self.missing['special'].append('prevcrop')
            return None


        holder = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        active_row = None
        if active is not None:
            active_row = EditRow('input_image.crop', active,
                                 self.refresh_summaries)
            column.addWidget(active_row)

        pad = CropPad(centre, size)
        # asked at use time: the provider is installed after the panel is built
        pad.frame_shape_provider = lambda: (
            self._frame_shape_provider() if self._frame_shape_provider else None)
        self._crop_pads.append(pad)
        column.addWidget(pad)

        # 2026-10-08; see design/DECISIONS.md#d-edit_panel-510
        def _reset() -> None:
            size.reset_to_baseline()
            _push(pad.uniform_size())

        row = LabelledRow('Crop size',
                          reset=_reset if hasattr(size, 'reset_to_baseline') else None)
        slider = MiniSlider(minimum=0.1, maximum=1.0, step=0.01)
        slider.set_reset(getattr(size, 'reset_to_baseline', None))

        def _pull(*_args) -> None:
            width = pad.uniform_size()
            slider.set_value(width)
            row.set_value_text(f'{width:.2f}')
            row.set_modified(width != _baseline_of(size, width))

        def _push(value: float) -> None:
            pad.set_uniform_size(value)
            # re-clamp the centre so the enlarged box stays on the picture
            cx, cy = pad.centre()
            pad._set_centre(cx, cy)
            _pull()

        # Switching the crop ON is when the stored pair starts to matter, so
        # it is fitted to the frame then -- on the user's click only, so a
        # saved state keeps rendering what it always rendered.
        toggle = getattr(active_row, '_toggle', None)
        if toggle is not None:
            toggle.toggled.connect(
                lambda on: on and _push(pad.uniform_size()))

        slider.moved.connect(_push)
        row.set_value_input(_push, minimum=0.1, maximum=1.0, decimals=2)
        on_editor_changed(size, _pull)
        pad.changed.connect(_pull)
        _pull()

        # `srow = el('div','fg')`: the row and its slider are ONE block, 6 px
        # apart (the `.fg` gap), not 12 like the blocks around it
        crop_size = QtWidgets.QWidget()
        crop_size_column = QtWidgets.QVBoxLayout(crop_size)
        crop_size_column.setContentsMargins(0, 0, 0, 0)
        crop_size_column.setSpacing(6)
        crop_size_column.addWidget(row)
        crop_size_column.addWidget(slider)
        column.addWidget(crop_size)
        column.addWidget(Hint('Drag the box to place the preview crop; its '
                              'size is uniform.'))
        return holder

    def _special_halpreview(self, _options: dict):
        """`{t:'halpreview'}` — the FX preview, from the halation group.

        2026-09-25; see design/DECISIONS.md#d-edit_panel-521
        """
        return self._fx_row('Preview', stage=None)

    def _special_glowpreview(self, options: dict):
        """`{t:'glowpreview'}` — the same panel, from a diffusion group.

        The page passes which STAGE's diffusion filter this row belongs to:
        the camera filter or the enlarger's.
        """
        return self._fx_row('Glow preview', stage=options.get('stage', 'camera'))

    # ------------------------------------------------------- applicability
    #: Push/pull's two-layer crossover and developer exhaustion belong to the
    #: EXPERIMENTAL mode; the standard morph does not read them.
    def _wire_applicability(self) -> None:
        """Show a setting only where it does something (NAT 2026-08-02).

        The legacy sections already knew this -- `GrainSection` has hidden
        wrong-model rows since Phase 11A -- but this panel RE-HOMES their
        editors, so `set_row_visible` acts on wrappers that are no longer on
        screen. Same shape as the reset convention and the unstyled
        containers: behaviour the old sections had, bypassed rather than
        carried across. So it is done HERE, where the rows actually live.
        """
        grain = getattr(self._widgets, 'grain', None)
        model = getattr(grain, 'model', None)
        if model is not None:
            signal = getattr(model, 'currentTextChanged', None)
            if signal is not None:
                signal.connect(self._sync_applicability)
        push_pull = getattr(self._widgets, 'push_pull', None)
        mode = getattr(push_pull, 'mode', None)
        if mode is not None:
            signal = getattr(mode, 'currentTextChanged', None)
            if signal is not None:
                signal.connect(self._sync_applicability)
        for name in ('film_stock', 'scan_film'):
            editor = getattr(getattr(self._widgets, 'simulation', None),
                             name, None)
            for signal_name in ('currentTextChanged', 'toggled'):
                signal = getattr(editor, signal_name, None)
                if signal is not None and hasattr(signal, 'connect'):
                    signal.connect(self._sync_applicability)
                    break
        self._sync_applicability()

    def _rows_named(self, names) -> list:
        """Every row carrying one of these field names.

        ANY widget that names its field, not just `LabelledRow`. A knob
        triplet is a `KnobRow`, which is not one -- so `density_min`,
        `particle_scale`, `rms_granularity` and `uniformity` were invisible
        to the applicability pass and showed under every grain model. That
        is four of the fifteen model-specific rows, and it is what made the
        panel look like it was showing all four models at once (NAT
        2026-08-03; measured, not inferred).
        """
        wanted = set(names)
        return [row for row in self.findChildren(QtWidgets.QWidget)
                if getattr(row, 'row', None) in wanted]

    def _sync_applicability(self) -> None:
        # --- grain: only the selected model's own rows
        from spektrafilm_gui.widget_sections import GrainSection

        grain = getattr(self._widgets, 'grain', None)
        model = getattr(grain, 'model', None)
        if model is not None:
            current = str(model.value)
            wanted = GrainSection._MODEL_FIELDS.get(
                current, GrainSection._MODEL_FIELDS['production'])
            for field in GrainSection._MODEL_ROWS:
                for row in self._rows_named([f'grain.{field}']):
                    row.setVisible(field in wanted)

        # Push / pull had three rows hidden outside experimental mode until
        # 2026-09-03. They are FilmChemistryState's now and always apply, so
        # there is nothing left here to show or hide -- which is the point:
        # the mode picks a mechanism, it does not gate a set of knobs.

        # --- print: GREYED, not removed, when the print stage is bypassed
        self._sync_print_applicability()

    def _print_is_bypassed(self) -> bool:
        """Whether the print stage does nothing for the current frame.

        BOTH conditions, per NAT: `scan_film` taps CMY_FILM instead of
        CMY_PRINT, and a POSITIVE stock is scanned directly. They are not
        the same -- a negative can be scanned as film too.
        """
        simulation = getattr(self._widgets, 'simulation', None)
        scan_film = getattr(simulation, 'scan_film', None)
        if scan_film is not None:
            try:
                if bool(scan_film.value):
                    return True
            except Exception:
                pass
        stock = getattr(simulation, 'film_stock', None)
        if stock is None:
            return False
        try:
            from spektrafilm.profiles.io import load_profile

            return str(load_profile(str(stock.value)).info.type) == 'positive'
        except Exception:
            return False

    def _sync_print_applicability(self) -> None:
        """GREY the print stage rather than hide it (NAT's call).

        2026-08-02; see design/DECISIONS.md#d-edit_panel-522
        """
        bypassed = self._print_is_bypassed()
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index).upper() != 'PRINT':
                continue
            page = self.tabs.widget(index)
            page.setEnabled(not bypassed)
            self.tabs.setTabToolTip(
                index,
                'The print stage is bypassed for this frame: a positive '
                'stock, or scan-film, is scanned directly.' if bypassed else '')

    def _advanced_disclosure(self, options: dict):
        """`{t:'adv'}` — research parameters behind a closed button.

        2026-08-02; see design/DECISIONS.md#d-edit_panel-523

        The rows themselves were right; they were in the wrong container.
        """

        body = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(DISCLOSURE_ROW_GAP)

        class _Into:
            """`_add_row` only needs somewhere with `add()`."""

            @staticmethod
            def add(widget) -> None:
                column.addWidget(widget)

        for inner in options.get('rows', ()):
            self._add_row(_Into, inner)
        return Disclosure(options.get('label') or 'Advanced', body)

    #: The stage the last glow preview opened on: the halation preview's.
    _last_fx_stage = 'camera'

    def _fx_row(self, label: str, *, stage: str | None):
        button = CompositeButton(label, value='1:1')
        button.setToolTip('What halation and diffusion are doing, run '
                          'through the real code on a test target.')

        def _open() -> None:
            shown = stage or self._last_fx_stage
            if stage is not None:
                self._last_fx_stage = stage
            popover = self._popover(f'fx-{shown}', 'Preview',
                                    meta=f'{shown}, live, 1:1',
                                    width=FX_POPOVER_WIDTH)
            panel = getattr(popover, '_fx_panel', None)
            if panel is None:
                panel = FxPreviewPanel(self._widgets, stage=shown)
                panel.plate.frame_shape_provider = self._frame_shape_provider
                popover.add(panel)
                # the popover takes the plate's width (the scatter strip is
                # narrower than a chart), now that it has one to take
                panel.plate._resize_to_target()
                popover._fx_panel = panel
                self._fx_panels.append(panel)
            popover.show_beside(button)

        button.clicked.connect(_open)
        return button

    def refresh_fx_previews(self) -> None:
        """Any parameter may feed the preview, so every row asks for one.

        The plate coalesces the burst into a single render, which is why a
        drag does not turn into one engine call per pixel of travel.
        """
        for panel in self._fx_panels:
            try:
                if panel.isVisible():
                    panel.plate.request_render()
            except RuntimeError:
                pass

    def _special_coupbtn(self, _options: dict):
        """`{t:'coupbtn'}` — the DIR matrix, behind a composite row."""
        couplers = getattr(self._widgets, 'couplers', None)
        if couplers is None:
            self.missing['special'].append('coupbtn')
            return None

        button = CompositeButton(
            'Inhibition matrix', value='3\u00d73',
            dyes=(palette.CHANNEL_R, palette.CHANNEL_G, palette.CHANNEL_B))
        button.setToolTip('Every DIR inhibition, as from-layer against '
                          'onto-layer.')

        def _open() -> None:
            popover = self._popover('couplers', 'Inhibition matrix',
                                    meta='DIR couplers', width=302,
                                    padding=(17, 16, 17, 17), gap=14)   # #coup-pop .pb
            if popover.body.layout().count() == 0:
                popover.add(CouplerMatrix(couplers))
                popover.add(Hint('Each row is a layer holding back the layers in '
                                 'the columns. Drag a cell up or down; '
                                 'double-click resets it.'))
                size = self._editor_for('couplers.diffusion_size_um')
                if size is not None:
                    popover.add(EditRow('couplers.diffusion_size_um', size,
                                        self.refresh_summaries, columns=SIDE_COLUMNS))
            popover.show_beside(button)

        button.clicked.connect(_open)
        return button

    def _special_toningbtn(self, _options: dict):
        """`{t:'toningbtn'}` — split toning, behind a composite row."""
        finishing = getattr(self._widgets, 'scan_finishing', None)
        if finishing is None:
            self.missing['special'].append('toningbtn')
            return None

        button = CompositeButton('Split toning')
        button.setToolTip('Shadow and highlight tints, as one axis through '
                          'the neutral centre.')

        def _summary() -> str:
            return spec.tone_meta(self._value_of)

        def _refresh(*_args) -> None:
            button.set_value_text(_summary())
            hues = []
            for name in ('shadow_tint_hue', 'midtone_tint_hue',
                         'highlight_tint_hue'):
                editor = getattr(finishing, name, None)
                try:
                    hues.append(dye_hue_colour(float(editor.value)))
                except (AttributeError, TypeError, ValueError):
                    pass
            button.set_dyes(tuple(hues))
            popover = self._popovers.get('toning')
            if popover is not None:
                popover.set_meta(_summary())

        for name in ('shadow_tint_hue', 'shadow_tint_strength',
                     'midtone_tint_hue', 'midtone_tint_strength',
                     'highlight_tint_hue', 'highlight_tint_strength'):
            editor = getattr(finishing, name, None)
            signal = getattr(editor, 'valueChanged', None)
            if signal is not None:
                signal.connect(_refresh)

        def _open() -> None:
            popover = self._popover('toning', 'Toning', meta=_summary(),
                                    width=TONE_POPOVER_WIDTH,   # 20.3: 262 -> 333
                                    padding=(18, 12, 18, 14))           # #tone-pop .pb
            if popover.body.layout().count() == 0:
                wheel = ToningWheel(finishing)
                wheel.changed.connect(self.refresh_summaries)
                holder = QtWidgets.QWidget()
                centre = QtWidgets.QHBoxLayout(holder)
                centre.setContentsMargins(0, 0, 0, 0)
                centre.addStretch(1)
                centre.addWidget(wheel)
                centre.addStretch(1)
                popover.add(holder)
                # 2026-08-27, 2026-09-08; see design/DECISIONS.md#d-edit_panel-511
                for name, label in (
                        ('scan_finishing.shadow_tint_strength',
                         'Shadow strength'),
                        ('scan_finishing.midtone_tint_strength',
                         'Midtone strength'),
                        ('scan_finishing.highlight_tint_strength',
                         'Highlight strength')):
                    editor = self._editor_for(name)
                    if editor is not None:
                        row = EditRow(name, editor, self.refresh_summaries,
                                      columns=SIDE_COLUMNS)   # 20.3: a popover's columns
                        row.label.setText(label)
                        popover.add(row)
                popover.add(Hint(
                    'Angle is hue, distance from the centre is strength. Dark '
                    'puck = shadows, mid puck = midtones, light puck = '
                    'highlights.'))
            popover.show_beside(button)

        button.clicked.connect(_open)
        _refresh()
        return button

    def _popover(self, key: str, title: str, *, meta: str = '',
                 width: int = 268, padding: tuple | None = None,
                 gap: int | None = None):
        """One popover per key, kept and reused.

        Rebuilding it per click would drop its position, so it is created
        once and re-shown -- which is also what makes it a tool window
        rather than a menu.
        """

        popover = self._popovers.get(key)
        if popover is None or not _alive(popover):
            popover = Popover(title, meta=meta, width=width, parent=self)
            popover.set_body_metrics(padding, gap)
            self._popovers[key] = popover
        else:
            popover.set_meta(meta)
        return popover

    def _special_btns(self, options: dict):
        """`{t:'btns'}` — a row of the section's OWN action buttons."""
        wanted = tuple(options.get('items', ()))
        if 'Add texture…' in wanted:
            return self._paper_texture_buttons(wanted)

        source = {
            'Crop': ('scan_frame', 'crop_button'),
            'Straighten': ('scan_frame', 'straighten_button'),
            'Swap': ('scan_frame', 'swap_button'),
            'Reset': ('scan_frame', 'reset_button'),
        }
        buttons = []
        for label in wanted:
            where = source.get(label)
            if where is None:
                continue
            section = getattr(self._widgets, where[0], None)
            button = getattr(section, where[1], None) if section else None
            if button is not None:
                button.setText(label)    # the design's capitalisation
                # ...and the design's HEIGHT. These arrive from their
                # section carrying `role="compactAction"`, which the sheet
                # pins to a smaller minimum than `role="segmented"` takes
                # from its padding: measured 26px against the Grain model
                # row's 31px, side by side, four buttons each (NAT
                # 2026-08-27, with `reference-line-of-4-buttons.png`).
                #
                # Set HERE rather than on the section's own buttons, and
                # rather than by raising `SIZE_COMPACT_BUTTON_MIN_HEIGHT`:
                # `compactAction` is also the presets row's Update and
                # Delete, which nobody asked to resize. The panel is what
                # re-presents these four as a row, so the panel is where
                # the row's shape belongs.
                button.setProperty('role', 'segmented')
                if where[0] == 'scan_frame':
                    # 2026-09-30; see design/DECISIONS.md#d-edit_panel-512
                    button.setFixedHeight(28)
                    button.setStyleSheet(
                        'QPushButton[role="segmented"], QToolButton[role="segmented"] '
                        '{ font-size: 11px; padding: 0px; min-height: 26px; }')
                button.style().unpolish(button)
                button.style().polish(button)
                buttons.append(button)
        if not buttons:
            self.missing['special'].append('btns')
            return None

        return ButtonPair(*buttons)

    def _paper_texture_buttons(self, wanted: tuple[str, ...]):
        """The paper-texture stack, plus Add and Clear.

        The STACK comes with the buttons deliberately. The design's row only
        names the two buttons, but a texture list you cannot see is a texture
        list you cannot remove from, so the section's own rows container is
        re-homed above them -- it is where the per-texture fusion mode,
        opacity and remove already live.

        `Clear` is not a button the app owns; it is built here, and it does
        real work (drops every row, then tells the controller), because a
        button in this design that does nothing would be a lie.
        """
        section = getattr(self._widgets, 'paper', None)
        add = getattr(section, 'add_button', None)
        rows_container = getattr(section, '_rows_container', None)
        if add is None or rows_container is None:
            self.missing['special'].append('btns')
            return None


        add.setText('Add texture…')
        buttons: list[QtWidgets.QWidget] = [add]

        if 'Clear' in wanted:
            clear = QtWidgets.QPushButton('Clear')
            clear.setToolTip('Remove every texture from the stack.')
            clear.setCursor(QtCore.Qt.PointingHandCursor)

            def _clear() -> None:
                for entry in list(section._rows):
                    section._drop_row(entry)
                section._emit_changed()
                _follow()

            clear.clicked.connect(_clear)
            buttons.append(clear)
        else:
            clear = None

        # 2026-08-27; see design/DECISIONS.md#d-edit_panel-513
        for button in buttons:
            button.setProperty('role', 'segmented')
            button.style().unpolish(button)
            button.style().polish(button)

        holder = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(7)
        column.addWidget(rows_container)
        column.addWidget(ButtonPair(*buttons))

        def _follow() -> None:
            empty = not section._rows
            rows_container.setVisible(not empty)
            if clear is not None:
                clear.setEnabled(not empty)

        # No signal reports a texture being added or removed on its own, so
        # follow the section's own `changed` -- which every path emits.
        section.changed.connect(_follow)
        _follow()
        return holder

    def refresh_pins(self) -> None:
        """Rebuild MAIN's pinned shortlist from the user's preference.

        Pinned rows are ALWAYS mirrors: the field's home stage keeps the one
        real editor, so pinning can never take a control off the stage it
        belongs to.
        """
        section = self._pinned_section
        if section is None:
            return
        section.clear()
        names = [name for name in row_menu.pinned_rows()
                 if self._editor_for(name) is not None]
        for name in names:
            section.add(MirrorRow(name, self._editor_for(name),
                                  self.refresh_summaries))
        # Nothing pinned draws NOTHING. The empty-state hint made sense under
        # a 'Pinned' header; with the header gone it is a stray line, and
        # MAIN's closing note already explains how to pin.
        section.setVisible(bool(names))

    def set_preview_image(self, image) -> None:
        """Hand the panel the render the analysis panel is already given.

        2026-08-27; see design/DECISIONS.md#d-edit_panel-524
        """
        for row in self._levels_rows:
            row.set_image(image)

        for view in self.findChildren(CurveEditorView):
            try:
                view.set_preview_image(image)
            except RuntimeError:
                pass          # the C++ side went away between the two lines

    # -------------------------------------------------------------- summary
    def _value_of(self, name: str):
        """What a summary formatter should SHOW for a field.

        The page's `optLabel()` maps a value to its display label before
        printing it, so the summaries read 'Kodak Portra 400 +1' rather than
        'kodak_portra_400 +1'. The stored value is untouched -- this is the
        display side only.
        """
        editor = self._editor_for(name)
        if editor is None:
            targets = spec.TUPLE_ROWS.get(name)
            if targets:
                return tuple(self._value_of(t) for t in targets)
            return 0
        try:
            value = editor.value
        except Exception:
            return 0
        display = getattr(type(editor), 'display_text_for_value', None)
        if callable(display) and isinstance(value, str):
            return display(value).split(' / ', 1)[-1]
        return value

    def release(self) -> None:
        """Let go of everything this panel points at, before it is dropped.

        WHY THIS IS NOT `deleteLater()` AND WHY IT IS NOT A REWIRING.

        The panel connects closures to signals on the BUNDLE's editors, and
        the bundle outlives the panel. A lambda has no receiver, so Qt's own
        auto-disconnect -- which does fire for bound methods -- never
        reaches them, and they capture `self`. The panel's Python wrapper
        therefore survives its own C++ widget, and `self._widgets` keeps the
        entire bundle alive with it: measured 2026-08-27, about 2000 Qt
        wrappers per build (380 QLabel, 226 FloatEditor, 140 _ResetButton,
        128 QSlider, 118 SliderFieldEditor...).

        2026-09-06; see design/DECISIONS.md#d-edit_panel-525

            none              3835 widgets   panel wrapper survives
            release()         2735 widgets   survives
            deleteLater()     1368 widgets   survives
            release+delete     136 widgets   collected

        BOTH steps are needed and neither alone is enough, so a caller that
        wants the memory back must do both. The app never pays either cost
        -- `build_controls_panel` runs once (`app.py`), so the app builds
        one panel and keeps it. What pays is anything that builds panels
        repeatedly, which in practice is the test suite (~5.4 GB).

        So this cuts the edge that matters rather than every edge. A
        retained panel wrapper is a few KB; a retained BUNDLE is those two
        thousand widgets. Letting go of the bundle and the panel's own
        caches takes the retention from ~2000 objects per build to ~200.

        Safe to call more than once, and safe to call on a panel whose C++
        side has already gone.
        """
        self._widgets = None
        for name in ('_groups', '_levels_rows', '_crop_pads', '_fx_panels'):
            container = getattr(self, name, None)
            if container is not None:
                try:
                    container.clear()
                except Exception:
                    pass
        popovers = getattr(self, '_popovers', None)
        if popovers is not None:
            try:
                popovers.clear()
            except Exception:
                pass
        self._pinned_section = None

    def _on_summary_source_changed(self, *_) -> None:
        """`refresh_summaries`, as a BOUND METHOD rather than a lambda.

        The senders here are the BUNDLE's editors, which outlive this panel.
        A lambda has no receiver, so Qt keeps it for as long as the sender
        lives -- and it captured `self`, so the panel stayed alive, and with
        it `self._widgets`, and with that every widget in the bundle.

        2026-08-27; see design/DECISIONS.md#d-edit_panel-526

            dict <- EditPanel <- cell <- tuple
                    <- function EditPanel._add_row.<locals>.<lambda>

        A bound method of a QObject carries that QObject as the receiver, so
        Qt drops the connection when the panel is destroyed. `*_` because
        the signals carry a value this does not want.
        """
        self.refresh_summaries()

    def refresh_summaries(self) -> None:
        """Recompute every group header's right-aligned summary.

        Also nudges any open FX preview: every row calls this, and any
        parameter may be one the preview draws.
        """
        self.refresh_fx_previews()
        for group, section in self._groups:
            if group.meta is None:
                continue
            try:
                section.set_meta(str(group.meta(self._value_of)))
            except Exception:
                section.set_meta('')

    def missing_rows(self) -> dict[str, list[str]]:
        return {kind: sorted(set(values)) for kind, values in self.missing.items()}


def _nd_glass() -> dict:
    """ND key -> the page's glass: `oklch(.86 - d/4 * .72, 0, 0)`, a neutral
    grey that darkens with density (zero chroma, so L alone sets it)."""

    glass = {}
    for density in ND_DENSITIES:
        lightness = 0.86 - min(density, 4.0) / 4.0 * 0.72
        linear = lightness ** 3                       # OKLab L -> Y, grey
        encoded = (12.92 * linear if linear <= 0.0031308
                   else 1.055 * linear ** (1 / 2.4) - 0.055)
        level = max(0, min(255, round(encoded * 255)))
        glass[nd_filter_key(density)] = '#%02X%02X%02X' % (level, level, level)
    return glass


def _connect_value_signal(editor, slot) -> None:
    """Connect whichever change signal an editor has (the house editor
    contract: `.value` plus one of these)."""
    for name in ('valueChanged', 'toggled', 'stateChanged', 'currentTextChanged'):
        signal = getattr(editor, name, None)
        if signal is not None and hasattr(signal, 'connect'):
            signal.connect(slot)
            return


def _library_pool(kind: str, combo) -> list[str]:
    """Every value the selector can hold, as its library: the combo's
    selectable items, less "none", the separators, the custom-stock
    creator and -- for the filter rows -- the NDs, which have their own."""

    model = combo.model()
    values = []
    for index in range(combo.count()):
        value = combo.itemText(index)
        item = model.item(index) if hasattr(model, 'item') else None
        if item is not None and not (item.flags() & Qt.ItemIsSelectable):
            continue
        if value in ('', 'none', '__custom__') or value.startswith('Custom'):
            continue
        is_nd = nd_filter_density(value) is not None
        if (kind == 'nd') != is_nd and kind in ('filter', 'nd'):
            continue
        values.append(value)
    return values
