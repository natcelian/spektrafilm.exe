"""The SCAN tab's sections: the frame, scan exposure and its levels,
response, colour, toning and curve groups, gamut compression and the scanner.
"""

from __future__ import annotations

from typing import Any

from qtpy import QtCore, QtWidgets

from spektrafilm_gui.crop_frame import oriented_ratio, parse_ratio, RATIO_PRESETS
from spektrafilm_gui.curve_widget import CurveEditorView
from spektrafilm_gui.state import (InputGamutCompressState, OutputGamutCompressState,
                                   ScanFinishingState)
from spektrafilm_gui.widget_primitives import normalize_ui_text as _normalize_ui_text
from spektrafilm_gui.widget_sections import (DataclassSection, SimpleDataclassSection, SimulationSection, _build_button, _build_collapsible_form_section, _build_linked_form_section, _compound_spec_row, _knob_row, _new_form_layout, _spec_row)

QFormLayout = QtWidgets.QFormLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QPushButton = QtWidgets.QPushButton
QSizePolicy = QtWidgets.QSizePolicy
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget
Signal = QtCore.Signal


class ScanFrameSection(QWidget):
    """SCAN tab 'Frame': the complete crop tool on the RENDERED film —
    aspect-ratio selector, Manual (draw on the image) and Move tools, plus
    skew and anamorphic desqueeze. All editors are borrowed from the
    scan_finishing master section; the controller owns the on-canvas
    interaction and listens to ``tool_changed``."""

    tool_changed = Signal(str)                   # 'off' | 'frame' | 'line'
    crop_reset_requested = Signal()              # back to the original photo format
    orientation_swap_requested = Signal()        # landscape <-> portrait (LR's X)
    ratio_changed = Signal(str)                  # preset picked (re-fit armed frame)
    # the crop session's controls, which live in this group (Cancel · Done,
    # Constrain to image, the orientation pair); the controller applies them
    crop_cancel_requested = Signal()
    crop_done_requested = Signal()
    constrain_toggled = Signal(bool)
    orientation_chosen = Signal(bool)            # True = portrait
    session_open_changed = Signal(bool)          # Cancel · Done show while cropping
    reveal_requested = Signal()                  # show this group (cropping began elsewhere)
    display_changed = Signal()                   # ratio or orientation set without a pick

    # Phase 9 presets: the historical set + Original + Cinema + Vertical/social
    # (defined once in crop_frame.RATIO_PRESETS).
    ASPECT_RATIOS = None  # set in __init__ from crop_frame

    def __init__(self, scan_finishing: 'ScanFinishingSection'):
        super().__init__()

        if type(self).ASPECT_RATIOS is None:
            type(self).ASPECT_RATIOS = RATIO_PRESETS

        self.ratio_combo = QtWidgets.QComboBox()
        # 2026-07-22; see design/DECISIONS.md#d-widget_sections-888
        self.ratio_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.ratio_combo.setMinimumContentsLength(6)
        for ratio in RATIO_PRESETS:
            self.ratio_combo.addItem(_normalize_ui_text(ratio))
        self.ratio_combo.setToolTip(_normalize_ui_text(
            'Aspect preset. Anything but Free LOCKS the crop frame to the ratio '
            '(handle drags stay constrained); picking a preset re-fits the frame. '
            'Original = the image\'s own aspect.'))
        # the ratio before the last pick, so a crop the pick opened can put it back
        self.previous_ratio = self.ratio_combo.currentText()
        self._ratio_text = self.previous_ratio
        self.ratio_combo.currentTextChanged.connect(self._ratio_picked)

        # Phase 9: ONE persistent Lightroom-style crop tool (replaces the old
        # draw-then-move pair) + a straighten-line tool + a crop reset.
        self.crop_button = QPushButton(_normalize_ui_text('Crop'))
        self.crop_button.setProperty('role', 'compactAction')
        self.crop_button.setToolTip(_normalize_ui_text(
            'Lightroom-style crop: a persistent frame with 8 handles over the full scan. '
            'Drag handles to resize (the aspect preset locks the ratio), drag inside to '
            'move the image under the frame (Shift = fine). Enter or double-click '
            'applies, Esc cancels, toggling the tool off applies.'))
        self.crop_button.setCheckable(True)
        self.straighten_button = QPushButton(_normalize_ui_text('Straighten'))
        self.straighten_button.setProperty('role', 'compactAction')
        self.straighten_button.setToolTip(_normalize_ui_text(
            'Drag a line along a horizon or vertical on the image; the skew angle is set '
            'so the line comes out level, and the crop session opens with the frame '
            'fitted inside the rotated image (Done applies both).'))
        self.straighten_button.setCheckable(True)
        self.reset_button = QPushButton(_normalize_ui_text('Reset'))
        self.reset_button.setProperty('role', 'compactAction')
        self.reset_button.setToolTip(_normalize_ui_text(
            'Reset the crop to the original photo format (full frame, ratio Free).'))
        self.reset_button.clicked.connect(self.crop_reset_requested.emit)
        self.swap_button = QPushButton(_normalize_ui_text('Swap'))
        self.swap_button.setProperty('role', 'compactAction')
        self.swap_button.setToolTip(_normalize_ui_text(
            'Swap the crop landscape <-> portrait (like Lightroom\'s X): the frame\'s '
            'dimensions exchange, shrinking only if the swapped shape no longer fits; '
            'a locked ratio preset flips with it (16:9 <-> 9:16).'))
        self.swap_button.clicked.connect(self.orientation_swap_requested.emit)
        self.crop_button.toggled.connect(lambda on: self._on_tool_toggled('frame', on))
        self.straighten_button.toggled.connect(lambda on: self._on_tool_toggled('line', on))
        from spektrafilm_gui.dyeworks_buttons import ToggleSwitch
        from spektrafilm_gui.dyeworks_icons import dyeworks_icon
        from spektrafilm_gui.theme_palette import (BORDER_LINE, BORDER_LINE_STRONG, CONTROL_BG,
                                                   CONTROL_BG_HOVER, TEXT_BRIGHT, TEXT_SOFT)
        from qtpy import QtGui

        # the same command as the viewing rail's crop button, with a name
        self.crop_button.setIcon(dyeworks_icon('crop', TEXT_BRIGHT))
        self.crop_button.setToolTip(_normalize_ui_text(
            'Crop & straighten: drag the handles to resize, inside to move, outside '
            'the photo to turn (Shift = fine). Click again to apply.'))
        self.straighten_button.setIcon(dyeworks_icon('straighten', TEXT_BRIGHT))
        self.straighten_button.setToolTip(_normalize_ui_text(
            'Draw a line along a horizon or a vertical; the angle is set so it '
            'comes out level.'))
        self.reset_button.setToolTip(_normalize_ui_text(
            'Everything in Frame back: the whole photo, 0°, Free, no desqueeze. One undo step.'))
        self.cancel_button = QPushButton(_normalize_ui_text('Cancel  Esc'))
        self.cancel_button.setProperty('role', 'compactAction')
        self.cancel_button.setToolTip(_normalize_ui_text(
            'Leave the photo exactly as it was before (Esc)'))
        self.cancel_button.clicked.connect(self.crop_cancel_requested.emit)
        self.done_button = QPushButton(_normalize_ui_text('Done  ↵'))
        self.done_button.setProperty('role', 'compactAction')
        self.done_button.setToolTip(_normalize_ui_text(
            'Apply the angle and the crop as one undo step (Enter)'))
        self.done_button.clicked.connect(self.crop_done_requested.emit)
        self.constrain_toggle = ToggleSwitch()
        self.constrain_toggle.setChecked(True)
        self.constrain_toggle.setToolTip(_normalize_ui_text(
            'On: the frame stays inside the turned photo. Off: the frame may '
            'include the corners, which come out white.'))
        self.constrain_toggle.toggled.connect(self.constrain_toggled.emit)
        # landscape / portrait, at the right end of the aspect ratio row
        self.orientation_toggle = QWidget()
        pair = QHBoxLayout(self.orientation_toggle)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(0)
        group = QtWidgets.QButtonGroup(self.orientation_toggle)
        group.setExclusive(True)
        # the app's segmented look (theme_styles `.segrow`), which the sheet
        # only gives push buttons: CONTROL_BG, and checked a lighter fill,
        # the strong line and a bright glyph
        cell = ('QToolButton {{ border: 1px solid {line}; background: {bg}; padding: 0px; {corners} }}'
                'QToolButton:hover {{ background: {on}; }}'
                'QToolButton:checked {{ background: {on}; border: 1px solid {strong}; }}')
        self.landscape_button = QtWidgets.QToolButton()
        self.portrait_button = QtWidgets.QToolButton()
        for button, glyph, tip, corners in (
                (self.landscape_button, 'landscape', 'Landscape',
                 'border-top-left-radius: 6px; border-bottom-left-radius: 6px;'),
                (self.portrait_button, 'portrait', 'Portrait',
                 'border-top-right-radius: 6px; border-bottom-right-radius: 6px; border-left: 0px;')):
            button.setCheckable(True)
            button.setFixedSize(26, 24)
            icon = QtGui.QIcon()
            icon.addPixmap(dyeworks_icon(glyph, TEXT_SOFT).pixmap(28, 28),
                           QtGui.QIcon.Normal, QtGui.QIcon.Off)
            icon.addPixmap(dyeworks_icon(glyph, TEXT_BRIGHT).pixmap(28, 28),
                           QtGui.QIcon.Normal, QtGui.QIcon.On)
            button.setIcon(icon)
            button.setIconSize(QtCore.QSize(14, 14))
            button.setToolTip(_normalize_ui_text(tip))
            button.setAccessibleName(tip)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setStyleSheet(cell.format(line=BORDER_LINE, bg=CONTROL_BG, on=CONTROL_BG_HOVER,
                                             strong=BORDER_LINE_STRONG, corners=corners))
            group.addButton(button)
            pair.addWidget(button)
        self._portrait_shown = False
        self.landscape_button.setChecked(True)
        self.landscape_button.clicked.connect(lambda: self._orientation_clicked(False))
        self.portrait_button.clicked.connect(lambda: self._orientation_clicked(True))

        # Phase 18F: the ratio SELECTOR takes its own line and the four
        # actions sit under it as the design's `.quad` (four equal columns).
        # On one line these five summed to a 449px minimum -- wider than the
        # whole edit panel, which is why the panel could not reach its 372.
        tool_column = QVBoxLayout()
        tool_column.setContentsMargins(0, 0, 0, 0)
        tool_column.setSpacing(6)
        tool_column.addWidget(self.ratio_combo)
        quad = QHBoxLayout()
        quad.setContentsMargins(0, 0, 0, 0)
        quad.setSpacing(6)
        for button in (self.swap_button, self.reset_button,
                       self.crop_button, self.straighten_button):
            quad.addWidget(button, 1)
        quad_container = QWidget()
        quad_container.setLayout(quad)
        tool_column.addWidget(quad_container)
        session_row = QHBoxLayout()
        session_row.setContentsMargins(0, 0, 0, 0)
        session_row.setSpacing(6)
        for widget in (self.constrain_toggle, self.orientation_toggle,
                       self.cancel_button, self.done_button):
            session_row.addWidget(widget)
        session_container = QWidget()
        session_container.setLayout(session_row)
        tool_column.addWidget(session_container)
        tool_container = QWidget()
        tool_container.setLayout(tool_column)
        tool_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.setLayout(_build_linked_form_section(
            'Frame',
            [
                _spec_row('scan_finishing', 'crop', scan_finishing.crop),
                _spec_row('scan_finishing', 'crop_center', scan_finishing.crop_center),
                _spec_row('scan_finishing', 'crop_size', scan_finishing.crop_size),
                _spec_row('scan_finishing', 'skew_deg', scan_finishing.skew_deg),
                _spec_row('scan_finishing', 'desqueeze', scan_finishing.desqueeze),
            ],
            expanded=True,
            extra_top_widget=tool_container,
        ))

    def _on_tool_toggled(self, tool: str, on: bool) -> None:
        if on:
            other = self.straighten_button if tool == 'frame' else self.crop_button
            if other.isChecked():
                other.blockSignals(True)
                other.setChecked(False)
                other.blockSignals(False)
            self.tool_changed.emit(tool)
        elif not (self.crop_button.isChecked() or self.straighten_button.isChecked()):
            self.tool_changed.emit('off')

    def _ratio_picked(self, text: str) -> None:
        self.previous_ratio, self._ratio_text = self._ratio_text, text
        self.ratio_changed.emit(text)

    def show_ratio(self, text: str) -> None:
        """Set the ratio without asking for a refit (Cancel, Reset put one
        back), keeping `previous_ratio` in step."""
        was_blocked = self.ratio_combo.blockSignals(True)
        self.ratio_combo.setCurrentText(text)
        self.ratio_combo.blockSignals(was_blocked)
        self._ratio_text = self.ratio_combo.currentText()
        self.display_changed.emit()

    def portrait(self) -> bool:
        return self._portrait_shown

    def set_portrait(self, portrait: bool) -> None:
        """Show the frame's orientation (no signal: it reports, not asks)."""
        self._portrait_shown = bool(portrait)
        (self.portrait_button if portrait else self.landscape_button).setChecked(True)
        self.display_changed.emit()

    def _orientation_clicked(self, portrait: bool) -> None:
        # The frame decides what the pair shows: put it back, and ask. The
        # controller turns the frame and shows its new shape.
        shown = self._portrait_shown
        self.set_portrait(shown)
        if portrait != shown:
            self.orientation_chosen.emit(portrait)

    def set_constrain(self, on: bool) -> None:
        was_blocked = self.constrain_toggle.blockSignals(True)
        self.constrain_toggle.setChecked(bool(on))
        self.constrain_toggle.blockSignals(was_blocked)

    def set_session_open(self, open_: bool) -> None:
        self.session_open_changed.emit(bool(open_))

    def release_tools(self) -> None:
        for button in (self.crop_button, self.straighten_button):
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)

    def aspect_ratio(self) -> float | None:
        """Numeric ratio of the selected preset in the chosen orientation
        (None for Free/Original — Original needs the image size, resolved
        controller-side)."""
        return oriented_ratio(parse_ratio(self.ratio_combo.currentText()), self.portrait())


