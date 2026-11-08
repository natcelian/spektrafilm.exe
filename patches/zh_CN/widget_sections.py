# -------------------------------------------------------------------------
# FORK CONTRACT -- upstream has this file too, and both sides edit it.
# OURS: the section builders for the Dyeworks edit panel -- 3,556 lines, 19% of
#   the fork's merge risk. Groups, popovers, the instrument rows, and the
#   199-parameter layout the frozen design page specifies.
# UPSTREAM: their sections build their own shell. Not a base we edited.
# ON CONFLICT: KEEP OURS. Same rule as controller.py: mine their diff for
#   new parameters, not for code.
# Convention and rationale: docs/reports/2026.08.20_fork_contracts.md
# -------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from qtpy import QtCore, QtGui, QtWidgets

QComboBox = QtWidgets.QComboBox
QFileDialog = QtWidgets.QFileDialog
QFormLayout = QtWidgets.QFormLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QLineEdit = QtWidgets.QLineEdit
QPushButton = QtWidgets.QPushButton
QSizePolicy = QtWidgets.QSizePolicy
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget
Qt = QtCore.Qt
Signal = QtCore.Signal

from spektrafilm_gui.state import (
    DisplayState,
    GrainState,
    InputImageState,
    LoadRawState,
    LoadRGBState,
    SimulationState,
    SpecialState,
)
from spektrafilm_gui.options import (
    WIDE_GAMUT_COLOR_SPACES as _WIDE_GAMUT_COLOR_SPACES,
    export_format_spec as _export_format_spec,
)
from spektrafilm_gui.persistence import load_dialog_dir, save_dialog_dir
from spektrafilm_gui.preset_store import (
    FOLDER_SEPARATOR as _PRESET_FOLDER_SEPARATOR,
    list_presets,
    user_presets_dir,
)
# Read through the palette MODULE, not a frozen name: the accent is
# choosable at runtime (theme_accent) and a `from ... import X` binds
# the value at import, where a retint can never reach it.
from spektrafilm_gui import theme_palette
from spektrafilm_gui.theme_palette import (
    SIZE_FOOTER_ITEM_SPACING,
    TEXT_DIM,
)
from spektrafilm_gui.widget_editors import BoolEditor, EnumEditor, FloatEditor, FloatTupleEditor, IntEditor, IntTupleEditor
from spektrafilm_gui.widget_primitives import (
    CollapsibleSection,
    FolderMenuComboBox,
    StarRating,
    normalize_ui_text as _normalize_ui_text,
)
from spektrafilm_gui.widget_specs import GUI_SECTION_ENUMS, get_auxiliary_spec, get_widget_spec
import os
from spektrafilm_gui.widget_slider import (
    HUE_TINTS,
    LinkedKnobRow,
    _begin_drag,
    _end_drag,
    held_value,
)
from spektrafilm_gui.app_logging import log_directory
from spektrafilm_gui.state import PROJECT_DEFAULT_GUI_STATE


def _enum_values(enum_cls):
    return [member.value for member in enum_cls]


def _build_collapsible_form_section(
    title: str,
    form: QFormLayout,
    *,
    expanded: bool,
) -> QVBoxLayout:
    content = QWidget()
    content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    content.setLayout(form)

    root = QVBoxLayout()
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    root.addWidget(CollapsibleSection(title, content, expanded=expanded))
    return root


def _new_form_layout() -> QFormLayout:
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)
    # 2026-07-22; see design/DECISIONS.md#d-widget_sections-882
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    return form


def _add_form_rows(form: QFormLayout, rows: list[tuple[str | QLabel, QWidget]]) -> None:
    for label, widget in rows:
        if isinstance(label, str):
            label = _normalize_ui_text(label)
        form.addRow(label, widget)


def _build_linked_form_section(
    title: str,
    rows: list[tuple[str | QLabel, QWidget]],
    *,
    expanded: bool,
    extra_top_widget: QWidget | None = None,
) -> QVBoxLayout:
    form = _new_form_layout()
    if extra_top_widget is not None:
        form.addRow(extra_top_widget)
    _add_form_rows(form, rows)
    return _build_collapsible_form_section(title, form, expanded=expanded)


class _WrapButton(QPushButton):
    """A push button whose label WRAPS onto a second line instead of
    widening the button (the page's `.wide` buttons do: 'Restore factory /
    settings', 'Open film stocks / folder' in the 300px Config panel, once
    20.3's copy gave them verbs). `text()` / `setText()` read and write the
    label, so callers see an ordinary button."""

    PADDING = (8, 5, 8, 5)

    def __init__(self, text: str) -> None:
        super().__init__()
        self._label = QLabel(text, self)
        self._label.setObjectName('wrapButtonLabel')
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*self.PADDING)
        layout.addWidget(self._label)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def text(self) -> str:  # noqa: D102 - QPushButton API
        return self._label.text()

    def setText(self, text: str) -> None:  # noqa: N802 - QPushButton API
        self._label.setText(text)
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API name
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API name
        left, top, right, bottom = self.PADDING
        inner = self._label.heightForWidth(max(1, width - left - right))
        return max(super().sizeHint().height(), inner + top + bottom)

    def sizeHint(self):  # noqa: N802 - Qt API name
        hint = self.layout().sizeHint()
        return QtCore.QSize(hint.width(), max(super().sizeHint().height(), hint.height()))

    def minimumSizeHint(self):  # noqa: N802 - Qt API name
        left, _top, right, _bottom = self.PADDING
        words = self._label.text().split()
        widest = max((self._label.fontMetrics().horizontalAdvance(w) for w in words), default=0)
        return QtCore.QSize(widest + left + right, super().minimumSizeHint().height())


def _build_button(
    text: str,
    callback: Any,
    *,
    tooltip: str | None = None,
    preserve_case: bool = False,
    role: str | None = None,
    wrap: bool = False,
) -> QPushButton:
    shown = text if preserve_case else _normalize_ui_text(text)
    button = _WrapButton(shown) if wrap else QPushButton(shown)
    if role is not None:
        button.setProperty('role', role)
    if tooltip:
        button.setToolTip(tooltip)
    button.clicked.connect(callback)
    return button


def _build_widget_label(section_name: str, field_name: str) -> QLabel:
    spec = get_widget_spec(section_name, field_name)
    label_text = spec.label or _format_label(field_name)
    label = QLabel(_normalize_ui_text(label_text))
    if spec.tooltip:
        label.setToolTip(spec.tooltip)
    return label


def _build_auxiliary_label(name: str) -> QLabel:
    spec = get_auxiliary_spec(name)
    label_text = spec.label or name.replace("_", " ")
    label = QLabel(_normalize_ui_text(label_text))
    if spec.tooltip:
        label.setToolTip(spec.tooltip)
    return label


def _spec_row(section_name: str, field_name: str, widget: QWidget) -> tuple[QLabel, QWidget]:
    return _build_widget_label(section_name, field_name), widget


def _compound_spec_row(section_name: str, label_field_name: str, *widgets: QWidget) -> tuple[QLabel, QWidget]:
    return _build_widget_label(section_name, label_field_name), _build_inline_container(*widgets, stretch_last=True)


def _build_button_row(*widgets: QWidget, stretch: int | None = None, spacing: int = 6) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for widget in widgets:
        if stretch is None:
            row.addWidget(widget)
        else:
            row.addWidget(widget, stretch)
    return row


def _build_inline_container(
    *widgets: QWidget,
    spacing: int = 6,
    add_stretch: bool = False,
    stretch_last: bool = False,
) -> QWidget:
    container = QWidget()
    container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    last_widget_index = len(widgets) - 1
    for index, widget in enumerate(widgets):
        if stretch_last and index == last_widget_index:
            size_policy = widget.sizePolicy()
            size_policy.setHorizontalPolicy(QSizePolicy.Expanding)
            widget.setSizePolicy(size_policy)
            layout.addWidget(widget, 1)
        else:
            layout.addWidget(widget)
    if add_stretch and not stretch_last:
        layout.addStretch(1)
    return container


def _build_vertical_container(*items: QHBoxLayout | QFormLayout | QWidget, spacing: int = 6) -> QWidget:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    layout.setAlignment(Qt.AlignTop)
    for item in items:
        if isinstance(item, QWidget):
            layout.addWidget(item)
        else:
            layout.addLayout(item)
    return container


def _set_single_collapsible_layout(widget: QWidget, title: str, content: QWidget, *, expanded: bool = True) -> None:
    root = QVBoxLayout()
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    root.addWidget(CollapsibleSection(_normalize_ui_text(title), content, expanded=expanded))
    widget.setLayout(root)


def _format_label(field_name: str) -> str:
    # a field name is all lower case: sentence case starts with a capital
    label = field_name.replace("_", " ")
    return _normalize_ui_text(label[:1].upper() + label[1:])


