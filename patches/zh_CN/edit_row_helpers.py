"""The edit panel's row builders and value formatting: labels and baselines,
the dye row, the compact cell grid, slider stacks, and how wide a value slot
must be for the numbers it can show.
"""

from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets

from spektrafilm_gui import edit_panel_spec as spec, field_labels
from spektrafilm_gui.dyeworks_widgets import DYE_COLUMNS, SUB_LABEL_INDENT
from spektrafilm_gui.dyeworks_editors import MiniSlider, on_editor_changed, saturation_ramp_qss
from spektrafilm_gui.dyeworks_labels import _mono_font, _ui_font, LabelledRow, ValueText
from spektrafilm_gui.theme_tokens import TYPE_ROW_LABEL, TYPE_VALUE
from spektrafilm_gui.widget_specs import GUI_WIDGET_SPECS

Qt = QtCore.Qt


# 2026-08-04; see design/DECISIONS.md#d-edit_panel-479


def _apply_saturation_ramp(row) -> None:
    """Re-fill a built row's slider with the saturation ramp."""

    for slider in row.findChildren(QtWidgets.QSlider):
        slider.setStyleSheet(saturation_ramp_qss())

_WIDGET_ROW_LABELS = {
    'scan_finishing.crop_ratio': 'Aspect ratio',
}


def _widget_spec(row: str):
    section, _, field = row.partition('.')
    return GUI_WIDGET_SPECS.get(section, {}).get(field)


def _label_for(row: str) -> str:
    """THE PAGE'S label wins, where it has one."""
    page = field_labels.label_for(row)
    if page:
        try:
            from spektrafilm_gui.zh_translation import translate_field
            return translate_field(row, page)
        except Exception:
            return page
    entry = _widget_spec(row)
    if entry is not None and entry.label:
        try:
            from spektrafilm_gui.zh_translation import translate_field
            return translate_field(row, entry.label)
        except Exception:
            return entry.label
    _section, _, field = row.partition('.')
    raw_name = field.replace('_', ' ')
    try:
        from spektrafilm_gui.zh_translation import translate_field
        return translate_field(row, raw_name)
    except Exception:
        return raw_name


def _baseline_of(editor, fallback):
    """An editor's reset baseline, or `fallback` when it has none.

    Tuple editors answer with a tuple; a row showing one component of one
    wants that component, so a sequence is reduced to its first element.
    """
    getter = getattr(editor, 'baseline', None)
    if not callable(getter):
        return fallback
    try:
        value = getter()
    except Exception:
        return fallback
    if value is None:
        return fallback
    if isinstance(value, (tuple, list)) and value:
        return value[0]
    return value


def _dye_row(dye: str, tint: str, name: str, editor, on_changed=None,
             decimals: int = 0, signed: bool | None = None):
    """`openHeadPop`'s `.dyerow` — a chip, the dye's name, its value, and a slider
    filled in that dye.

    2026-08-31; see design/DECISIONS.md#d-edit_panel-514
    """

    entry = _widget_spec(name)

    def _bound(attribute: str, fallback: float) -> float:
        """The spec's value when it HAS one, including 0.0.

        2026-09-08; see design/DECISIONS.md#d-edit_panel-516
        """
        value = getattr(entry, attribute, None)
        return float(fallback if value is None else value)

    low = _bound('min_value', -60.0)
    high = _bound('max_value', 60.0)
    step = _bound('step', 1.0) or 1.0

    slider = MiniSlider(minimum=low, maximum=high, step=step, tint=tint)
    slider.set_reset(getattr(editor, 'reset_to_baseline', None))
    # 20.3: one line, dot and name | slider | value, on the CMY popover's columns
    row = LabelledRow(dye, slider, reset=getattr(editor, 'reset_to_baseline', None),
                      columns=DYE_COLUMNS)
    # 2026-09-30; see design/DECISIONS.md#d-edit_panel-482
    chip = QtWidgets.QLabel()
    chip.setFixedSize(8, 8)
    chip.setStyleSheet(f'background: {tint}; border-radius: 3px;')       # a round dot, as the page
    row.add_label_prefix(chip, gap=6)

    def _pull(*_args) -> None:
        try:
            current = float(editor.value)
        except (TypeError, ValueError):
            return
        slider.set_value(current)
        row.set_value_text(spec.fmt_num(current, decimals=decimals,
                                        signed=(low < 0) if signed is None else signed))
        row.set_modified(current != _baseline_of(editor, current))

    def _push(value: float) -> None:
        rounded = round(float(value), decimals) if decimals else round(float(value))
        if float(editor.value) != rounded:
            editor.value = rounded
        _pull()
        if on_changed is not None:
            on_changed()

    slider.moved.connect(_push)
    row.set_value_input(_push, minimum=low, maximum=high, decimals=decimals)
    on_editor_changed(editor, _pull)
    _pull()

    holder = QtWidgets.QWidget()
    column = QtWidgets.QVBoxLayout(holder)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(6)
    column.addWidget(row)
    holder.slider = slider
    return holder