class ScanFinishingSection(DataclassSection):
    """SCAN tab master section ('Scan exposure'): the scanner's analog side —
    master bypass, exposure and channel gains. The remaining editors are
    hidden here and BORROWED by the sibling wrapper sections (Levels /
    Response / Color / Toning), all writing into the one scan_finishing
    state. Auto levels asks the controller to measure the rendered frame's
    per-channel floors/ceilings — the analysis vocabulary the future
    real-film inversion will build on."""

    auto_levels_requested = Signal()

    def __init__(self):
        super().__init__(
            state_cls=ScanFinishingState,
            section_name='scan_finishing',
            title='Exposure',
            collapsed_by_default=False,
            hidden_fields={
                'crop', 'crop_center', 'crop_size', 'skew_deg', 'desqueeze',
                # 2026-07-22; see design/DECISIONS.md#d-widget_sections-898
                'gain_red_ev', 'gain_green_ev', 'gain_blue_ev',
                'black_point', 'white_point', 'black_point_rgb', 'white_point_rgb',
                'gamma', 'contrast', 'toe', 'shoulder', 'micro_contrast',
                'color_separation', 'saturation', 'vibrance',
                'hue_saturation_ryg', 'hue_saturation_cbm',
                'shadow_tint_hue', 'shadow_tint_strength',
                'midtone_tint_hue', 'midtone_tint_strength',
                'highlight_tint_hue', 'highlight_tint_strength',
                'curve_luma', 'curve_r', 'curve_g', 'curve_b',
            },
        )

    def _add_extra_rows_after(self, form: QFormLayout) -> None:
        self.channel_gain_row = _knob_row('scan_finishing', (
            (self.gain_red_ev, 'gain_red_ev', 'r', 'c↔r'),
            (self.gain_green_ev, 'gain_green_ev', 'g', 'm↔g'),
            (self.gain_blue_ev, 'gain_blue_ev', 'b', 'y↔b'),
        ))
        form.addRow(QLabel(_normalize_ui_text('Gains ev')),
                    self.channel_gain_row)

    _CURVE_FIELDS = ('curve_luma', 'curve_r', 'curve_g', 'curve_b')

    def _build_editor(self, field_name: str, annotation: Any) -> QWidget:
        # The point-curve fields are tuples of (x, y) POINTS, not numbers —
        # they get the invisible holder the ScanCurveSection's plot binds to.
        if field_name in self._CURVE_FIELDS:
            from spektrafilm_gui.curve_widget import CurvePointsEditor

            return CurvePointsEditor()
        # 2026-07-22; see design/DECISIONS.md#d-widget_sections-899
        if field_name in ('hue_saturation_ryg', 'hue_saturation_cbm'):
            from spektrafilm_gui.widget_slider import HUE_TINTS, KnobTripletEditor

            keys = 'ryg' if field_name.endswith('ryg') else 'cbm'
            return KnobTripletEditor(
                3, decimals=2, tints=[HUE_TINTS[key] for key in keys])
        return super()._build_editor(field_name, annotation)

    def _init_extra_widgets(self) -> None:
        self.auto_levels_button = _build_button(
            'Auto levels', self.auto_levels_requested.emit,
            tooltip='Measure the rendered output (0.1 / 99.9 percentiles) and set the master '
                    'black/white points + a midtone gamma from it. Luminance only: the '
                    'per-channel offsets are reset to zero, so no cast is introduced.',
            role='compactAction',
        )