class DataclassSection(QWidget):
    def __init__(
        self,
        *,
        state_cls: type[Any],
        section_name: str,
        title: str,
        enum_fields: dict[str, type[Any]] | None = None,
        hidden_fields: set[str] | None = None,
        collapsed_by_default: bool = False,
    ):
        super().__init__()
        self._state_cls = state_cls
        self._section_name = section_name
        self._title = title
        self._enum_fields = enum_fields or {}
        self._hidden_fields = hidden_fields or set()
        self._collapsed_by_default = collapsed_by_default
        self._type_hints = get_type_hints(state_cls)
        self._init_extra_widgets()
        self._build_ui()
        self._apply_specs()

    def _init_extra_widgets(self) -> None:
        return

    def _build_ui(self) -> None:
        form = _new_form_layout()
        self._form = form            # 2026-07-21; see design/DECISIONS.md#d-widget_sections-883
        self._row_labels: dict[str, QWidget] = {}
        self._row_widgets: dict[str, QWidget] = {}
        self._add_extra_rows_before(form)
        for field_info in fields(self._state_cls):
            field_name = field_info.name
            annotation = self._type_hints[field_name]
            widget = self._build_editor(field_name, annotation)
            setattr(self, field_name, widget)
            if field_name not in self._hidden_fields:
                label = _build_widget_label(self._section_name, field_name)
                row = self._row_widget(field_name, widget)
                self._row_labels[field_name] = label
                self._row_widgets[field_name] = row
                form.addRow(label, row)
        self._add_extra_rows_after(form)

        content = QWidget()
        content.setLayout(form)

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        section = CollapsibleSection(self._title, content, expanded=not self._collapsed_by_default)
        # Phase 15: per-group reset in the header (visible rows only —
        # hidden fields belong to other groups' wrappers).
        section.set_reset_callback(self.reset_all)
        root.addWidget(section)
        self.setLayout(root)

    def _add_extra_rows_before(self, form: QFormLayout) -> None:
        del form
        return

    def _add_extra_rows_after(self, form: QFormLayout) -> None:
        del form
        return

    def _row_widget(self, field_name: str, widget: QWidget) -> QWidget:
        del field_name
        return widget

    def _build_editor(self, field_name: str, annotation: Any) -> QWidget:
        spec = get_widget_spec(self._section_name, field_name)
        enum_cls = self._enum_fields.get(field_name)
        if enum_cls is not None:
            if self._section_name == 'simulation' and field_name == 'film_stock':
                # Phase 10C: bundled stocks + saved custom stocks + the
                # pinned 'Custom...' creator item.
                from spektrafilm_gui.widget_editors import FilmStockSelector

                return FilmStockSelector(_enum_values(enum_cls))
            if self._section_name == 'simulation' and field_name == 'print_paper':
                # 2026-09-14; see design/DECISIONS.md#d-widget_sections-884
                from spektrafilm_gui.widget_editors import PaperSelector

                return PaperSelector(_enum_values(enum_cls))
            if self._section_name == 'input_image' and field_name in ('color_filter',
                                                                     'color_filter_2'):
                # 2026-09-18; see design/DECISIONS.md#d-widget_sections-885
                from spektrafilm_gui.widget_editors import FilterSelector

                return FilterSelector(_enum_values(enum_cls))
            return EnumEditor(_enum_values(enum_cls))
        if annotation is bool:
            return BoolEditor()
        # Phase 15: scalar rows become slider+box+reset composites (the
        # slider itself appears only once the spec provides a finite range;
        # the composite honors the .value/valueChanged editor contract, so
        # everything downstream is untouched).
        if annotation is int:
            from spektrafilm_gui.widget_slider import SliderFieldEditor

            return SliderFieldEditor(IntEditor())
        if annotation is float:
            from spektrafilm_gui.widget_slider import SliderFieldEditor

            return SliderFieldEditor(
                FloatEditor(decimals=2 if spec.decimals is None else spec.decimals))
        if get_origin(annotation) is tuple:
            element_types = get_args(annotation)
            if element_types and all(element_type is int for element_type in element_types):
                return IntTupleEditor(len(element_types))
            # Phase 15: RGB-marked 3-float triplets = three channel-tinted
            # knobs + boxes on one line (NAT's knob design).
            if spec.rgb and len(element_types) == 3:
                from spektrafilm_gui.widget_slider import KnobTripletEditor

                return KnobTripletEditor(
                    3, decimals=2 if spec.decimals is None else spec.decimals)
            return FloatTupleEditor(len(element_types), decimals=2 if spec.decimals is None else spec.decimals)
        raise TypeError(f"Unsupported field type for {self._state_cls.__name__}.{field_name}: {annotation!r}")

    def _apply_specs(self) -> None:
        for field_info in fields(self._state_cls):
            field_name = field_info.name
            spec = get_widget_spec(self._section_name, field_name)
            widget = getattr(self, field_name)
            if spec.tooltip:
                widget.setToolTip(spec.tooltip)
            if spec.min_value is not None:
                self._apply_numeric_attr(widget, 'setMinimum', spec.min_value)
            if spec.max_value is not None:
                self._apply_numeric_attr(widget, 'setMaximum', spec.max_value)
            if spec.step is not None:
                self._apply_numeric_attr(widget, 'setSingleStep', spec.step)
            # LAST, so it wins: a tuple whose parts are different things
            # names its own bounds per component. See `WidgetSpec.components`.
            if spec.components:
                self._apply_component_ranges(widget, spec.components)

    @staticmethod
    def _apply_component_ranges(widget: QWidget,
                                ranges: tuple[tuple[float, float], ...]) -> None:
        """Give each box in a tuple editor its own (min, max).

        Silent about a length mismatch on purpose: a spec that names three
        ranges for a two-component field is a spec bug, and
        `test_widget_specs` catches it against the state dataclass -- here it
        would only mean an exception at bundle construction, which takes the
        whole app down for a typo.
        """
        editors = getattr(widget, '_editors', None) or getattr(
            widget, '_knobs', None)
        if not editors:
            return
        for editor, bounds in zip(editors, ranges):
            box = getattr(editor, '_inner', editor)
            low, high = bounds
            if hasattr(box, 'setMinimum'):
                box.setMinimum(low)
                box.setMaximum(high)

    @staticmethod
    def _apply_numeric_attr(widget: QWidget, method_name: str, value: float | int) -> None:
        method = getattr(widget, method_name, None)
        if callable(method):
            method(value)
            return
        editors = getattr(widget, '_editors', None)
        if editors is not None:
            for editor in editors:
                getattr(editor, method_name)(value)

    def set_state(self, state: Any) -> None:
        for field_info in fields(self._state_cls):
            field_name = field_info.name
            getattr(self, field_name).value = getattr(state, field_name)

    def get_state(self) -> Any:
        values = {field_info.name: getattr(self, field_info.name).value for field_info in fields(self._state_cls)}
        return self._state_cls(**values)

    # ------------------------------------------------------ Phase 15 resets
    def seed_baselines(self) -> None:
        """Factory-default baselines for every reset-capable editor (called
        once at bundle creation; profile sync refreshes the synced fields)."""

        defaults = getattr(PROJECT_DEFAULT_GUI_STATE, self._section_name, None)
        if defaults is None:
            return
        self.update_baselines(defaults)

    def set_row_visible(self, field_name: str, visible: bool) -> None:
        """NAT 2026-07-21: show only the settings that matter for the
        selected options (grain model, scan-film, ...)."""
        for widget in (self._row_labels.get(field_name),
                       self._row_widgets.get(field_name)):
            if widget is not None:
                widget.setVisible(visible)

    def reset_all(self) -> None:
        """Header reset: every VISIBLE reset-capable row back to baseline
        (hidden fields are surfaced — and reset — by their wrapper groups).
        Each reset emits valueChanged; the auto-preview debounce coalesces."""
        for field_info in fields(self._state_cls):
            field_name = field_info.name
            if field_name in self._hidden_fields:
                continue
            editor = getattr(self, field_name, None)
            reset = getattr(editor, 'reset_to_baseline', None)
            if callable(reset):
                reset()

    def update_baselines(self, section_state: Any, field_names=None) -> None:
        """Set the reset targets. With field_names (the profile-sync list),
        only those fields move — reset then means 'what this stock gave
        you'; everything else keeps its factory-default baseline.

        Each baseline is stored as its editor holds it (`held_value`): one
        the box cannot show kept the reset lit for good (QA 2026-10-08)."""

        for field_info in fields(self._state_cls):
            field_name = field_info.name
            if field_names is not None and field_name not in field_names:
                continue
            editor = getattr(self, field_name, None)
            set_baseline = getattr(editor, 'set_baseline', None)
            if callable(set_baseline) and hasattr(section_state, field_name):
                set_baseline(held_value(editor, getattr(section_state, field_name)))


class SimpleDataclassSection(DataclassSection):
    STATE_CLS: type[Any]
    SECTION_NAME: str
    TITLE: str
    COLLAPSED_BY_DEFAULT = True
    ENUM_FIELDS_KEY: str | None = None
    HIDDEN_FIELDS: set[str] = set()

    def __init__(self):
        super().__init__(
            state_cls=self.STATE_CLS,
            section_name=self.SECTION_NAME,
            title=self.TITLE,
            enum_fields=GUI_SECTION_ENUMS[self.ENUM_FIELDS_KEY] if self.ENUM_FIELDS_KEY is not None else None,
            hidden_fields=self.HIDDEN_FIELDS,
            collapsed_by_default=self.COLLAPSED_BY_DEFAULT,
        )


class InputImageSection(SimpleDataclassSection):
    STATE_CLS = InputImageState
    SECTION_NAME = 'input_image'
    TITLE = 'Input'
    ENUM_FIELDS_KEY = 'input_image'
    HIDDEN_FIELDS = {
        'upscale_factor',
        'crop',
        'crop_center',
        'crop_size',
        'spectral_upsampling_method',
        'apply_hanatos2025_adaptation_window',
        'apply_hanatos2025_adaptation_surface',
        'spectral_gaussian_blur',
        'filter_uv',
        'filter_ir',
        'color_filter',
        'color_filter_2',
        'nd_filter',
        'filter_factor_compensation',
    }

    def __init__(self, filepicker_section: 'FilePickerSection'):
        self._filepicker_section = filepicker_section
        super().__init__()