def _project_default_component(row: str | None, component: int):
    """The factory value of ONE component of a tuple field.

    Phase 21E needs a reset target for the cell grids, and the tuple editors
    behind them have no baseline: `TupleEditor` never grew `set_baseline`, so
    `DataclassSection.update_baselines` skips them entirely. That is a
    PRE-EXISTING gap and a wider one than this gesture -- those rows' reset
    buttons have nothing to reset to either -- and fixing it properly means
    giving nine rows a reset button and a modified dot they do not have
    today, which is a visible change nobody asked for.

    So the gesture resolves the same value the baseline convention would
    give: `PROJECT_DEFAULT_GUI_STATE`. For a field that is not profile-synced
    -- which every one of these is -- that IS the baseline definition (Phase
    15), so this agrees with the reset button by construction on the day the
    reset button starts working.
    """
    if not row or '.' not in row:
        return None
    try:
        from spektrafilm_gui.state import PROJECT_DEFAULT_GUI_STATE

        section, _, field = row.partition('.')
        values = getattr(getattr(PROJECT_DEFAULT_GUI_STATE, section, None),
                         field, None)
        if values is None:
            return None
        return float(tuple(values)[component])
    except Exception:
        return None


# 2026-10-07; see design/DECISIONS.md#d-edit_panel-483
SUB_LINE_HEIGHT = 22
SUB_LINE_GAP = 8


def _full_sub_line(text: str, slider, value, columns) -> QtWidgets.QWidget:
    """One mini slider as a full line: the sub-label in the label column,
    indented 12 and a bit greyed out (NAT: "I liked better when small sliders
    titles were a bit greyed out"), the slider in the middle, the value in the
    value column at the value size."""

    label_px, value_px = columns
    line = QtWidgets.QWidget()
    line.setObjectName('dyeworksSubLine')
    line.setMinimumHeight(SUB_LINE_HEIGHT)
    row = QtWidgets.QHBoxLayout(line)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    caption = QtWidgets.QLabel(text)
    caption.setProperty('role', 'subLabel')
    caption.setFont(_ui_font(caption, TYPE_ROW_LABEL))
    caption.setFixedWidth(label_px)
    caption.setContentsMargins(SUB_LABEL_INDENT, 0, 0, 0)
    caption.setWordWrap(True)
    value.setProperty('role', 'value')
    value.setFont(_mono_font(TYPE_VALUE))
    value.setFixedWidth(value_px)
    value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    row.addWidget(caption, 0, Qt.AlignVCenter)
    row.addWidget(slider, 1, Qt.AlignVCenter)
    row.addWidget(value, 0, Qt.AlignVCenter)
    return line