class ScanLevelsSection(QWidget):
    def __init__(self, scan_finishing: ScanFinishingSection):
        super().__init__()
        self.setLayout(_build_linked_form_section(
            'Levels',
            [
                _spec_row('scan_finishing', 'black_point', scan_finishing.black_point),
                _spec_row('scan_finishing', 'white_point', scan_finishing.white_point),
                _spec_row('scan_finishing', 'black_point_rgb', scan_finishing.black_point_rgb),
                _spec_row('scan_finishing', 'white_point_rgb', scan_finishing.white_point_rgb),
                _spec_row('scan_finishing', 'gamma', scan_finishing.gamma),
            ],
            expanded=True,
            extra_top_widget=scan_finishing.auto_levels_button,
        ))


class ScanResponseSection(QWidget):
    def __init__(self, scan_finishing: ScanFinishingSection):
        super().__init__()
        self.setLayout(_build_linked_form_section(
            'Response',
            [
                _spec_row('scan_finishing', 'contrast', scan_finishing.contrast),
                _spec_row('scan_finishing', 'toe', scan_finishing.toe),
                _spec_row('scan_finishing', 'shoulder', scan_finishing.shoulder),
                _spec_row('scan_finishing', 'micro_contrast', scan_finishing.micro_contrast),
            ],
            expanded=False,
        ))