class LoadRawSection(DataclassSection):
    load_requested = Signal(str)

    def __init__(self):
        super().__init__(
            state_cls=LoadRawState,
            section_name='load_raw',
            # Phase 18E: the design calls this section FILE — it is the
            # frame's import-time decisions (white balance, lens correction),
            # not an import dialog. The file PICKER inside it moves to the
            # filmstrip's `+` in 18G; the section name and state are
            # unchanged, so persistence and profile sync do not notice.
            title='File',
            enum_fields=GUI_SECTION_ENUMS['load_raw'],
            collapsed_by_default=False,   # 2026-07-11; see design/DECISIONS.md#d-widget_sections-886
        )

    def _init_extra_widgets(self) -> None:
        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setPlaceholderText(_normalize_ui_text('No raw selected'))
        self.reprocess_button = _build_button('reprocess raw', self._reprocess_raw, role='compactAction')
        self.reprocess_button.setEnabled(False)
        # Phase 17A: daylight/tungsten/custom WB is live (no reprocess), but
        # crossing as_shot in either direction needs a real re-decode. The
        # controller schedules it here; debounced so a state-apply storm
        # (roll switch, preset load) arms it at most once, and the roll
        # switch cancels it exactly like the RGB picker's. Parentless: the
        # attribute reference owns it (created on the GUI thread).
        self._reprocess_timer = QtCore.QTimer()
        self._reprocess_timer.setSingleShot(True)
        self._reprocess_timer.setInterval(400)
        self._reprocess_timer.timeout.connect(self._reprocess_raw)

    def schedule_reprocess(self) -> None:
        if self.file_path.text().strip():
            self._reprocess_timer.start()

    def cancel_scheduled_reprocess(self) -> None:
        self._reprocess_timer.stop()

    def _add_extra_rows_before(self, form: QFormLayout) -> None:
        browse_button = _build_button(
            'Select file',
            self._choose_file,
            tooltip='Load and process a raw file using rawpy, output colorspace and cctf as defined in current input widget state',
            role='compactAction',
        )
        form.addRow(_build_vertical_container(_build_button_row(self.file_path, browse_button, spacing=4), spacing=0))

    def _add_extra_rows_after(self, form: QFormLayout) -> None:
        form.addRow(self.reprocess_button)

    def _choose_file(self) -> None:
        # 2026-07-25; see design/DECISIONS.md#d-widget_sections-887
        from spektrafilm_gui.roll_session import RAW_EXTENSIONS
        raw_globs = ' '.join(f'*{ext}' for ext in sorted(RAW_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(
            self, _normalize_ui_text('Select input raw'), load_dialog_dir('raw_input'),
            f'Camera RAW ({raw_globs});;All files (*)')
        if not path:
            return
        save_dialog_dir('raw_input', str(Path(path).parent))
        self.set_path(path)
        self.load_requested.emit(path)

    def _reprocess_raw(self) -> None:
        path = self.file_path.text().strip()
        if not path:
            return
        self.load_requested.emit(path)

    def set_path(self, path: str) -> None:
        self.file_path.setText(path)
        self.reprocess_button.setEnabled(bool(path.strip()))


class PreviewCropSection(QWidget):
    """MAIN tab camera crop, back to the simple numeric form: this crop
    re-frames the film gate (grain/halation rescale). The complete scan-side
    tool (manual draw, aspect ratios, skew, desqueeze — a cutout of the
    rendered film) is the SCAN tab's Frame section."""

    def __init__(self, input_image_section: InputImageSection):
        super().__init__()
        self.setLayout(_build_linked_form_section(
            'Crop and upscale',
            [
                _spec_row('input_image', 'upscale_factor', input_image_section.upscale_factor),
                _spec_row('input_image', 'crop', input_image_section.crop),
                _spec_row('input_image', 'crop_center', input_image_section.crop_center),
                _spec_row('input_image', 'crop_size', input_image_section.crop_size),
            ],
            expanded=False,
        ))


class GrainSection(SimpleDataclassSection):
    STATE_CLS = GrainState
    SECTION_NAME = 'grain'
    TITLE = 'Grain'
    COLLAPSED_BY_DEFAULT = False
    ENUM_FIELDS_KEY = 'grain'   # Phase 11A: the 'model' dropdown

    # 2026-07-21; see design/DECISIONS.md#d-widget_sections-889
    _MODEL_FIELDS = {
        'production': {'sublayers_active', 'particle_area_um2', 'particle_scale',
                       'particle_scale_sublayers', 'density_min', 'uniformity',
                       'blur', 'blur_dye_clouds_um', 'micro_structure'},
        'preview': {'particle_area_um2', 'particle_scale', 'density_min',
                    'uniformity', 'blur'},
        'rms': {'rms_granularity', 'rms_strength', 'sublayers_active',
                'particle_scale_sublayers', 'density_min', 'uniformity',
                'blur', 'blur_dye_clouds_um', 'micro_structure'},
        'synthesis': {'density_min', 'synthesis_size', 'synthesis_amount',
                      'synthesis_sharpness', 'synthesis_quality'},
    }
    _MODEL_ROWS = ({'sublayers_active', 'particle_area_um2', 'particle_scale',
                    'particle_scale_sublayers', 'density_min', 'uniformity',
                    'blur', 'blur_dye_clouds_um', 'micro_structure',
                    'rms_granularity', 'rms_strength', 'synthesis_size',
                    'synthesis_amount', 'synthesis_sharpness',
                    'synthesis_quality'})

    def __init__(self):
        super().__init__()
        self.model.currentTextChanged.connect(self._sync_model_rows)
        self._sync_model_rows(self.model.value)

    def _sync_model_rows(self, model: str) -> None:
        wanted = self._MODEL_FIELDS.get(model, self._MODEL_FIELDS['production'])
        for field_name in self._MODEL_ROWS:
            self.set_row_visible(field_name, field_name in wanted)
    # Phase 11C: the advanced Boolean-model controls live in the collapsed
    # Grain Synthesis section below (editors still built + state-wired here).
    HIDDEN_FIELDS = {
        'synthesis_samples',
        'synthesis_mean_radius_um',
        'synthesis_radius_stddev_ratio',
        'synthesis_aperture_sigma_um',
        'synthesis_cell_size_ratio',
        'synthesis_max_radius_quantile',
        'synthesis_coverage_epsilon',
        'synthesis_max_grains_per_cell',
        'synthesis_radius_scale',
        'synthesis_layered',
        'synthesis_layer_scale',
    }


class GrainSynthesisSection(QWidget):
    """Phase 11C: the collapsed research group for the Boolean grain model,
    borrowing the GrainSection's hidden editors (EnlargerSection pattern)."""

    def __init__(self, grain_section: GrainSection):
        super().__init__()
        self.setLayout(
            _build_linked_form_section(
                'Grain synthesis',
                [
                    _spec_row('grain', name, getattr(grain_section, name))
                    for name in (
                        'synthesis_samples',
                        'synthesis_mean_radius_um',
                        'synthesis_radius_stddev_ratio',
                        'synthesis_aperture_sigma_um',
                        'synthesis_cell_size_ratio',
                        'synthesis_max_radius_quantile',
                        'synthesis_coverage_epsilon',
                        'synthesis_max_grains_per_cell',
                        'synthesis_radius_scale',
                        'synthesis_layered',
                        'synthesis_layer_scale',
                    )
                ],
                expanded=False,
            ),
        )


def _knob_row(section_name: str, entries) -> 'QWidget':
    """Build a LinkedKnobRow from (target_editor, field_name, tint_key,
    text) entries, ranges pulled from the widget specs. NAT 2026-07-22:
    parameter families stored as separate scalar fields collapse to ONE
    row of tinted knobs (enlarger YMC, chemistry RGB, preflash YM, scan
    channel gains)."""

    units = []
    for target, field_name, tint_key, text in entries:
        spec = get_widget_spec(section_name, field_name)
        step = spec.step or 0.01
        decimals = 0 if float(step) >= 1 else 2
        units.append((target, HUE_TINTS[tint_key], text,
                      float(spec.min_value), float(spec.max_value),
                      float(step), decimals))
    return LinkedKnobRow(units)


class _FaderCell(QtCore.QObject):
    """Console-fader matrix cell (NAT 2026-07-22, WIDGETS-2 'console
    faders' made minimal): a thin vertical fader plus a value box, held as
    SEPARATE widgets so the group can stagger the boxes (the inverted
    pyramid) while keeping one signal contract. Fader drags update the box
    live and COMMIT once on release; the box types/wheels as usual. The
    SELF (diagonal) fader carries the channel tint in its fill."""

    valueChanged = Signal(float)   # noqa: N815 - editor contract
    _FADER_MAX = 1.0               # fader travel (typical gammas); the box
    _STEPS = 200                   # types up to 8, the fader clamps at top

    def __init__(self, tint_name: str | None):
        super().__init__()
        self._syncing = False
        self._drag_pending = False

        self._slider = QtWidgets.QSlider(Qt.Vertical)
        self._slider.setRange(0, self._STEPS)
        self._slider.setFixedHeight(76)   # 2026-07-22; see design/DECISIONS.md#d-widget_sections-891
        fill = tint_name or TEXT_DIM
        self._slider.setStyleSheet(
            'QSlider::groove:vertical { width: 4px; border-radius: 2px;'
            ' background: #262626; }'
            f'QSlider::sub-page:vertical {{ background: #262626; }}'
            f'QSlider::add-page:vertical {{ background: {fill};'
            ' border-radius: 2px; }'
            'QSlider::handle:vertical { height: 8px; margin: 0 -4px;'
            ' border-radius: 3px; background: #e8e8e8; }')

        self._box = FloatEditor(decimals=2, minimum=0.0, maximum=8.0)
        self._box.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self._box.setSingleStep(0.02)
        # 2026-07-22; see design/DECISIONS.md#d-widget_sections-892
        self._box.setFixedWidth(54)
        self._box.setAlignment(Qt.AlignCenter)

        self._box.valueChanged.connect(self._on_box)
        self._slider.valueChanged.connect(self._on_slider)
        self._slider.sliderPressed.connect(_begin_drag)
        self._slider.sliderReleased.connect(self._on_release)

    def setToolTip(self, text: str) -> None:  # noqa: N802 - widget-style API
        self._slider.setToolTip(text)
        self._box.setToolTip(text)

    @property
    def value(self) -> float:
        return float(self._box.value)

    def setValue(self, value) -> None:  # noqa: N802 - spinbox-style API
        self._box.setValue(float(value))

    def set_silent(self, value) -> None:
        """Display sync from the hidden editors — no commit, no loop."""
        self._box.blockSignals(True)
        self._box.setValue(float(value))
        self._box.blockSignals(False)
        self._sync_slider()

    def _sync_slider(self) -> None:
        fraction = min(max(float(self._box.value) / self._FADER_MAX, 0.0), 1.0)
        self._slider.blockSignals(True)
        self._slider.setValue(int(round(fraction * self._STEPS)))
        self._slider.blockSignals(False)

    def _on_box(self, value) -> None:
        self._sync_slider()
        if not self._syncing:
            self.valueChanged.emit(float(value))

    def _on_slider(self, position: int) -> None:
        new_value = position / self._STEPS * self._FADER_MAX
        self._syncing = True
        try:
            self._box.blockSignals(True)
            self._box.setValue(new_value)     # box follows the fader live
            self._box.blockSignals(False)
        finally:
            self._syncing = False
        # Phase 16A: STREAM every move (the controller's supersede scheduler
        # coalesces; the drag-end callback fires the full-res release frame).
        self.valueChanged.emit(float(self._box.value))

    def _on_release(self) -> None:
        _end_drag()


class SpecialSection(DataclassSection):
    def __init__(self, simulation_section: 'SimulationSection'):
        self._simulation_section = simulation_section
        super().__init__(
            state_cls=SpecialState,
            section_name='special',
            title='Experimental',
            collapsed_by_default=True,
            hidden_fields={
                'film_gamma_factor',
                'print_gamma_factor',
            },
        )

    def _add_extra_rows_before(self, form: QFormLayout) -> None:
        form.addRow(_build_widget_label('simulation', 'print_illuminant'), self._simulation_section.print_illuminant)


class SpectralUpsamplingSection(QWidget):
    # NAT 2026-07-22: the method + taking filter moved to MAIN under
    # Profiles (SimulationSection.adopt_spectral_rows); the tuning knobs
    # stay here on ADVANCED.
    def __init__(self, input_image_section: InputImageSection):
        super().__init__()
        self.setLayout(
            _build_linked_form_section(
                'Spectral upsampling',
                [
                    _spec_row('input_image', 'apply_hanatos2025_adaptation_window', input_image_section.apply_hanatos2025_adaptation_window),
                    _spec_row('input_image', 'apply_hanatos2025_adaptation_surface', input_image_section.apply_hanatos2025_adaptation_surface),
                    _spec_row('input_image', 'spectral_gaussian_blur', input_image_section.spectral_gaussian_blur),
                    _spec_row('input_image', 'filter_uv', input_image_section.filter_uv),
                    _spec_row('input_image', 'filter_ir', input_image_section.filter_ir),
                ],
                expanded=True,
            ),
        )


class TuneSection(QWidget):
    def __init__(self, special_section: SpecialSection):
        super().__init__()
        self.setLayout(
            _build_linked_form_section(
                'Tune',
                [
                    _spec_row('special', 'film_gamma_factor', special_section.film_gamma_factor),
                    _spec_row('special', 'print_gamma_factor', special_section.print_gamma_factor),
                ],
                expanded=True,
            ),
        )


class FilePickerSection(DataclassSection):
    """Import RGB panel: file picker plus raw-style white balance controls."""

    load_requested = Signal(str)
    # Phase 17A: WB/tint edits re-balance the stored base live instead of
    # re-reading the file; the debounced timer below emits this.
    wb_changed = Signal()

    def __init__(self):
        super().__init__(
            state_cls=LoadRGBState,
            section_name='load_rgb',
            title='Import RGB',
            enum_fields=GUI_SECTION_ENUMS['load_rgb'],
            collapsed_by_default=True,    # Import Raw is default-open instead
        )
        self._connect_auto_reprocess_signals()

    def _init_extra_widgets(self) -> None:
        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setPlaceholderText(_normalize_ui_text('No image selected'))
        self.reprocess_button = _build_button('reprocess rgb', self._reprocess_rgb, role='compactAction')
        self.reprocess_button.setEnabled(False)
        # Phase 17A: the stored image is the un-balanced file base, so a
        # white balance / tint change re-balances it live in the controller
        # (wb_changed) instead of re-importing the file. Debounced so
        # spinner scrubbing coalesces; the reprocess button still does a
        # full re-read.
        self._reprocess_timer = QtCore.QTimer(self)
        self._reprocess_timer.setSingleShot(True)
        self._reprocess_timer.setInterval(150)
        self._reprocess_timer.timeout.connect(self.wb_changed)

    def _connect_auto_reprocess_signals(self) -> None:
        for field_info in fields(LoadRGBState):
            editor = getattr(self, field_info.name)
            for signal_name in ('toggled', 'currentTextChanged', 'valueChanged'):
                signal = getattr(editor, signal_name, None)
                if signal is not None and hasattr(signal, 'connect'):
                    signal.connect(self.schedule_reprocess)
                    break

    def schedule_reprocess(self) -> None:
        if self.file_path.text().strip():
            self._reprocess_timer.start()

    def cancel_scheduled_reprocess(self) -> None:
        self._reprocess_timer.stop()

    def _add_extra_rows_before(self, form: QFormLayout) -> None:
        browse_button = _build_button(
            'Select file',
            self._choose_file,
            tooltip='Load an RGB image (e.g. TIFF/PNG/JPEG), applying the white balance settings below',
            role='compactAction',
        )
        form.addRow(_build_vertical_container(_build_button_row(self.file_path, browse_button, spacing=4), spacing=0))

    def _add_extra_rows_after(self, form: QFormLayout) -> None:
        form.addRow(self.reprocess_button)

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, _normalize_ui_text('Select input image'), load_dialog_dir('rgb_input'))
        if not path:
            return
        save_dialog_dir('rgb_input', str(Path(path).parent))
        self.set_path(path)
        self.load_requested.emit(path)

    def _reprocess_rgb(self) -> None:
        path = self.file_path.text().strip()
        if not path:
            return
        self.load_requested.emit(path)

    def set_path(self, path: str) -> None:
        # A new path supersedes any pending debounced reprocess of the old one.
        self._reprocess_timer.stop()
        self.file_path.setText(path)
        self.reprocess_button.setEnabled(bool(path.strip()))


class GuiConfigSection(QWidget):
    save_current_as_default_requested = Signal()
    save_current_to_file_requested = Signal()
    load_from_file_requested = Signal()
    restore_factory_default_requested = Signal()
    export_lut_requested = Signal()
    open_logs_requested = Signal()
    # Phase 14 preset browser
    preset_selected = Signal(str)
    save_preset_requested = Signal()
    update_preset_requested = Signal(str)   # 2026-07-22; see design/DECISIONS.md#d-widget_sections-914
    delete_preset_requested = Signal(str)

    _PRESET_PLACEHOLDER = '(选择预设)'

    def __init__(self):
        super().__init__()
        self._build_ui()

    # ------------------------------------------------------- preset browser
    def refresh_presets(self, select: str | None = None) -> None:
        """Rebuild the dropdown from the user presets dir. ``select`` keeps /
        sets the shown entry (e.g. right after saving)."""

        current = select if select is not None else self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        try:
            self.preset_combo.clear()
            self.preset_combo.addItem(self._PRESET_PLACEHOLDER)
            for name in list_presets():
                self.preset_combo.addItem(name)
            index = self.preset_combo.findText(current)
            self.preset_combo.setCurrentIndex(index if index > 0 else 0)
        finally:
            self.preset_combo.blockSignals(False)
        self._sync_delete_enabled()

    def current_preset(self) -> str:
        text = self.preset_combo.currentText()
        return '' if text == self._PRESET_PLACEHOLDER else text

    def _on_preset_activated(self, index: int) -> None:
        del index
        name = self.current_preset()
        self._sync_delete_enabled()
        if name:
            self.preset_selected.emit(name)

    def _on_delete_preset(self) -> None:
        name = self.current_preset()
        if name:
            self.delete_preset_requested.emit(name)

    def _on_update_preset(self) -> None:
        name = self.current_preset()
        if name:
            self.update_preset_requested.emit(name)

    def _sync_delete_enabled(self) -> None:
        self.delete_preset_button.setEnabled(bool(self.current_preset()))
        self.update_preset_button.setEnabled(bool(self.current_preset()))

    def _build_ui(self) -> None:
        # 2026-07-25; see design/DECISIONS.md#d-widget_sections-915
        self.preset_combo = FolderMenuComboBox(
            separator=_PRESET_FOLDER_SEPARATOR)
        self.preset_combo.setToolTip(
            'Saved looks (.sfpreset in your user presets folder). Folders open '
            'as submenus. Selecting one applies it to every control, like Load '
            'from file. The OFX plugin\'s .spkpreset files cannot be read '
            '(their payload is obfuscated).')
        self.preset_combo.activated.connect(self._on_preset_activated)
        self.save_preset_button = _build_button(
            'Save preset...',
            self.save_preset_requested.emit,
            tooltip='Save the current look into the presets dropdown (user folder, '
                    'kept across sessions and app updates).',
            role='compactAction',
        )
        self.update_preset_button = _build_button(
            'Update',
            self._on_update_preset,
            tooltip='Overwrite the selected preset with the current look.',
            role='compactAction',
        )
        self.update_preset_button.setEnabled(False)
        self.delete_preset_button = _build_button(
            'Delete',
            self._on_delete_preset,
            tooltip='Delete the selected preset file.',
            role='compactAction',
        )
        self.delete_preset_button.setEnabled(False)

        # The design's wording, and the reason it is the design's wording:
        # a QPushButton reports its full text width as its MINIMUM, so two
        # long labels side by side set a floor the panel cannot go under.
        # 'Save current as default' + 'Restore factory default' measured 322
        # inside a 300px panel, and the scroll area has its horizontal bar
        # off, so the overflow did not scroll -- it CLIPPED, taking every
        # other row in the panel with it (NAT, 2026-08-26, 'edges still
        # bugging and getting wider than the panel'). Same disease
        # `ShrinkingLabel` was written for; nobody had checked the buttons.
        self.save_current_as_default_button = _build_button(
            'Save as defaults',
            self.save_current_as_default_requested.emit,
            tooltip='Save the current state as the default for new frames.',
            wrap=True,
        )
        self.save_current_to_file_button = _build_button(
            'Save current to file',
            self.save_current_to_file_requested.emit,
        )
        self.load_from_file_button = _build_button('Load from file', self.load_from_file_requested.emit)
        self.restore_factory_default_button = _build_button(
            'Restore factory settings',
            self.restore_factory_default_requested.emit,
            tooltip='Restore the factory default state.',
            wrap=True,
        )
        self.export_lut_button = _build_button(
            'Export LUT...',
            self.export_lut_requested.emit,
            tooltip='Bake the current film/print/scan look into a 3D LUT (.cube / .3dl / HaldCLUT) '
                    'and an optional OCIO config. Spatial and stochastic effects (grain, halation) '
                    'are excluded -- a LUT is a per-pixel transform.',
        )
        self.open_logs_button = _build_button(
            'Open logs',
            self.open_logs_requested.emit,
            tooltip='Open the application log folder (rotating spektrafilm.log). Attach the '
                    'latest log when reporting a problem.',
        )

        # Phase 18E: the selector gets its OWN line and the three actions sit
        # under it, as the design's Presets section does. On one line the four
        # controls' minimum widths added up to a 471px floor — wider than the
        # library panel — so the whole panel clipped rather than compressing.
        preset_row = QWidget()
        preset_layout = QHBoxLayout()
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(4)
        preset_layout.addWidget(self.preset_combo, 1)
        preset_row.setLayout(preset_layout)

        # 2026-07-21; see design/DECISIONS.md#d-widget_sections-916
        content = _build_vertical_container(
            preset_row,
            _build_button_row(self.save_preset_button, self.update_preset_button,
                              self.delete_preset_button),
            _build_button_row(self.export_lut_button, self.open_logs_button),
        )
        _set_single_collapsible_layout(self, 'Presets', content, expanded=True)
        self.refresh_presets()


class LocationsSection(QWidget):
    """NAT 2026-07-21: CONFIG tab shortcuts to the app's user folders."""

    def __init__(self):
        super().__init__()

        def _open(path_fn):
            def run():
                path = str(path_fn())
                os.makedirs(path, exist_ok=True)
                os.startfile(path)
            return run

        def _presets_dir():
            return user_presets_dir()

        def _stocks_dir():
            from spektrafilm.profiles.custom_stocks import user_stocks_dir
            return user_stocks_dir()

        def _logs_dir():
            return log_directory()

        content = _build_vertical_container(
            _build_button_row(
                _build_button('Open presets folder', _open(_presets_dir),
                              tooltip='Open the .sfpreset folder in Explorer', wrap=True),
                _build_button('Open film stocks folder', _open(_stocks_dir),
                              tooltip='Open the custom .sfstock folder in Explorer', wrap=True),
            ),
            _build_button_row(
                _build_button('Open logs folder', _open(_logs_dir),
                              tooltip='Open the log folder in Explorer', wrap=True),
            ),
        )
        _set_single_collapsible_layout(self, 'Locations', content, expanded=True)


class _ChevronButton(QtWidgets.QToolButton):
    """One half of the stepper's `.flip`: 28px wide, a chevron drawn in ink-2 (ink on
    hover), the second button carrying the hairline between the two."""

    def __init__(self, up: bool) -> None:
        super().__init__()
        self._up = up
        self.setObjectName('stepperFlipUp' if up else 'stepperFlipDown')
        self.setFixedSize(28, 27)
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoRaise(False)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        if self._up:
            # `.flip button + button { border-left: 1px }` -- a STRAIGHT rule,
            # full height, on the pixel grid (not the stylesheet's border,
            # which curved into the rounded corner)
            painter.fillRect(QtCore.QRect(0, 0, 1, self.height()),
                             QtGui.QColor(theme_palette.BORDER_LINE_STRONG))
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        colour = QtGui.QColor(theme_palette.TEXT_PRIMARY if self.underMouse()
                              else theme_palette.TEXT_SOFT)
        pen = QtGui.QPen(colour, 1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        d = 2.2 if self._up else -2.2
        painter.drawPolyline([QtCore.QPointF(cx - 3.5, cy + d), QtCore.QPointF(cx, cy - d),
                              QtCore.QPointF(cx + 3.5, cy + d)])
        painter.end()


class PreviewSizeStepper(QWidget):
    """Top-bar preview max size (NAT 2026-07-22): a box plus ONE up / ONE
    down arrow stepping a coherent size ladder — no slider. House editor
    contract (.value + valueChanged); specs forward via _editors."""

    LADDER = (128, 192, 256, 384, 512, 640, 768, 1024, 1280, 1536, 2048,
              2560, 3072, 4096, 5120, 6144, 8192, 10240)

    valueChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self._box = IntEditor(minimum=128, maximum=10240)
        # NAT 2026-07-25: start at the project default (1024), NOT the spinbox
        # minimum (128). preview_max_size is a machine preference that
        # apply_gui_state PRESERVES on every load, so the constructed value is
        # what sticks — an IntEditor defaulting to its minimum meant every
        # session opened at 128 until the user stepped it up by hand.
        self._box.value = 1024
        self._box.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        # `.stepper .sv`: 72px, centred, bg-0, line-2 border, radius 7, padding 5 8 (29 high)
        self._box.setObjectName('stepperValue')
        self._box.setProperty('role', 'value')
        self._box.setFixedSize(72, 29)      # IntEditor pins 24 high; the page's field is 29
        self._box.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self._box.valueChanged.connect(self.valueChanged.emit)
        self._editors = (self._box,)   # _apply_numeric_attr forwards specs here
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)                    # `.stepper { gap: 6px }`
        layout.addWidget(self._box)
        # `.stepper .flip`: ONE bordered box (bg-2, line-2, radius 7) holding two 28px
        # buttons split by a hairline. Chevrons, down then up, painted (no icon font
        # is assumed). Was a 56px box and two loose 20x24 arrow buttons.
        flip = QtWidgets.QFrame()
        flip.setObjectName('stepperFlip')
        flip.setFixedHeight(29)
        flip_layout = QHBoxLayout(flip)
        flip_layout.setContentsMargins(0, 0, 0, 0)
        flip_layout.setSpacing(0)
        for up, direction, tip in (
                (False, -1, 'Smaller preview (next size down the ladder)'),
                (True, +1, 'Larger preview (next size up the ladder)')):
            button = _ChevronButton(up)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, d=direction: self._step(d))
            flip_layout.addWidget(button)
        layout.addWidget(flip)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    def _step(self, direction: int) -> None:
        current = self._box.value
        if direction > 0:
            larger = [size for size in self.LADDER if size > current]
            target = larger[0] if larger else self._box.maximum()
        else:
            smaller = [size for size in self.LADDER if size < current]
            target = smaller[-1] if smaller else self._box.minimum()
        self._box.value = min(max(target, self._box.minimum()), self._box.maximum())

    @property
    def value(self) -> int:
        return self._box.value

    @value.setter
    def value(self, value: int) -> None:
        self._box.value = value