def _mini_cell_grid(editor, specs, *, on_changed=None, columns: int = 2,
                    value_width: int = 30, value_px: int = 0,
                    row_name: str | None = None,
                    full_rows: tuple[int, int] | None = None):
    """The page's compact parameter cells: `34px label | slider | 30px value`.

    `rowSpecFilter` lays amp and sigma out this way, and the same shape
    recurs wherever a tuple's components need to be reachable without giving
    each one a full row. Each cell drives ONE component of the tuple editor,
    which stays the model.

    20.3: with `full_rows` (the container's columns), every component is a
    full line instead (`_full_sub_line`), one per line.
    """
    from spektrafilm_gui.theme_tokens import TYPE_CAPTION

    grid = QtWidgets.QWidget()
    layout = QtWidgets.QGridLayout(grid)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(10)          # `gap: 10px` between cells
    layout.setVerticalSpacing(SUB_LINE_GAP if full_rows else 6)
    if full_rows:
        columns = 1

    for index, (text, component, low, high, step, decimals) in enumerate(specs):
        cell = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(cell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        caption = QtWidgets.QLabel(text)
        caption.setProperty('role', 'caption')
        # ...and its caption, for the same reason the value's face is set
        # here: the sheet's size only lands at polish, so a row built into a
        # tab nobody has opened yet keeps the application default.
        caption.setFont(_ui_font(caption, TYPE_CAPTION))
        caption.setFixedWidth(34)
        slider = MiniSlider(minimum=low, maximum=high, step=step)
        value = ValueText()
        value.setProperty('role', 'miniValue')
        value.setFixedWidth(value_width)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # The page sizes these per widget -- 10px under a spectrum bar, 11px
        # in a tuple row -- so the SIZE is a parameter. The FACE is not: a
        # number is set in the semi-mono wherever it appears, and leaving
        # that to the sheet meant an unpolished row kept the application
        # default (see `_scalar_slider_stack`).
        from spektrafilm_gui.dyeworks_labels import _mono_font
        from spektrafilm_gui.theme_tokens import TYPE_CAPTION

        value.setFont(_mono_font(value_px or TYPE_CAPTION))

        def _pull(*_args, _c=component, _s=slider, _v=value, _d=decimals):
            try:
                current = float(editor.value[_c])
            except (AttributeError, IndexError, TypeError, ValueError):
                return
            _s.set_value(current)
            _v.setText(f'{current:.{_d}f}')

        def _push(new_value, _c=component, _pull=_pull):
            values = list(editor.value)
            values[_c] = round(float(new_value), 3)
            if tuple(values) != tuple(editor.value):
                editor.value = tuple(values)
            _pull()
            if on_changed is not None:
                on_changed()

        slider.moved.connect(_push)

        # Double-click resets THIS COMPONENT, not the whole tuple. The row's
        # own reset button restores all three, which is right for a button
        # that sits beside the label; a gesture aimed at one cell that moved
        # the other two would be a surprise (Phase 21E).
        def _reset_component(_c=component, _push=_push) -> None:
            reference = getattr(editor, 'baseline', None)
            reference = reference() if callable(reference) else None
            if reference is not None:
                try:
                    _push(float(reference[_c]))
                    return
                except (TypeError, ValueError, IndexError):
                    return
            # No baseline on this editor -- see `_project_default_component`.
            # `row_name`, NOT `row`: the per-cell loop below binds `row`
            # to a QHBoxLayout, so a parameter of that name would be
            # shadowed by the time this closure ran.
            fallback = _project_default_component(row_name, _c)
            if fallback is not None:
                _push(fallback)

        slider.set_reset(_reset_component)
        value.enable_input(_push, minimum=low, maximum=high, decimals=decimals)
        on_editor_changed(editor, _pull)
        _pull()

        if full_rows:
            caption.deleteLater()
            cell.deleteLater()
            cell = _full_sub_line(text, slider, value, full_rows)
        else:
            row.addWidget(caption, 0)
            row.addWidget(slider, 1)
            row.addWidget(value, 0)
        layout.addWidget(cell, index // columns, index % columns)
    return grid


def _scalar_slider_stack(editors, captions, parent, on_changed=None,
                         full_rows: tuple[int, int] | None = None):
    """`rowTuple` over SEPARATE scalar editors rather than one tuple.

    Same shape as `_mini_cell_grid` -- caption, mini slider, value -- but
    each line drives its own editor, because the app stores these as
    independent fields where the page names them as one row. 20.3: with
    `full_rows`, each line is a full one (`_full_sub_line`).
    """
    from spektrafilm_gui.theme_tokens import TYPE_CAPTION

    holder = QtWidgets.QWidget(parent)
    grid = QtWidgets.QVBoxLayout(holder)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(SUB_LINE_GAP if full_rows else 5)   # `gap: 5px` between components

    # 34px is the page's column, and it fits every caption the page writes
    # -- they are all four letters or fewer ('fast', 'slow', 'thr', 'lim').
    # 'shoulder' is not, and a fixed 34 cut it to 'shou'. The floor stays
    # 34 so no existing row moves.
    metrics = QtGui.QFontMetrics(holder.font())
    caption_width = max([34] + [metrics.horizontalAdvance(text) + 2
                                for text in captions])

    for index, editor in enumerate(editors):
        line = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(line)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        caption = QtWidgets.QLabel(
            captions[index] if index < len(captions) else str(index + 1))
        caption.setProperty('role', 'caption')
        caption.setFont(_ui_font(caption, TYPE_CAPTION))
        caption.setFixedWidth(caption_width)
        # The RANGE lives on the inner spin box: `SliderFieldEditor` is a
        # composite around one, and the science's bounds are the spin's.
        box = getattr(editor, '_inner', editor)
        decimals = int(box.decimals())
        slider = MiniSlider(minimum=float(box.minimum()),
                            maximum=float(box.maximum()),
                            step=float(box.singleStep()))
        slider.set_reset(getattr(editor, 'reset_to_baseline', None))
        value = ValueText()
        value.setProperty('role', 'miniValue')
        # 2026-08-27; see design/DECISIONS.md#d-edit_panel-484
        from spektrafilm_gui.dyeworks_labels import _mono_font
        from spektrafilm_gui.theme_tokens import TYPE_CAPTION

        value.setFont(_mono_font(TYPE_CAPTION))
        value.setFixedWidth(52)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        def _pull(*_args, _e=editor, _s=slider, _v=value, _d=decimals):
            try:
                current = float(_e.value)
            except (TypeError, ValueError):
                return
            _s.set_value(current)
            _v.setText(f'{current:.{_d}f}')

        def _push(new_value, _e=editor, _pull=_pull):
            if float(_e.value) != float(new_value):
                _e.value = float(new_value)
            _pull()
            if on_changed is not None:
                on_changed()

        slider.moved.connect(_push)
        value.enable_input(_push, minimum=float(box.minimum()),
                           maximum=float(box.maximum()), decimals=decimals)
        on_editor_changed(editor, _pull)
        _pull()

        if full_rows:
            caption.deleteLater()
            line.deleteLater()
            line = _full_sub_line(caption.text(), slider, value, full_rows)
        else:
            row.addWidget(caption, 0)
            row.addWidget(slider, 1)
            row.addWidget(value, 0)
        grid.addWidget(line)
    return holder


def _typed_bound(value):
    """A spec bound, or None when the field has no real limit there.

    An unbounded spin box carries Qt's own huge sentinel, and clamping a
    typed number to 1.7e308 is not a clamp -- it is a rounding error waiting
    to be reported as a bug.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) >= 1e12:
        return None
    return number


def _widest_value_text(row: str) -> str:
    """The longest string this FIELD can ever show in its value slot.

    2026-08-02; see design/DECISIONS.md#d-edit_panel-515

    Sizing every row from its OWN range also keeps the value column aligned
    down the panel, which sizing-to-content cannot.
    """
    entry = _widget_spec(row)
    low = getattr(entry, 'min_value', None) if entry is not None else None
    high = getattr(entry, 'max_value', None) if entry is not None else None
    if low is None or high is None:
        return ''
    decimals = _decimals_for(row)
    unit = _unit_for(row)
    signed = _is_signed(row)
    candidates = [
        spec.fmt_num(float(low), decimals=decimals, signed=signed) + unit,
        spec.fmt_num(float(high), decimals=decimals, signed=signed) + unit,
    ]
    return max(candidates, key=len)


def _fit_value_box(label: QtWidgets.QLabel, row: str) -> None:
    """Give a row's value slot room for the widest number it can hold."""
    widest = _widest_value_text(row)
    if not widest:
        return
    label.setMinimumWidth(label.fontMetrics().horizontalAdvance(widest) + 2)


def _unit_for(row: str) -> str:
    """Appended to the VALUE, as `fmtNum` does. Leading space included."""
    return field_labels.FIELD_UNIT.get(row, '')


def _decimals_for(row: str) -> int:
    page = field_labels.decimals_for(row)
    if page is not None:
        return page
    entry = _widget_spec(row)
    if entry is not None and entry.decimals is not None:
        return int(entry.decimals)
    step = getattr(entry, 'step', None) if entry is not None else None
    if step and float(step) >= 1:
        return 0
    return 2


def _is_signed(row: str) -> bool:
    """The page prints an explicit + only when the range crosses zero."""
    entry = _widget_spec(row)
    return bool(entry is not None and entry.min_value is not None
                and float(entry.min_value) < 0)


def _ends_with_note(stage: spec.Stage) -> bool:
    """Whether the stage closes with the design's unnamed note group."""
    return bool(stage.groups) and not stage.groups[-1].name


def _alive(widget) -> bool:
    """Whether the C++ side of a Qt object is still there.

    A popover the user closed is only hidden, but one destroyed with its
    parent leaves a live Python wrapper over a dead object.
    """
    try:
        widget.objectName()
    except RuntimeError:
        return False
    return True


def _row_owners() -> dict[str, str]:
    """Which stage HOLDS each field, for the fields named on more than one.

    The design says MAIN is a view, not a location -- everything on it also
    lives in its home stage. So the home stage keeps the real editor and
    MAIN mirrors it, not the other way round, even though MAIN builds first.
    """
    seen: dict[str, list[str]] = {}
    for stage in spec.STAGES:
        for row in spec.field_rows(stage):
            for name in spec.resolve_row(row):
                seen.setdefault(name, []).append(stage.key)

    owners: dict[str, str] = {}
    for name, stages in seen.items():
        if len(stages) < 2:
            continue
        home = next((key for key in stages if key != 'main'), stages[0])
        owners[name] = home
    return owners