class ScanColorSection(QWidget):
    def __init__(self, scan_finishing: ScanFinishingSection):
        super().__init__()
        self.setLayout(_build_linked_form_section(
            'Color',
            [
                _spec_row('scan_finishing', 'color_separation', scan_finishing.color_separation),
                _spec_row('scan_finishing', 'saturation', scan_finishing.saturation),
                _spec_row('scan_finishing', 'vibrance', scan_finishing.vibrance),
                _spec_row('scan_finishing', 'hue_saturation_ryg', scan_finishing.hue_saturation_ryg),
                _spec_row('scan_finishing', 'hue_saturation_cbm', scan_finishing.hue_saturation_cbm),
            ],
            expanded=False,
        ))


class ScanToningSection(QWidget):
    def __init__(self, scan_finishing: ScanFinishingSection):
        super().__init__()
        self.setLayout(_build_linked_form_section(
            'Toning',
            [
                _spec_row('scan_finishing', 'shadow_tint_hue', scan_finishing.shadow_tint_hue),
                _spec_row('scan_finishing', 'shadow_tint_strength', scan_finishing.shadow_tint_strength),
                # Between the other two: the section reads dark to light,
                # as the wheel does.
                _spec_row('scan_finishing', 'midtone_tint_hue', scan_finishing.midtone_tint_hue),
                _spec_row('scan_finishing', 'midtone_tint_strength', scan_finishing.midtone_tint_strength),
                _spec_row('scan_finishing', 'highlight_tint_hue', scan_finishing.highlight_tint_hue),
                _spec_row('scan_finishing', 'highlight_tint_strength', scan_finishing.highlight_tint_strength),
            ],
            expanded=False,
        ))