class DisplaySection(SimpleDataclassSection):
    STATE_CLS = DisplayState
    SECTION_NAME = 'display'
    TITLE = 'Display'
    COLLAPSED_BY_DEFAULT = False
    ENUM_FIELDS_KEY = 'display'
    # 2026-07-21, 2026-07-29, 2026-08-02; see design/DECISIONS.md#d-widget_sections-917
    HIDDEN_FIELDS: set[str] = set()

    update_preview_requested = Signal()

    def _add_extra_rows_after(self, form) -> None:
        """The `update` button follows the fields it acts on.

        It came off the top-bar strip with them. The design has no button
        for it -- refreshing is the top bar's Preview -- but this one clears
        the preview CACHE as well, which nothing else does, so it keeps a
        home rather than being deleted.
        """
        form.addRow('', self.update_preview_button)

    def _init_extra_widgets(self) -> None:
        self.update_preview_button = _build_button(
            'update',
            self.update_preview_requested.emit,
            preserve_case=True,
            role='compactAction',
        )
        self.update_preview_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def _build_editor(self, field_name: str, annotation: Any) -> QWidget:
        # 2026-07-22; see design/DECISIONS.md#d-widget_sections-918
        if field_name == 'preview_max_size':
            return PreviewSizeStepper()
        return super()._build_editor(field_name, annotation)


class MetadataPanel(QWidget):
    """Bottom-left Metadata panel: a few editable fields written into every
    export (single and roll). Camera / Lens Model default to the loaded file's
    own values (editable); the EXIF is not displayed."""

    _FIELDS = (
        ('artist', 'Artist', 'Exif.Image.Artist'),
        ('copyright', 'Copyright', 'Exif.Image.Copyright'),
        ('description', 'Description', 'Exif.Image.ImageDescription'),
        ('camera_model', 'Camera Model', 'Exif.Image.Model'),
        ('lens_model', 'Lens Model', 'Exif.Photo.LensModel'),
    )

    rating_changed = Signal(int)   # 0-5 stars for the CURRENT frame

    def __init__(self):
        super().__init__()

        form = _new_form_layout()
        self._editors: dict[str, QLineEdit] = {}
        for attr, label, key in self._FIELDS:
            editor = QLineEdit()
            editor.setToolTip(f'Optional; applied to every exported file ({key}). Leave empty to skip.')
            setattr(self, attr, editor)
            self._editors[key] = editor
            form.addRow(QLabel(_normalize_ui_text(label)), editor)
        # Per-frame star rating (Exif.Image.Rating on export; drives the
        # nav-bar filter and the roll-export selection).
        self.rating = StarRating(tooltip=_normalize_ui_text(
            'Star rating of the CURRENT frame (0-5). Written as Exif.Image.Rating on '
            'export; the nav-bar star filter and roll export select by it.'))
        self.rating.valueChanged.connect(self.rating_changed.emit)
        form.addRow(QLabel(_normalize_ui_text('Rating')), self.rating)

        content = QWidget()
        content.setLayout(form)
        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(CollapsibleSection(_normalize_ui_text('Metadata'), content, expanded=False))
        self.setLayout(root)

    def set_rating(self, rating: int) -> None:
        """Reflect the current frame's rating (no signal emission)."""
        self.rating.set_value(rating)

    def custom_tags(self) -> dict:
        """Non-empty editable fields as exiv2 EXIF keys (the per-frame Rating
        is merged controller-side so batch export stays per-frame)."""
        return {key: editor.text().strip() for key, editor in self._editors.items() if editor.text().strip()}

    def set_camera_lens(self, camera_model: str = '', lens_model: str = '') -> None:
        """Prefill Camera/Lens Model from the loaded file (does not overwrite a
        value the user has typed). Values are raw metadata, not UI labels, so
        they are inserted verbatim."""
        if camera_model and not self.camera_model.text().strip():
            self.camera_model.setText(str(camera_model))
        if lens_model and not self.lens_model.text().strip():
            self.lens_model.setText(str(lens_model))