class ScanCurveSection(QWidget):
    """Phase 10A: the finishing point curve (below Toning; the engine applies
    it after split toning and before micro_contrast)."""

    def __init__(self, scan_finishing: ScanFinishingSection):
        super().__init__()

        # The section goes in too: the curve's 2x2 cells are a second VIEW
        # of gamma / contrast / toe / shoulder, bound to the very editors
        # the Response sliders drive.
        self.curve_view = CurveEditorView({
            'luma': scan_finishing.curve_luma,
            'r': scan_finishing.curve_r,
            'g': scan_finishing.curve_g,
            'b': scan_finishing.curve_b,
        }, finishing=scan_finishing)
        self.curve_view.setToolTip(
            'Lightroom-style point curve on the encoded scan: Luma applies to '
            'all channels, then the per-channel curves. Click to add a point, '
            'drag to move, double-click to remove.'
        )
        form = _new_form_layout()
        form.addRow(self.curve_view)
        self.setLayout(_build_collapsible_form_section('Curve', form, expanded=False))


class InputGamutCompressSection(SimpleDataclassSection):
    STATE_CLS = InputGamutCompressState
    SECTION_NAME = 'input_gamut_compress'
    TITLE = 'Input gamut compress'
    ENUM_FIELDS_KEY = 'input_gamut_compress'


class OutputGamutCompressSection(SimpleDataclassSection):
    STATE_CLS = OutputGamutCompressState
    SECTION_NAME = 'output_gamut_compress'
    TITLE = 'Output gamut compress'
    ENUM_FIELDS_KEY = 'output_gamut_compress'
    COLLAPSED_BY_DEFAULT = False


class ScannerSection(QWidget):
    def __init__(self, simulation_section: SimulationSection):
        super().__init__()
        self.setLayout(
            _build_linked_form_section(
                'Scanner',
                [
                    _spec_row('simulation', 'scan_lens_blur', simulation_section.scan_lens_blur),
                    _compound_spec_row(
                        'simulation',
                        'scan_white_correction',
                        simulation_section.scan_white_correction,
                        simulation_section.scan_white_level,
                    ),
                    _compound_spec_row(
                        'simulation',
                        'scan_black_correction',
                        simulation_section.scan_black_correction,
                        simulation_section.scan_black_level,
                    ),
                    _spec_row('simulation', 'scan_unsharp_mask', simulation_section.scan_unsharp_mask),
                ],
                expanded=True,
            ),
        )