class SimulationSection(DataclassSection):
    test_strip_requested = Signal()      # Phase 17C: EV test strip toggle
    loupe_requested = Signal()           # Phase 17B: loupe toggle
    shelf_requested = Signal()           # Phase 17E: library browser
    edit_stock_requested = Signal()   # Phase 10C: reopen the stock editor
    _glare_section: 'GlareSection | None'
    _scan_for_print_restore_state: dict[str, object] | None

    def adopt_spectral_rows(self, input_image_section: 'InputImageSection') -> None:
        """NAT 2026-07-22: spectral upsampling + taking filter live on the
        MAIN tab under Profiles. Editors stay input_image-owned (state and
        profile sync untouched); the rows just render here."""
        for field_name in ('spectral_upsampling_method', 'color_filter'):
            self._form.addRow(_build_widget_label('input_image', field_name),
                              getattr(input_image_section, field_name))

    def _row_widget(self, field_name: str, widget: QWidget) -> QWidget:
        if field_name != 'film_stock':
            return super()._row_widget(field_name, widget)
        # 2026-07-20; see design/DECISIONS.md#d-widget_sections-920
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(widget, 1)
        self.edit_stock_button = _build_button(
            'Edit', self.edit_stock_requested.emit,
            tooltip='Edit the selected custom stock, or design a new custom '
                    'stock based on the selected bundled stock.',
            role='compactAction',
        )
        layout.addWidget(self.edit_stock_button)
        row.setLayout(layout)
        return row

    def __init__(self):
        super().__init__(
            state_cls=SimulationState,
            section_name='simulation',
            title='Profiles',
            enum_fields=GUI_SECTION_ENUMS['simulation'],
            hidden_fields={
                'film_format_mm',
                'camera_lens_blur_um',
                'camera_diffusion_filter_active',
                'camera_diffusion_filter_family',
                'camera_diffusion_filter_strength',
                'camera_diffusion_filter_spatial_scale',
                'camera_diffusion_filter_halo_warmth',
                'camera_diffusion_filter_core_intensity',
                'camera_diffusion_filter_core_size',
                'camera_diffusion_filter_halo_intensity',
                'camera_diffusion_filter_halo_size',
                'camera_diffusion_filter_bloom_intensity',
                'camera_diffusion_filter_bloom_size',
                'exposure_compensation_ev',
                'film_exposure_ev',
                'auto_exposure',
                'auto_exposure_method',
                'auto_exposure_hold',
                'auto_exposure_held_ev',
                'print_exposure',
                'print_exposure_compensation',
                'print_y_filter_shift',
                'print_m_filter_shift',
                'print_c_filter_shift',
                'film_development_time',
                'print_shadow_shape',
                'print_highlight_shape',
                'negative_bleach_bypass',
                'negative_leuco_cyan_coupling',
                'print_bleach_bypass',
                'diffusion_filter_active',
                'diffusion_filter_family',
                'diffusion_filter_strength',
                'diffusion_filter_spatial_scale',
                'diffusion_filter_halo_warmth',
                'diffusion_filter_core_intensity',
                'diffusion_filter_core_size',
                'diffusion_filter_halo_intensity',
                'diffusion_filter_halo_size',
                'diffusion_filter_bloom_intensity',
                'diffusion_filter_bloom_size',
                'print_illuminant',
                'scan_lens_blur',
                'scan_white_correction',
                'scan_white_level',
                'scan_black_correction',
                'scan_black_level',
                'scan_unsharp_mask',
                'auto_preview',
                'scan_film',
                'output_color_space',
                'saving_color_space',
                'saving_cctf_encoding',
                'export_format',
                'export_jpeg_quality',
                'export_full_precision',
            },
        )

    def _init_extra_widgets(self) -> None:
        self._glare_section = None
        self._scan_for_print_restore_state = None
        self.bottom_auto_preview = BoolEditor()
        # On by default; apply_gui_state never overwrites it (session-scoped
        # toggle, dormant until the first render — NAT 2026-07-18).
        self.bottom_auto_preview.setChecked(True)
        self.bottom_scan_film = BoolEditor()
        # 2026-07-26, 2026-10-06; see design/DECISIONS.md#d-widget_sections-921
        self.bottom_scan_for_print = BoolEditor()
        scan_for_print_spec = get_auxiliary_spec('scan_for_print')
        if scan_for_print_spec.tooltip:
            self.bottom_scan_for_print.setToolTip(scan_for_print_spec.tooltip)
        self.bottom_scan_for_print.toggled.connect(self._apply_scan_for_print_mode)

        # (The GPU status/toggle moved to GpuBackendWidget at the top of the
        # left panel.)
        scan_film_row = QHBoxLayout()
        scan_film_row.setContentsMargins(0, 0, 0, 0)
        scan_film_row.setSpacing(SIZE_FOOTER_ITEM_SPACING)
        scan_film_row.addWidget(_build_widget_label('simulation', 'auto_preview'))
        scan_film_row.addWidget(self.bottom_auto_preview)
        scan_film_row.addSpacing(SIZE_FOOTER_ITEM_SPACING)
        scan_film_row.addWidget(_build_widget_label('simulation', 'scan_film'))
        scan_film_row.addWidget(self.bottom_scan_film)
        scan_film_row.addSpacing(SIZE_FOOTER_ITEM_SPACING)
        scan_film_row.addWidget(_build_auxiliary_label('scan_for_print'))
        scan_film_row.addWidget(self.bottom_scan_for_print)
        scan_film_row.addStretch(1)

        # Phase 17C: EV test strip — a triggered instrument (darkroom verb).
        # Session-scoped setup (not gui_state): step in EV and band count.
        self.test_strip_button = _build_button(
            'Test strip', self.test_strip_requested.emit,
            tooltip='Split the preview into vertical bands rendered at EV offsets '
                    'around the current exposure. Click a band to adopt its EV; '
                    'Esc or a new render dismisses the strip.',
            role='accentAction',
        )
        self.test_strip_step = QtWidgets.QComboBox()
        for label in ('1/3 EV', '1/2 EV', '1 EV'):
            self.test_strip_step.addItem(_normalize_ui_text(label))
        self.test_strip_step.setCurrentIndex(2)
        self.test_strip_step.setToolTip('EV difference between neighbouring bands')
        self.test_strip_bands = QtWidgets.QSpinBox()
        self.test_strip_bands.setRange(3, 9)
        self.test_strip_bands.setValue(5)
        self.test_strip_bands.setToolTip('Number of bands in the strip')
        # Phase 17E: the Shelf — library browser (stocks, papers, presets,
        # rolls), one modeless window.
        self.shelf_button = _build_button(
            'Shelf', self.shelf_requested.emit,
            tooltip='Browse everything the app can load (film stocks, custom '
                    'stocks, papers, presets and recent rolls) in one place. '
                    'Double-click applies or opens through the normal paths; '
                    'browsing never changes the image.',
            role='compactAction',
        )
        # Phase 17B: the loupe — 1:1 production-pipeline window.
        self.loupe_button = _build_button(
            'Loupe', self.loupe_requested.emit,
            tooltip='A circular window showing the image at 1:1 resolution through '
                    'the full production pipeline: the honest answer to what the '
                    'grain will look like. Move the mouse to inspect; while moving '
                    'it magnifies the preview, at rest it renders full quality. '
                    'Click to PIN the loupe on a zone (it keeps re-rendering there '
                    'as you edit); click the circle to pick it up again. Toggle '
                    'again (or Esc) to dismiss.',
            role='accentAction',
        )

        # 2026-07-29; see design/DECISIONS.md#d-widget_sections-922
        self.bottom_bar = QWidget()
        bottom_bar_layout = QVBoxLayout(self.bottom_bar)
        bottom_bar_layout.setContentsMargins(0, 0, 0, 0)
        bottom_bar_layout.setSpacing(SIZE_FOOTER_ITEM_SPACING)
        bottom_bar_layout.addLayout(scan_film_row)

    def action_bar(self) -> QWidget:
        return self.bottom_bar

    def set_auto_preview_value(self, value: bool) -> None:
        self.bottom_auto_preview.setChecked(value)

    def auto_preview_value(self) -> bool:
        return self.bottom_auto_preview.isChecked()

    def set_scan_film_value(self, value: bool) -> None:
        self.bottom_scan_film.setChecked(value)

    def scan_film_value(self) -> bool:
        return self.bottom_scan_film.isChecked()

    def bind_scan_for_print_glare_section(self, glare_section: 'GlareSection') -> None:
        self._glare_section = glare_section

    def reset_scan_for_print_value(self) -> None:
        was_blocked = self.bottom_scan_for_print.blockSignals(True)
        self.bottom_scan_for_print.setChecked(False)
        self.bottom_scan_for_print.blockSignals(was_blocked)
        self._scan_for_print_restore_state = None

    def _apply_scan_for_print_mode(self, active: bool) -> None:
        if active:
            if self._scan_for_print_restore_state is None:
                self._scan_for_print_restore_state = {
                    'scan_white_correction': self.scan_white_correction.value,
                    'scan_black_correction': self.scan_black_correction.value,
                    'glare_active': None if self._glare_section is None else self._glare_section.active.value,
                }
            self.scan_white_correction.value = True
            self.scan_black_correction.value = True
            if self._glare_section is not None:
                self._glare_section.active.value = False
            return

        restore_state = self._scan_for_print_restore_state
        if restore_state is None:
            return
        self.scan_white_correction.value = restore_state['scan_white_correction']
        self.scan_black_correction.value = restore_state['scan_black_correction']
        glare_active = restore_state['glare_active']
        if self._glare_section is not None and glare_active is not None:
            self._glare_section.active.value = glare_active
        self._scan_for_print_restore_state = None


class ExportSection(QWidget):
    """Export panel (the 'film scanner' output). Groups the independent,
    non-baked export choices -- format/bit-depth, encoding (flat-linear vs
    tonally-normal), and output colour profile -- plus a full-precision toggle
    and the Export/Cancel action. The render is full-resolution and written off
    the UI thread by the controller; 16-bit comes straight from the float
    render. Warns on 8-bit + wide-gamut (posterisation) and notes when a
    chosen (profile, encoding) pair has no embeddable ICC.
    """

    export_requested = Signal()
    export_roll_requested = Signal()
    #: a job started (True) or ended (False) -- see `set_exporting`
    exportingChanged = Signal(bool)   # noqa: N815 - Qt signal naming

    def __init__(self, simulation_section: SimulationSection):
        super().__init__()
        self._simulation = simulation_section
        self._format = simulation_section.export_format
        self._profile = simulation_section.saving_color_space
        self._encoding = simulation_section.saving_cctf_encoding
        self._full_precision = simulation_section.export_full_precision

        form = _new_form_layout()
        _add_form_rows(
            form,
            [
                _spec_row('simulation', 'export_format', self._format),
                _spec_row('simulation', 'saving_color_space', self._profile),
            ],
        )

        # Encoding is shown as a plain "Linear tones" tick: ticked = flat/linear
        # (cctf OFF, max headroom), unticked = tonally-normal. It drives the
        # hidden saving_cctf_encoding editor INVERSELY (linear == encoding off),
        # which is the field the rest of the app + the saved state read.
        self._linear = BoolEditor()
        linear_label = QLabel(_normalize_ui_text('Linear tones'))
        _linear_tip = (
            'Ticked = flat/linear: no tone curve, maximum grading headroom, looks flat on open (best for Resolve). '
            'Unticked = tonally-normal: the colour space curve is applied so the file looks correct on open '
            '(Lightroom/Capture One). The matching ICC variant is embedded either way.'
        )
        linear_label.setToolTip(_linear_tip)
        self._linear.setToolTip(_linear_tip)
        form.addRow(linear_label, self._linear)

        _add_form_rows(form, [_spec_row('simulation', 'export_full_precision', self._full_precision)])

        self._warning_label = QLabel('')
        self._warning_label.setWordWrap(True)
        self._warning_label.setProperty('role', 'warning')  # amber, advisory
        self._warning_label.setVisible(False)
        form.addRow(self._warning_label)

        self._icc_note_label = QLabel('')
        self._icc_note_label.setWordWrap(True)
        self._icc_note_label.setProperty('role', 'note')  # muted, informational
        self._icc_note_label.setVisible(False)
        form.addRow(self._icc_note_label)

        self.export_button = _build_button(
            'Export',
            self.export_requested.emit,
            tooltip='Write the image with these settings, off the UI thread. Full-precision re-renders the input; otherwise the current scan is exported as-is.',
            role='accentAction',
        )
        form.addRow(self.export_button)

        # ---- batch (roll) export --------------------------------------
        self.sync_export_settings = BoolEditor()
        self.sync_export_settings.setChecked(True)
        sync_label = QLabel(_normalize_ui_text('Sync export settings'))
        _sync_tip = (
            'On (default): every frame in the roll is written with the format / profile / '
            'encoding chosen ABOVE. Off: each frame uses the export settings stored with '
            'its own look. The per-frame LOOK settings are always the frame\'s own.'
        )
        sync_label.setToolTip(_sync_tip)
        self.sync_export_settings.setToolTip(_sync_tip)
        form.addRow(sync_label, self.sync_export_settings)

        self.sync_metadata = BoolEditor()
        self.sync_metadata.setChecked(True)
        sync_metadata_label = QLabel(_normalize_ui_text('Sync metadata'))
        _sync_metadata_tip = (
            'On (default): the Metadata panel values (Artist / Copyright / Description / '
            'Camera / Lens) as currently shown are embedded in EVERY exported frame. '
            'Off: each frame keeps only the metadata read from its own source file.'
        )
        sync_metadata_label.setToolTip(_sync_metadata_tip)
        self.sync_metadata.setToolTip(_sync_metadata_tip)
        form.addRow(sync_metadata_label, self.sync_metadata)

        # Live note on what 'Export roll' will write under the nav-bar star
        # filter (the filter is CONTROLLED there; this line only reports it).
        self.rating_filter_info = QLabel(_normalize_ui_text('Exports every frame in the roll.'))
        self.rating_filter_info.setToolTip(_normalize_ui_text(
            'Export roll only writes frames passing the star filter on the '
            'navigation bar (bottom left).'))
        self.rating_filter_info.setWordWrap(True)
        self.rating_filter_info.setProperty('role', 'note')  # muted, informational
        form.addRow(self.rating_filter_info)

        self.export_roll_button = _build_button(
            'Export roll',
            self.export_roll_requested.emit,
            tooltip='Render and write every frame PASSING the star filter above (full precision, each '
                    'with its own look) into a folder you choose. Runs off the UI thread with progress; '
                    'cancel between frames with the Cancel export button.',
            role='accentAction',
        )
        form.addRow(self.export_roll_button)

        self.setLayout(_build_collapsible_form_section('Export', form, expanded=False))

        # The (removed) Output section's colour space now MIRRORS the export
        # saving colour space: the render lands directly in the space the file
        # is written in. output_color_space is the hidden editor the pipeline
        # reads; keep it locked to the saving choice both ways.
        self._output = simulation_section.output_color_space
        self._mirror_output_color_space(self._profile.currentText())

        # Sync the linear tick from the canonical encoding field, then wire both ways.
        self._linear.value = not self._encoding.value
        self._format.currentTextChanged.connect(self._refresh_state)
        self._profile.currentTextChanged.connect(self._refresh_state)
        self._profile.currentTextChanged.connect(self._mirror_output_color_space)
        self._linear.toggled.connect(self._on_linear_toggled)
        self._encoding.toggled.connect(self._sync_linear_from_encoding)
        self._refresh_state()

    def _mirror_output_color_space(self, text: str) -> None:
        was_blocked = self._output.blockSignals(True)
        try:
            self._output.value = text
        finally:
            self._output.blockSignals(was_blocked)

    def is_exporting(self) -> bool:
        return bool(getattr(self, '_exporting', False))

    def set_exporting(self, exporting: bool) -> None:
        """Relabel the action button Export <-> Cancel while a job runs.

        2026-08-25; see design/DECISIONS.md#d-widget_sections-926
        """
        exporting = bool(exporting)
        self._exporting = exporting
        self.export_button.setText(_normalize_ui_text('Cancel export' if exporting else 'Export'))
        self.export_roll_button.setEnabled(not exporting)
        self.exportingChanged.emit(exporting)

    def sync_export_settings_value(self) -> bool:
        return self.sync_export_settings.isChecked()

    def sync_metadata_value(self) -> bool:
        return self.sync_metadata.isChecked()

    def _on_linear_toggled(self, checked: bool) -> None:
        # Linear ticked == cctf encoding OFF.
        was_blocked = self._encoding.blockSignals(True)
        self._encoding.value = not checked
        self._encoding.blockSignals(was_blocked)
        self._update_posterization_warning()

    def _sync_linear_from_encoding(self, encoded: bool) -> None:
        # External / state-load changes to the canonical field reflect into the tick.
        was_blocked = self._linear.blockSignals(True)
        self._linear.value = not encoded
        self._linear.blockSignals(was_blocked)

    def _refresh_state(self, *_args) -> None:
        self._update_encoding_availability()
        self._update_posterization_warning()

    def _update_encoding_availability(self) -> None:
        profile = self._profile.currentText()
        has_encoded, has_linear = self._icc_availability(profile)

        if has_encoded and has_linear:
            self._linear.setEnabled(True)
            self._set_icc_note('')
            return
        if has_encoded and not has_linear:
            self._force_linear(False)
            self._set_icc_note(f'No bundled ICC for linear {profile}; using normal tones and tagging via metadata.')
            return
        if has_linear and not has_encoded:
            self._force_linear(True)
            self._set_icc_note(f'No bundled ICC for normal {profile}; using linear tones and tagging via metadata.')
            return
        # Neither variant has a bundled ICC: leave the choice, note metadata-only.
        self._linear.setEnabled(True)
        self._set_icc_note(f'No bundled ICC for {profile}; the file is tagged via metadata only (no embedded profile).')

    def _force_linear(self, linear: bool) -> None:
        lb = self._linear.blockSignals(True)
        eb = self._encoding.blockSignals(True)
        self._linear.value = linear
        self._encoding.value = not linear
        self._encoding.blockSignals(eb)
        self._linear.blockSignals(lb)
        self._linear.setEnabled(False)

    def _update_posterization_warning(self) -> None:
        _ext, bit_depth = _export_format_spec(self._format.currentText())
        profile = self._profile.currentText()
        if bit_depth <= 8 and profile in _WIDE_GAMUT_COLOR_SPACES:
            self._set_warning(
                f'{bit_depth}-bit + {profile}: a wide gamut in 8-bit can posterise smooth gradients. '
                'Prefer 16-bit TIFF/PNG, or sRGB for 8-bit JPEG.'
            )
        else:
            self._set_warning('')

    def _set_warning(self, text: str) -> None:
        self._warning_label.setText(text)
        self._warning_label.setVisible(bool(text))

    def _set_icc_note(self, text: str) -> None:
        self._icc_note_label.setText(text)
        self._icc_note_label.setVisible(bool(text))

    @staticmethod
    def _icc_availability(profile: str) -> tuple[bool, bool]:
        try:
            from spektrafilm.utils.io import icc_pair_available
        except Exception:
            return True, True  # never block the UI if io import fails
        return icc_pair_available(profile, True), icc_pair_available(profile, False)


class ExposureControlSection(QWidget):
    """The camera + enlarger exposure rows.

    The camera EV field is ONE engine parameter (`exposure_compensation_ev`,
    a plain 2**ev gain on the film raw) wearing two hats, because that is
    what it physically is: with the meter ON it biases the measured
    exposure, with the meter OFF nothing else sets the exposure, so the very
    same number IS the camera exposure. The row says which one it currently
    is instead of leaving the user to infer it.

    The Meter button HOLDS the reading auto exposure would take (NAT
    2026-09-30): it keeps auto exposure on and freezes the metered EV, so the
    picture -- print included -- is exactly auto ON's and the compensation
    field adjusts from there. (Written into the manual field instead, the
    print's midgray balance lost the meter -- Andrea's rule, it follows the
    meter only while auto is on -- and the print differed.) The same button reads Release while a reading is held, and
    turning auto exposure off releases it: manual exposure keeps Andrea's
    meaning."""

    meter_requested = Signal()

    _AUTO_ON_LABEL = 'Camera exposure ev'
    _AUTO_OFF_LABEL = 'Camera exposure ev'
    _AUTO_OFF_TOOLTIP = (
        'Camera exposure in stops, set by hand (the meter is off, so this is '
        'the whole exposure). 0 is the raw file as the sensor recorded it; '
        'each +1 is one stop more light on the film.'
    )
    _METER_TOOLTIP = (
        'Measure the loaded frame the way auto exposure does and hold that '
        'reading: auto exposure stays on and exposes at it, so the picture '
        'is the one auto exposure gave, and the compensation adjusts from '
        'there. Turns auto exposure on.'
    )
    _RELEASE_TOOLTIP = (
        'Release the held reading: auto exposure meters every render again.'
    )

    def __init__(self, simulation_section: SimulationSection):
        super().__init__()
        self._auto_exposure = simulation_section.auto_exposure
        self._exposure_label = _build_widget_label('simulation', 'exposure_compensation_ev')
        self._auto_on_tooltip = get_widget_spec(
            'simulation', 'exposure_compensation_ev').tooltip or ''
        self.meter_button = _build_button(
            'Meter', self.meter_requested.emit,
            tooltip=self._METER_TOOLTIP, role='compactAction',
        )

        form = _new_form_layout()
        self._add_spec_row(form, 'simulation', 'auto_exposure', simulation_section.auto_exposure)
        form.addRow(
            self._exposure_label,
            _build_inline_container(
                simulation_section.exposure_compensation_ev,
                self.meter_button,
                stretch_last=False,
            ),
        )
        self._add_spec_row(form, 'simulation', 'film_exposure_ev', simulation_section.film_exposure_ev)
        self._add_spec_row(form, 'simulation', 'print_exposure_compensation', simulation_section.print_exposure_compensation)
        self._add_spec_row(form, 'simulation', 'print_exposure', simulation_section.print_exposure)

        self.setLayout(_build_collapsible_form_section('Exposure control', form, expanded=True))

        self._exposure_editor = simulation_section.exposure_compensation_ev
        self._hold = simulation_section.auto_exposure_hold
        self._auto_exposure.toggled.connect(self._sync_exposure_mode)
        self._hold.toggled.connect(self._sync_hold)
        self._sync_exposure_mode(self._auto_exposure.value)
        self._sync_hold(self._hold.value)

    def _sync_exposure_mode(self, auto_on: bool) -> None:
        auto_on = bool(auto_on)
        label = self._AUTO_ON_LABEL if auto_on else self._AUTO_OFF_LABEL
        tooltip = self._auto_on_tooltip if auto_on else self._AUTO_OFF_TOOLTIP
        self._exposure_label.setText(_normalize_ui_text(label))
        self._exposure_label.setToolTip(tooltip)
        self._exposure_editor.setToolTip(tooltip)
        # A held reading is auto exposure's own: manual exposure (auto off)
        # is Andrea's, where the metered EV has no such meaning, so leaving
        # auto releases the hold.
        if not auto_on and self._hold.value:
            self._hold.value = False

    def _sync_hold(self, held: bool) -> None:
        held = bool(held)
        self.meter_button.setText(_normalize_ui_text('Release' if held else 'Meter'))
        self.meter_button.setToolTip(self._RELEASE_TOOLTIP if held else self._METER_TOOLTIP)

    def _add_spec_row(self, form: QFormLayout, section_name: str, field_name: str, widget: QWidget) -> None:
        spec = get_widget_spec(section_name, field_name)
        if spec.tooltip:
            widget.setToolTip(spec.tooltip)
        form.addRow(_build_widget_label(section_name, field_name), widget)


class EnlargerSection(QWidget):
    def __init__(self, simulation_section: SimulationSection):
        super().__init__()
        # 2026-07-22; see design/DECISIONS.md#d-widget_sections-923
        self.filter_shift_row = _knob_row('simulation', (
            (simulation_section.print_c_filter_shift, 'print_c_filter_shift', 'c', 'c'),
            (simulation_section.print_m_filter_shift, 'print_m_filter_shift', 'm', 'm'),
            (simulation_section.print_y_filter_shift, 'print_y_filter_shift', 'y', 'y'),
        ))
        self.setLayout(
            _build_linked_form_section(
                'Enlarger',
                [
                    (QLabel(_normalize_ui_text('Filter shifts')), self.filter_shift_row),
                ],
                expanded=True,
            ),
        )


class DevelopmentSection(QWidget):
    """Phase 12B: the development-time picker (FILM tab), borrowing the
    simulation section's hidden editor — B&W stocks only, 0 = default."""

    def __init__(self, simulation_section: SimulationSection):
        super().__init__()
        self.setLayout(
            _build_linked_form_section(
                'Development',
                [
                    _spec_row('simulation', 'film_development_time',
                              simulation_section.film_development_time),
                ],
                expanded=False,
            ),
        )
