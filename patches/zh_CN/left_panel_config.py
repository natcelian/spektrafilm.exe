"""The library panel's CONFIG view: the Display, GPU, UI and Shortcuts groups,
and the GPU memory and backend settings behind them.
"""

from __future__ import annotations

import os
import weakref

from qtpy import QtCore, QtWidgets

from spektrafilm.gpu import vendor
from spektrafilm_gui import theme_palette as _palette
from spektrafilm_gui.app_settings import app_settings
from spektrafilm_gui.dyeworks_buttons import SegmentedEnum, WideButton
from spektrafilm_gui.dyeworks_chips import ChipComboBox
from spektrafilm_gui.dyeworks_editors import MiniSlider, OverflowRings, SIDE_COLUMNS, slider_row
from spektrafilm_gui.dyeworks_labels import _mono_font, _ui_font, Hint, LabelledRow, ShrinkingLabel
from spektrafilm_gui.edit_panel import EditRow
from spektrafilm_gui.canvas_backgrounds import CANVAS_BACKGROUND_OPTIONS, DEFAULT_CANVAS_BACKGROUND
from spektrafilm_gui.theme_accent import ACCENTS, current_key, set_accent
from spektrafilm_gui.theme_tokens import TYPE_CAPTION
from spektrafilm_gui.ui_scaling import automatic_summary, scale_mode, SCALE_MODES, set_scale_mode
from spektrafilm_gui.widget_primitives import CollapsibleSection
from spektrafilm_gui.dyeworks_widgets import DyeworksSection

Qt = QtCore.Qt


# 2026-09-24; see design/DECISIONS.md#d-left_panel_sections-591
DISPLAY_ROWS = (
    'display.use_display_transform',
    'simulation.output_color_space',
    'display.white_padding',
    'display.preview_max_size',
    'simulation.auto_preview',
)


def display_section(widgets):
    """`CFG_GROUPS` -> Display, as dyeworks rows over the app's own editors.

    The legacy section had four of the page's six -- `output_color_space`
    and `auto_preview` were nowhere on Config -- and named them in lower
    case off the field name ('use display transform'). These are the page's
    labels, from the generated table, in the page's order.
    """

    section = DyeworksSection('显示与色彩管理', expanded=True, compact=True)
    holder = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(section)

    for name in DISPLAY_ROWS:
        group, _, field = name.partition('.')
        editor = getattr(getattr(widgets, group, None), field, None)
        if editor is None:
            continue
        row = EditRow(name, editor, None, columns=SIDE_COLUMNS)   # 20.3: the left panel's columns
        if field == 'preview_max_size':
            _dress_stepper(row, editor)
        section.add(row)
    holder.section = section
    return holder


def _dress_stepper(row, editor) -> None:
    """`preview_max_size` reads its number ONCE, in the stepper's own field.

    2026-08-03; see design/DECISIONS.md#d-left_panel_sections-606

    2026-09-30; see design/DECISIONS.md#d-left_panel_sections-607
    """
    row.value.setVisible(False)
    head = row.layout().itemAt(0).widget().layout()
    head.insertWidget(head.count() - 1, editor)
    if editor.layout() is not None:     # a stand-in editor has none
        editor.layout().setContentsMargins(0, 0, SWATCH_HEADER_VALUE_INSET, 0)   # page: 266 of 274


def gpu_section(widgets):
    """`CFG_GROUPS` -> GPU: the backend, then what the backend RESOLVED to.

    2026-08-30; see design/DECISIONS.md#d-left_panel_sections-608

    The kv table IS real: it reads the resolved device rather than the
    page's hardcoded 'RTX 4070 · 12 GB'.
    """

    # BEFORE the rows read the profile, and before the first render asks for
    # a budget: the saved memory size has to be in the engine or the panel
    # would draw the blind default and then disagree with itself one frame
    # later. Idempotent, so `Controller.refresh_gpu_status` calling it again
    # costs nothing.
    load_user_vram_setting()
    load_user_ceiling_setting()

    section = DyeworksSection('GPU 硬件加速', expanded=True, compact=True)
    holder = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(section)

    # The backend TICK, in a row of the panel's own language. The legacy
    # widget pairs it with a lower-cased status label ('gpu acceleration
    # (vulkan)') sitting at a nested indent -- that reads as a leftover
    # inside a group whose other lines are the page's.
    backend = getattr(widgets, 'gpu_backend', None)
    toggle = getattr(backend, 'gpu_backend_toggle', None)
    if toggle is not None:
        from spektrafilm_gui.dyeworks_buttons import ToggleSwitch
        from spektrafilm_gui.dyeworks_labels import LabelledRow

        # A NEW ToggleSwitch, two-way bound -- exactly what `EditRow` does
        # for every other boolean, Auto preview included. Moving the
        # editor's own QCheckBox into the head instead is what made the
        # control vanish last time: the raw box is not what the panel draws
        # anywhere, it is only what the panel READS.
        row = LabelledRow('GPU acceleration')
        row.value.setVisible(False)
        switch = ToggleSwitch()
        switch.setChecked(bool(toggle.isChecked()))
        switch.toggled.connect(toggle.setChecked)
        toggle.toggled.connect(switch.setChecked)
        head = row.layout().itemAt(0).widget().layout()
        head.insertWidget(head.count() - 1, switch)
        section.add(row)
        row.switch = switch
        # The legacy widget stays parented (it owns the signal that reaches
        # the controller) but never draws again.
        backend.setParent(holder)
        backend.hide()
        section.add(_backend_row(toggle))
    elif backend is not None:
        old = backend.findChild(CollapsibleSection)
        content = getattr(old, '_content', None) if old is not None else None
        section.add(content if content is not None else backend)
    facts = _kv_rows(_gpu_facts())
    section.add(facts)
    _live_facts.append(weakref.ref(facts))     # see show_last_render

    # 2026-08-30; see design/DECISIONS.md#d-left_panel_sections-592
    vram = _vram_row()
    section.add(vram)
    ceiling = _ceiling_row()
    section.add(ceiling)
    # 2026-08-30; see design/DECISIONS.md#d-left_panel_sections-593
    _sync_ceiling_enabled(ceiling)

    note = Hint(_vram_note())
    note.setWordWrap(True)
    section.add(note)

    def _restate() -> None:
        """Say the new truth after the user answers.

        Without this the section contradicts itself the moment it is used:
        the caption still reads 'a conservative 6 GB is assumed' and the
        Memory line still reads 'not reported' underneath a control that
        has just reported it.
        """
        try:
            note.setText(_vram_note())
            facts_now = dict((p[0], p[1]) for p in _gpu_facts())
            for key, label in (getattr(facts, 'values', {}) or {}).items():
                if key in facts_now:
                    label.setText(facts_now[key])
            # The memory row appears the moment the ceiling can no longer be
            # applied to a detected size, and disappears again when it can.
            # SAFE here, unlike in `_vram_row`: by now the row has been
            # added to the section, so it has a parent and showing it cannot
            # make a top-level window. Guarded anyway, because the day
            # somebody calls `_restate` earlier is the day six `python`
            # windows come back.
            if vram.parentWidget() is not None:
                vram.setVisible(_vram_source() != 'detected')
            # ...and the ceiling comes back to life the moment that size is
            # answered, which is the whole reason the dim helper reverses.
            _sync_ceiling_enabled(ceiling)
        except Exception:
            pass

    vram.on_change = _restate
    ceiling.on_change = _restate
    holder.vram_note = note
    holder.vram_row = vram
    holder.ceiling_row = ceiling
    holder.section = section
    return holder


#: QSettings key for the memory size the user typed. Same store as every
#: other app preference (`canvas_background`, `dialog_dirs/*`), which the
#: suite already snapshots and restores.
def gpu_settings():
    """The store the GPU preferences live in. ONE seam, so tests can move it.

    2026-08-30; see design/DECISIONS.md#d-left_panel_sections-609

    Redirection was tried before and does not work for the app at large --
    `setPath`/`setDefaultFormat` still resolved to HKEY_CURRENT_USER (see
    that fixture's docstring). This is narrower and does: one accessor the
    GPU tests point at a temporary IniFormat file, leaving the rest of the
    app on the native store exactly as before.
    """

    return app_settings()


VRAM_SETTING_KEY = 'gpu/vram_mb'
#: QSettings key for the memory ceiling, stored as a PERCENT integer. A
#: percent rather than a float because that is what the row shows and what a
#: user would recognise if they ever read the registry.
CEILING_SETTING_KEY = 'gpu/memory_ceiling_pct'
# 2026-09-28; see design/DECISIONS.md#d-left_panel_sections-594
BACKEND_SETTING_KEY = 'gpu/backend'
GPU_BACKENDS = (('auto', 'Auto (GPU, CPU fallback)'), ('gpu_tiled', 'GPU, tiled'),
                ('gpu', 'GPU, whole frame'), ('cpu', 'CPU only'))
_LADDER_FOR = {'auto': 'auto', 'gpu_tiled': 'tiled', 'gpu': 'whole', 'cpu': 'auto'}
#: Slider range in GB. 1 covers the smallest thing that can run this at all;
#: 48 covers a workstation card, and the step is a whole GB because nobody
#: knows their VRAM to finer than that.
VRAM_MIN_GB, VRAM_MAX_GB = 1, 48


def saved_backend() -> str:
    try:
        value = str(gpu_settings().value(BACKEND_SETTING_KEY, 'auto') or 'auto')
    except Exception:
        return 'auto'
    return value if value in dict(GPU_BACKENDS) else 'auto'


def apply_backend(key: str, *, save: bool = True) -> None:
    """The ladder rungs for the next render (`gpu.tiling.ladder_mode`).
    CPU only is carried by the acceleration switch, which the row turns off
    -- see `gpu_section`."""

    os.environ['SPEKTRAFILM_GPU_LADDER'] = _LADDER_FOR.get(key, 'auto')
    if save:
        try:
            settings = gpu_settings()
            settings.setValue(BACKEND_SETTING_KEY, key)
            settings.sync()
        except Exception:
            pass


def load_user_vram_setting() -> None:
    """Push the saved memory size into the engine. Idempotent, never raises.

    The engine side (`spektrafilm.gpu.vendor`) must not read QSettings
    itself: `gpu.device` is explicit that the GPU layer stays importable by
    the headless pipeline and the tests with no Qt involved. So the GUI
    pushes, and a headless run simply has no user answer -- which is right,
    because there is nobody there to have given one.
    """
    try:

        from spektrafilm.gpu import vendor

        raw = gpu_settings().value(VRAM_SETTING_KEY, None)
        # NOTHING STORED MEANS NOTHING TO SAY, not 'clear whatever you have'.
        # This read runs at every `gpu_section()` build and every
        # `refresh_gpu_status()`, so an unconditional push made a LOAD into a
        # CLEAR: a value set by any route other than the slider was discarded
        # the moment the Config panel was built. Caught by screenshotting the
        # three states and finding 'user' rendering identically to 'unknown'
        # -- the tests missed it because they either called the note directly
        # or went through the slider, which saves before it loads.
        if raw in (None, ''):
            return
        vendor.set_user_vram_mb(int(raw or 0))
    except Exception:                                # never break the panel
        pass


def save_user_vram_setting(megabytes: int) -> None:
    """Persist the user's answer and apply it immediately."""
    try:

        from spektrafilm.gpu import vendor

        gpu_settings().setValue(VRAM_SETTING_KEY, int(megabytes))
        vendor.set_user_vram_mb(int(megabytes))
        _budget_changed()
    except Exception:
        pass


def load_user_ceiling_setting() -> None:
    """Push the saved memory ceiling into the engine. Never raises."""
    try:

        from spektrafilm.gpu import vendor

        raw = gpu_settings().value(CEILING_SETTING_KEY, None)
        if raw in (None, ''):
            return                                   # a LOAD is not a CLEAR
        vendor.set_user_fraction(float(raw) / 100.0)
    except Exception:
        pass


def save_user_ceiling_setting(percent: float) -> None:
    """Persist the memory ceiling and apply it immediately."""
    try:

        from spektrafilm.gpu import vendor

        gpu_settings().setValue(CEILING_SETTING_KEY, int(round(percent)))
        vendor.set_user_fraction(float(percent) / 100.0)
        _budget_changed()
    except Exception:
        pass


def _budget_changed() -> None:
    """The render budget moved, so stale conclusions about it must go.

    `tiling` remembers which chains could not fit whole ('demotions') so it
    does not retry a doomed attempt every frame. That memory is a
    measurement AGAINST A BUDGET -- change the budget and it is stale.
    Without this, raising the ceiling or telling the app how much memory the
    card has would leave it on the slower tiled rung for exactly the work
    the user just made room for, and the setting would look inert.
    """
    try:
        from spektrafilm.gpu import tiling

        tiling.forget_demotions()
    except Exception:
        pass


def _sync_ceiling_enabled(ceiling: QtWidgets.QWidget) -> None:
    """Live when a memory size is known, greyed when it is not.

    One function for both directions so the two states cannot be wired
    differently -- entering a size has to bring the row back, not just
    stop hiding a caption.
    """
    # 2026-08-31; see design/DECISIONS.md#d-left_panel_sections-595
    set_row_dimmed(ceiling, False)
    ceiling.setToolTip(
        'How much of the GPU\'s memory the renderer may plan against. The rest '
        'is left for the canvas, the compositor and the desktop. Lower it if '
        'the machine stutters while rendering; raise it to keep more work on '
        'the whole-frame path instead of tiling.')


def _vram_source() -> str:
    """'detected' / 'user' / 'unknown', or 'unknown' if anything goes wrong."""
    try:
        from spektrafilm.gpu import vendor

        return vendor.profile().vram_source
    except Exception:
        return 'unknown'


def _vram_note() -> str:
    """The caption under the row — different sentence per state.

    It is the NON-BLOCKING NOTICE. A modal at startup was considered and
    rejected: a wrong budget costs speed, not the image (`run_or_tile`
    demotes a failed whole-frame attempt to tiled, and tiling halves before
    conceding), so blocking a launch over a tuning value most people cannot
    recite is out of proportion — and it would fire on exactly the
    unfamiliar machines we are trying to win over.
    """
    try:
        from spektrafilm.gpu import vendor

        profile = vendor.profile()
    except Exception:
        return 'GPU memory could not be read.'
    planned = f'{profile.budget_mb / 1024:.1f} GB'
    if profile.vram_source == 'detected':
        # 2026-08-30; see design/DECISIONS.md#d-left_panel_sections-596
        return (f'{profile.vram_mb / 1024:.0f} GB detected, read from the driver. '
                f'Planning against {planned} at the ceiling above.')
    if profile.vram_source == 'user':
        return ('This machine did not report its GPU memory, so the size above is '
                f'yours: planning against {planned}.')
    # THE CEILING DOES NOT APPLY IN THIS STATE -- the blind default is an
    # absolute budget, not a fraction of anything, because multiplying a
    # guess by another guess would make the number less honest rather than
    # more careful (see `vendor.profile`). The ceiling row is GREYED to say
    # that, so this sentence no longer has to; it went back to being about
    # the one thing the user can actually do here.
    return ('This machine did not report its GPU memory, so a conservative '
            f'{planned} is assumed. The GPU still runs, and oversized work falls '
            'back to tiling. Set your card\'s memory above to use all of it.')


class _VramEditor(QtCore.QObject):
    """A scalar editor over the GPU-memory preference, in GB.

    `dyeworks_widgets.slider_row` is the house component for 'a labelled row
    with a slider under it', and it drives an EDITOR: something with
    `.value`, a `valueChanged` signal, and optionally `baseline()` /
    `reset_to_baseline()`. Every other slider in the app goes through it,
    which is what makes them all carry the same typeable number, the same
    per-row reset arrow and the same modified dot.

    This row had none of those, because it was hand-built out of a
    `LabelledRow` and a `MiniSlider` instead -- close enough to look right
    in isolation and visibly not one of the family beside the others. The
    honest fix is not to copy the styling across but to use the component,
    so there is no second implementation to drift.

    There is no dataclass field behind this one -- it is a preference, not a
    render parameter -- so the editor is a small local object rather than
    anything from `state`. It writes THROUGH to `gpu.vendor` on every
    change (cheap, in-memory) and leaves persistence to the caller's
    release handler.
    """

    valueChanged = QtCore.Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._gb = self._current_gb()

    @staticmethod
    def _profile():
        return vendor.profile()

    def _current_gb(self) -> int:
        """What the row reads: the size in force, whoever supplied it.

        NOT `baseline()`, which deliberately ignores the user's own answer
        -- seeding from that would draw the blind number over a value the
        user had set. And NOT the slider minimum either: a machine that
        reports nothing has `vram_mb is None`, and `(None or 0) / 1024` fell
        through to VRAM_MIN_GB and drew '1 GB' under a caption saying a
        conservative 4 GB was assumed. The row shows what is actually being
        planned against.
        """
        try:
            profile = self._profile()
            gigabytes = ((profile.vram_mb or 0) / 1024) or (profile.budget_mb / 1024)
            return int(min(max(round(gigabytes), VRAM_MIN_GB), VRAM_MAX_GB))
        except Exception:
            return 8

    @property
    def value(self) -> float:
        return float(self._gb)

    @value.setter
    def value(self, incoming: float) -> None:
        try:
            gigabytes = int(round(float(incoming)))
        except (TypeError, ValueError):
            return
        gigabytes = min(max(gigabytes, VRAM_MIN_GB), VRAM_MAX_GB)
        if gigabytes == self._gb:
            return
        self._gb = gigabytes
        try:
            from spektrafilm.gpu import vendor

            vendor.set_user_vram_mb(gigabytes * 1024)
        except Exception:
            pass
        self.valueChanged.emit(float(gigabytes))

    def baseline(self):
        """What this row would read with NO answer of the user's own.

        The detected size when there is one, the blind assumption when
        there is not -- either way, what is actually in force. So the
        modified dot means 'you changed this from what the app would do',
        which is what it means on every other row.
        """
        try:
            from spektrafilm.gpu import vendor

            probed = vendor.probed_vram_mb()
            if probed:
                return float(round(probed / 1024))
            return float(round(vendor.profile().budget_mb / 1024)) or float(VRAM_MIN_GB)
        except Exception:
            return None

    def reset_to_baseline(self) -> None:
        """Forget the hand-set value and go back to what the app decides.

        The row's reset arrow, and the only way out of a number typed by
        mistake -- there is no other affordance that clears it.
        """
        try:

            from spektrafilm.gpu import vendor

            gpu_settings().remove(VRAM_SETTING_KEY)
            vendor.set_user_vram_mb(None)
        except Exception:
            pass
        self._gb = self._current_gb()
        self.valueChanged.emit(float(self._gb))


class _CeilingEditor(QtCore.QObject):
    """A scalar editor over the memory ceiling, as a PERCENT.

    Same house editor contract as `_VramEditor` -- `.value`, `valueChanged`,
    `baseline()`, `reset_to_baseline()` -- so `slider_row` can drive it and
    the row is a sibling of every other slider by construction.

    Percent rather than a fraction because that is the unit the row shows.
    The engine keeps a fraction; the conversion happens here, once.
    """

    valueChanged = QtCore.Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._pct = self._current_pct()

    @staticmethod
    def _integrated() -> bool:
        try:
            return bool(vendor.profile().is_integrated)
        except Exception:
            return False

    def _current_pct(self) -> int:
        try:
            return int(round(vendor.effective_fraction(self._integrated()) * 100))
        except Exception:
            return 75

    @property
    def value(self) -> float:
        return float(self._pct)

    @value.setter
    def value(self, incoming: float) -> None:
        try:
            percent = int(round(float(incoming)))
        except (TypeError, ValueError):
            return
        percent = min(max(percent, int(vendor.FRACTION_MIN * 100)),
                      int(vendor.FRACTION_MAX * 100))
        if percent == self._pct:
            return
        self._pct = percent
        try:
            vendor.set_user_fraction(percent / 100.0)
        except Exception:
            pass
        self.valueChanged.emit(float(percent))

    def baseline(self):
        """The ceiling this adapter kind gets when nobody has chosen one, so
        the modified dot means 'you moved this off the default'."""

        try:
            return float(round(vendor.default_fraction(self._integrated()) * 100))
        except Exception:
            return None

    def reset_to_baseline(self) -> None:
        try:

            from spektrafilm.gpu import vendor

            gpu_settings().remove(CEILING_SETTING_KEY)
            vendor.set_user_fraction(None)
            _budget_changed()
        except Exception:
            pass
        self._pct = self._current_pct()
        self.valueChanged.emit(float(self._pct))


def _commit_debounced(holder, editor, slider, commit) -> None:
    """Persist a row's value 400 ms after its editor last moved, and at once
    when a slider drag ends. Sets `holder.commit` and `holder.commit_timer`.

    DEBOUNCED ON THE EDITOR, not on `sliderReleased`. Release is only
    emitted for a MOUSE DRAG: arrow keys, a click on the groove and the
    typed number (the row's value is an input now, like every other row's)
    all move the editor without ever releasing a handle. Hanging
    persistence off the release meant those changed the live budget and
    were then lost at the next launch -- which is the sort of thing that
    looks like the setting 'not sticking' and is very hard to report.

    The timer is what keeps the registry out of the drag: `valueChanged`
    fires ~1000 times across one, and only the last one writes.
    """
    commit_timer = QtCore.QTimer(holder)
    commit_timer.setSingleShot(True)
    commit_timer.setInterval(400)
    commit_timer.timeout.connect(commit)
    editor.valueChanged.connect(lambda *_: commit_timer.start())
    if slider is not None:
        # A finished drag commits at once rather than waiting out the timer.
        slider.sliderReleased.connect(commit_timer.stop)
        slider.sliderReleased.connect(commit)
    holder.commit = commit
    holder.commit_timer = commit_timer


def _ceiling_row() -> QtWidgets.QWidget:
    """`cfg.conservative_limits`, live: how much of the card to plan against.

    2026-08-30; see design/DECISIONS.md#d-left_panel_sections-610

    It replaces the page's `cfg.conservative_limits` placeholder rather than
    sitting beside it: a percentage IS the conservative-limits control, and
    a more useful one than the boolean the mock-up drew. Low is cautious,
    high is greedy, and the row says what it costs in GB either way.

    It stays visible when the GPU-memory row hides, and that is the point --
    on a machine that reports its own size you cannot tell the app how much
    memory there is, but you can still tell it how much of it to use.
    """

    editor = _CeilingEditor()
    holder = slider_row('Memory ceiling', editor,
                        minimum=vendor.FRACTION_MIN * 100,
                        maximum=vendor.FRACTION_MAX * 100,
                        step=5, decimals=0, unit='%')
    slider = holder.findChild(MiniSlider)

    def _commit() -> None:
        save_user_ceiling_setting(editor.value)
        restate = getattr(holder, 'on_change', None)
        if callable(restate):
            restate()

    _commit_debounced(holder, editor, slider, _commit)
    holder.editor = editor
    holder.slider = slider
    holder.setToolTip(
        'How much of the GPU\'s memory the renderer may plan against. The rest '
        'is left for the canvas, the compositor and the desktop. Lower it if '
        'the machine stutters while rendering; raise it to keep more work on '
        'the whole-frame path instead of tiling.')
    return holder


def _vram_row() -> QtWidgets.QWidget:
    """`cfg.vram_ceiling`, live: the card's memory in GB.

    Built with `dyeworks_widgets.slider_row`, the same helper every other
    slider in the app uses, so it carries the house row by construction:
    typeable number, per-row reset, modified dot, the design's spacing.

    2026-08-30; see design/DECISIONS.md#d-left_panel_sections-611

    Persistence is on RELEASE, not on every drag step: `moved` fires ~1000
    times across a drag and each write touches the registry. The editor
    still updates the live budget per step, which is free.
    """

    editor = _VramEditor()
    holder = slider_row('GPU memory', editor,
                        minimum=VRAM_MIN_GB, maximum=VRAM_MAX_GB,
                        step=1, decimals=0, unit=' GB')
    holder.editor = editor
    slider = holder.findChild(MiniSlider)
    holder.slider = slider

    def _commit() -> None:
        save_user_vram_setting(int(round(editor.value)) * 1024)
        restate = getattr(holder, 'on_change', None)
        if callable(restate):
            restate()

    _commit_debounced(holder, editor, slider, _commit)
    holder.editor = editor                        # keep it referenced

    detected = False
    try:
        detected = _VramEditor._profile().vram_source == 'detected'
    except Exception:
        pass
    # 2026-08-30; see design/DECISIONS.md#d-left_panel_sections-597
    if detected:
        holder.hide()
    holder.setToolTip(
        'How much memory this GPU has. This machine does not report it, so the '
        'renderer plans against a conservative guess until you say otherwise.')
    return holder


def set_row_dimmed(widget: QtWidgets.QWidget, dimmed: bool = True) -> None:
    """Make a disabled row actually LOOK disabled -- and undo it.

    2026-08-03; see design/DECISIONS.md#d-left_panel_sections-612

    REVERSIBLE, because the memory ceiling goes both ways: it is inert until
    a memory size is known, and live the moment one is. A dim-only helper
    would have meant a second, subtly different un-dim somewhere else, which
    is how the two copies of this workaround would have started.

    Clearing the sheet rather than writing an 'enabled' one on purpose: the
    global sheet is the source of truth for how a live row looks, and a
    local override that tried to restate it would be a third place to keep
    in step with the design.
    """
    widget.setEnabled(not dimmed)
    widget.setStyleSheet(
        (f'QLabel {{ color: {_palette.DISABLED_TEXT}; }}'
         f' QSlider::sub-page:horizontal {{ background: {_palette.GRAY_3}; }}'
         f' QSlider::handle:horizontal {{ background: {_palette.DISABLED_TEXT}; }}')
        if dimmed else '')


def _kv_rows(pairs) -> QtWidgets.QWidget:
    """`{t:'kv', pairs:[...]}` — a fact and its value, one line each.

    Not editors: these REPORT. Built here rather than in the edit panel
    because Config is the only place the page uses them for the app's own
    state.
    """

    holder = QtWidgets.QWidget()
    column = QtWidgets.QVBoxLayout(holder)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)
    for pair in pairs:
        # A third element is a tooltip: the row stays one short line, and the
        # detail behind it (which chain is live, and why) is a hover away.
        key, value, tip = (tuple(pair) + ('',))[:3]
        line = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(line)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        name = QtWidgets.QLabel(str(key))
        name.setProperty('role', 'caption')
        name.setFont(_ui_font(name, TYPE_CAPTION))
        # ShrinkingLabel, not QLabel: a plain label reports its full text
        # width as its MINIMUM, so '9 of 9 chains - sized by expose' (341px)
        # set a floor the 300px panel could not go under and every row in
        # Config clipped. It elides PER LINE, which is what lets the rule
        # below survive.
        shown = ShrinkingLabel(str(value))
        shown.setProperty('role', 'caption')
        shown.setFont(_mono_font(TYPE_CAPTION))
        shown.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # NO WRAPPING AT ALL. The device name carries its own newline, put
        # where the MODEL starts; letting Qt break on width instead moved
        # the split from machine to machine. The other two are short phrases
        # that were breaking mid-arrow for want of room.
        shown.setWordWrap(False)
        if tip:
            line.setToolTip(str(tip))
            name.setToolTip(str(tip))
            shown.setToolTip(str(tip))
        # The value takes the stretch, not a spacer between the two: given
        # only its size hint it wrapped with room to spare.
        row.addWidget(name, 0)
        row.addWidget(shown, 1)
        column.addWidget(line)
        # Keyed by their label so a caller can update ONE fact in place.
        # Config -> GPU needs it: setting the GPU memory by hand changes the
        # Memory line, and a table built once would go on reporting 'not
        # reported' underneath a control that had just answered it.
        holder.values = getattr(holder, 'values', {})
        holder.values[str(key)] = shown
    return holder


def _split_device_name(name: str, backend: str) -> str:
    """Break the adapter name where its MODEL starts (NAT 2026-08-03).

        NVIDIA GeForce RTX
        4070 SUPER · Vulkan

    Left to wrap on width it broke wherever the column ran out, which put
    the split mid-series on one machine and mid-model on the next. The first
    numeric token is the model on every vendor's naming I can check --
    'RTX 4070', 'Radeon RX 7900', 'Arc A770' -- so the line reads as make
    then model rather than as an accident of panel width.
    """
    tail = f', {backend}' if backend else ''
    words = name.split()
    for index, word in enumerate(words):
        if index and word[:1].isdigit():
            return f'{" ".join(words[:index])}\n{" ".join(words[index:])}{tail}'
    return f'{name}{tail}'


def _backend_row(toggle):
    """`cfg.gpu_backend`: Auto / GPU · tiled / GPU · whole-frame / CPU only.
    CPU only turns the acceleration switch off; a GPU choice turns it back
    on. Saved with the other GPU preferences; the saved choice is applied
    when the section is built (NAT 2026-09-28)."""

    row = LabelledRow('Backend')
    row.value.setVisible(False)
    picker = ChipComboBox()
    picker.setObjectName('dyeworksGpuBackend')
    for key, label in GPU_BACKENDS:
        picker.addItem(label, key)
    picker.setToolTip('Which rungs of the GPU ladder may run: the whole frame, '
                      'tiles, both (Auto), or none (CPU only).')
    current = saved_backend()
    if current == 'cpu' and toggle is not None and toggle.isChecked():
        toggle.setChecked(False)
    apply_backend(current, save=False)
    picker.setCurrentIndex([k for k, _l in GPU_BACKENDS].index(current))

    def chosen(_index: int) -> None:
        key = picker.currentData()
        apply_backend(key)
        if toggle is not None:
            want_gpu = key != 'cpu'
            if bool(toggle.isChecked()) != want_gpu:
                toggle.setChecked(want_gpu)

    picker.currentIndexChanged.connect(chosen)
    head = row.layout().itemAt(0).widget().layout()
    head.insertWidget(head.count() - 1, picker)
    row.picker = picker
    return row


def _gpu_facts() -> tuple[tuple[str, str], ...]:
    """Device / tiling / fallback, ASKED of the app.

    The page states 'RTX 4070 · 12 GB' because a mock-up has to say
    something. Reporting that here would be a lie on every other machine,
    so this reads the adapter that was actually resolved -- and says so
    plainly when there is none, which is the case the fallback ladder
    exists for.
    """
    device = 'CPU (no GPU adapter)'
    try:
        from spektrafilm.gpu.device import GPUDevice

        manager = GPUDevice.get()
        adapter = getattr(manager, 'adapter', None)
        # 2026-10-06; see design/DECISIONS.md#d-left_panel_sections-598
        if getattr(manager, 'lost_reason', None) and getattr(manager, 'device', None) is None:
            device = 'CPU (GPU device lost)'
        elif adapter is not None:
            info = getattr(adapter, 'info', None) or {}
            name = info.get('device') or info.get('name') or 'GPU adapter'
            backend = info.get('backend_type') or info.get('adapter_type')
            device = _split_device_name(
                str(name), str(backend) if backend else '')
        elif getattr(manager, 'init_error', None):
            device = f'CPU ({manager.init_error})'
    except Exception as error:                      # never break the panel
        device = f'unavailable ({type(error).__name__})'
    # 2026-08-30; see design/DECISIONS.md#d-left_panel_sections-599
    memory = 'unavailable'
    memory_tip = ''
    try:
        from spektrafilm.gpu import vendor as _vendor

        profile = _vendor.profile()
        if profile.vram_mb:
            memory = (f'{profile.vram_mb / 1024:.0f} GB, '
                      f'{profile.budget_mb / 1024:.1f} GB planned')
            if profile.vram_source == 'user':
                # Said plainly, because a number the user typed must never
                # read as a detection -- otherwise the notice telling them
                # to set it disappears the moment they do, and nobody can
                # tell afterwards whether the machine answered or they did.
                memory += ', set by hand'
        else:
            memory = f'not reported, {profile.budget_mb / 1024:.1f} GB assumed'
        # NO VENDOR SUFFIX. It was here before the row could say anything
        # else; now it is the least important thing on the line and the
        # first casualty of the panel's 300px elision -- which is how 'set
        # by hand' became 'set by han...'. The Device row directly above
        # names the card in full, so this said it twice.
        memory_tip = _vendor.profile().describe()
    except Exception as error:                      # never break the panel
        memory = f'unavailable ({type(error).__name__})'
    # Which chains are actually resident, ASKED of the registry rather than
    # asserted. Before this the panel said 'GPU-tiled → GPU → CPU' as a
    # hardcoded string while six of the nine resident chains were declined,
    # and no surface in the app could say so (integration audit, P2/P3).
    resident = 'unavailable'
    detail = ''
    # ORDER CORRECTED 2026-08-30 (NAT: "shouldn't fallback be first GPU then
    # GPU tiled then CPU?"). It read 'GPU-tiled → GPU → CPU', which is
    # backwards and was describing the ladder as nobody runs it.
    # `tiling.run_or_tile` tries the WHOLE FRAME first whenever
    # `plan_residency` says it fits, demotes to TILED when that attempt
    # fails (and remembers, so the doomed attempt is not repeated), and only
    # returns None -- the CPU rung -- when even tiling will not go. Whole is
    # also the rung worth trying first on speed: 0.63 s vs 1.30 s tiled at
    # 24 MP, measured 2026-07-30.
    #
    # The first rung is SKIPPED, not failed, when the work is obviously too
    # big for the budget; that is the guard doing its job, and the order of
    # preference below is still what the code walks.
    ladder = 'GPU → GPU-tiled → CPU' if not device.startswith('CPU') else 'CPU only'
    # 2026-09-28; see design/DECISIONS.md#d-left_panel_sections-600
    import os
    mode = os.environ.get('SPEKTRAFILM_GPU_LADDER', 'auto')
    if not device.startswith('CPU'):
        ladder = {'tiled': 'GPU-tiled → CPU', 'whole': 'GPU → CPU'}.get(mode, ladder)
    try:
        from spektrafilm.gpu import seams

        live = seams.live_seams()
        resident = f'{len(live)} of {len(seams.SEAMS)} chains'
        binding = seams.binding_seam()
        if binding is not None:
            resident += f', sized by {binding.name}'
        detail = seams.describe()
    except Exception as error:                      # never break the panel
        resident = f'unavailable ({type(error).__name__})'
    return (('Device', device),
            ('Memory', memory, memory_tip),
            ('Tiling', 'dynamic, device limits'),
            ('Resident', resident, detail),
            ('Fallback', ladder),
            ('Last render', _last_render[0], _last_render[1]))


# 2026-06-16, 2026-09-26; see design/DECISIONS.md#d-left_panel_sections-601
_last_render = ['none yet', '']
_live_facts: list = []          # weak references to the live facts tables


def show_last_render(text: str, tip: str = '') -> None:
    """Say what the last preview render cost, in every live GPU table."""
    _last_render[:] = [text, tip]
    for ref in list(_live_facts):
        facts = ref()
        label = None if facts is None else getattr(facts, 'values', {}).get('Last render')
        if label is None:
            _live_facts.remove(ref)
            continue
        try:
            label.setText(text)
            label.setToolTip(tip)
        except RuntimeError:        # the C++ side went with its panel
            _live_facts.remove(ref)


def render_backend_text() -> str:
    """The File section's Backend row: what renders this frame, in the
    page's words ('Vulkan, through wgpu'), or the CPU when there is no GPU
    or it was lost (QA 2026-10-07)."""
    try:
        from spektrafilm.gpu.device import GPUDevice

        manager = GPUDevice.get()
        if getattr(manager, 'lost_reason', None) and getattr(manager, 'device', None) is None:
            return 'CPU (GPU device lost)'
        adapter = getattr(manager, 'adapter', None)
        if adapter is not None:
            info = getattr(adapter, 'info', None) or {}
            backend = info.get('backend_type') or info.get('adapter_type')
            return f'{backend}, through wgpu' if backend else 'GPU, through wgpu'
    except Exception:                       # never break the panel
        pass
    return 'CPU'


def refresh_gpu_facts() -> None:
    """Re-read the GPU facts into every live table: a device loss changes
    them mid-session (2026-10-06). 'Last render' keeps its own channel."""
    now = {row[0]: row[1] for row in _gpu_facts()}
    for ref in list(_live_facts):
        facts = ref()
        values = {} if facts is None else (getattr(facts, 'values', {}) or {})
        if not values:
            _live_facts.remove(ref)
            continue
        try:
            for key, label in values.items():
                if key in now and key != 'Last render':
                    label.setText(now[key])
        except RuntimeError:        # the C++ side went with its panel
            _live_facts.remove(ref)


def format_last_render(ms: float, megapixels: float, backend: str, mode: str):
    """('323 ms · 6.0 MP · GPU', 'full render') -- short enough for the
    panel's 300px value column; the tier goes in the tooltip."""
    tiers = {'full': 'full render', 'scan': 'SCAN tier (scan stage only)',
             'develop': 'DEVELOP tier (from the cached exposure)'}
    return (f'{ms:.0f} ms, {megapixels:.1f} MP, {backend}',
            tiers.get(mode, str(mode)))


def rehomed_section(legacy: QtWidgets.QWidget, title: str, *,
                    expanded: bool = True, meta: str = ''):
    """A legacy `CollapsibleSection` re-dressed as the page's group.

    Takes the CONTENT out of the old header and puts it in an
    `DyeworksSection` with the page's own title. The controls inside are the
    same objects -- same signals, same state, same wiring -- so this is a
    change of frame, not of behaviour, which is the pattern the whole edit
    panel is built on.

    Returns the legacy widget untouched if it has no collapsible inside, so
    a section that gets rebuilt properly later does not break this.
    """

    old = legacy.findChild(CollapsibleSection)
    content = getattr(old, '_content', None) if old is not None else None
    if content is None:
        return legacy

    section = DyeworksSection(title, expanded=expanded, meta=meta, compact=True)
    holder = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(section)
    section.add(content)
    # The old shell keeps the legacy widget alive without drawing: it still
    # owns whatever else was attached to it.
    legacy.setParent(holder)
    legacy.hide()
    holder.section = section
    return holder


#: `rowUIBg`'s swatches: 22px, radius 5, laid out `display:flex; gap:8px`.
CANVAS_SWATCH_PX = 22
CANVAS_SWATCH_RADIUS = 5


class UiSectionView(QtWidgets.QWidget):
    """`CFG_GROUPS` -> UI: the accent colour, and the canvas surround.

    Two rows the design has in Config and this app did not have anywhere:

      - **Accent** (`{t:'uiaccent'}`) retints the chrome. tokens.md settles
        the palette and cyan IS the decision; this lets a user retint their
        own copy without reopening it.
      - **Canvas** (`{t:'uibg'}`) puts the surround swatches back. They were
        never removed from the app -- NAT retired them from the NAV BAR in
        18D -- and the page's home for them is here.

    The swatches are PHOTOGRAPHIC, not theme: reference surfaces for judging
    an image. They are listed from the canvas's own option list so this view
    cannot invent one.
    """

    def __init__(self, on_canvas_background=None) -> None:
        super().__init__()
        self.setObjectName('uiSectionView')

        section = DyeworksSection('界面与主题外观', expanded=True, compact=True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section)

        self.accent_row = self._accent_row()
        section.add(self.accent_row)
        self.canvas_row = self._canvas_row(on_canvas_background)
        if self.canvas_row is not None:
            section.add(self.canvas_row)
            section.add(Hint('The surround is a reference surface, not a '
                             'theme: 18% grey is the meter standard.'))
        # Phase 21B. In UI rather than Display because Display is about the
        # IMAGE -- display transform, output colour space, white padding --
        # and this is about the chrome's own rasterisation, which is what the
        # accent and the surround above it are too.
        self.scale_row = self._scale_row()
        section.add(self.scale_row)
        self.scale_hint = Hint(self._scale_hint_text())
        section.add(self.scale_hint)
        self.section = section

    # -------------------------------------------------------------- scale
    def _scale_hint_text(self) -> str:
        """What this mode does, and — for Automatic — what it DETECTED.

        The detection line is the point: "does it know my screen?" is the
        first question anyone asks of an automatic setting, and answering it
        on screen is cheaper than answering it in a support thread.
        """

        mode = scale_mode()
        blurb = SCALE_MODES[mode][2]
        if mode == 'auto':
            blurb = f'{automatic_summary()}. {blurb}'
        return blurb + ' Takes effect the next time you open the app.'

    def _scale_row(self):
        """Whole-pixel rasterisation, as a three-way choice.

        2026-09-03; see design/DECISIONS.md#d-left_panel_sections-613
        """

        choices = [(key, SCALE_MODES[key][1]) for key in SCALE_MODES]
        # Two columns: four segments across a 300px panel elide to nothing.
        segmented = SegmentedEnum(choices, columns=2)
        segmented.set_value(scale_mode())
        segmented.setToolTip(
            'How the interface rasterises. Fractional display scaling '
            '(125%, 150%) puts 1px borders on half a pixel, which is what '
            'makes edges look soft; the crisp modes round to a whole number.')

        def _choose(value) -> None:
            set_scale_mode(str(value))
            hint = getattr(self, 'scale_hint', None)
            if hint is not None:
                hint.setText(self._scale_hint_text())

        segmented.changed.connect(_choose)
        self.scale_segmented = segmented
        return LabelledRow('Rendering', segmented)

    # ------------------------------------------------------------- accent
    def _accent_row(self):
        holder = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.accent_dots = {}
        for key, (colour, name) in ACCENTS.items():
            from spektrafilm_gui.dyeworks_editors import ColourDot

            # 2026-08-03; see design/DECISIONS.md#d-left_panel_sections-602
            dot = ColourDot(colour)
            dot.setObjectName(f'accentDot_{key}')
            dot.setProperty('role', 'hueDot')
            dot.setCheckable(True)
            dot.setChecked(key == current_key())
            dot.setToolTip(name)
            dot.clicked.connect(lambda *_, k=key: self._pick_accent(k))
            row.addWidget(dot)
            self.accent_dots[key] = dot
        row.addStretch(1)

        # The selected ring (`0 0 0 2px bg-1, 0 0 0 3.5px ink-2`): the dots
        # were checkable all along, but nothing drew which one was.
        self._accent_rings = OverflowRings(list(self.accent_dots.values()),
                                           parent=holder)

        labelled = LabelledRow('Accent colour', holder)
        labelled.set_value_inset(SWATCH_HEADER_VALUE_INSET)
        self._accent_label = labelled
        self._refresh_accent_text()
        return labelled

    def _pick_accent(self, key: str) -> None:
        if not set_accent(key):
            return
        for name, dot in self.accent_dots.items():
            dot.setChecked(name == key)
        self._refresh_accent_text()

    def _refresh_accent_text(self) -> None:
        self._accent_label.set_value_text(ACCENTS[current_key()][1])

    # ------------------------------------------------------------- canvas
    def _canvas_row(self, on_canvas_background):
        if on_canvas_background is None:
            return None

        holder = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.canvas_dots = []
        # 2026-09-26; see design/DECISIONS.md#d-left_panel_sections-603

        current = str(app_settings()
                      .value('canvas_background', '') or '')
        group = QtWidgets.QButtonGroup(holder)
        group.setExclusive(True)
        for index, entry in enumerate(CANVAS_BACKGROUND_OPTIONS):
            # the option list is (NAME, colour), not the other way round
            name, colour = entry
            swatch = QtWidgets.QToolButton()
            swatch.setObjectName(f'canvasSwatch{index}')
            swatch.setCheckable(True)
            swatch.setChecked(str(colour).lower() == current.lower())
            group.addButton(swatch)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.setToolTip(str(name))
            # `width:22px; height:22px; border-radius:5px; border:0` with an
            # INSET hairline -- a real border would eat 2px of a 22px
            # swatch and lighten every dark one it framed.
            swatch.setFixedSize(CANVAS_SWATCH_PX, CANVAS_SWATCH_PX)
            # 2026-08-03; see design/DECISIONS.md#d-left_panel_sections-604
            swatch.setStyleSheet(
                f'QToolButton {{ background: {colour}; '
                f'border: 1px solid {_palette.BORDER_LINE_STRONG}; '
                f'border-radius: {CANVAS_SWATCH_RADIUS}px; }}'
                f'QToolButton:checked {{ border: none; }}')
            swatch.clicked.connect(
                lambda *_, c=colour: (on_canvas_background(c), self._refresh_canvas_text(c)))
            row.addWidget(swatch)
            self.canvas_dots.append(swatch)
        row.addStretch(1)

        self._canvas_rings = OverflowRings(self.canvas_dots,
                                           radius=CANVAS_SWATCH_RADIUS,
                                           parent=holder)
        # 'Canvas background' is the page's own label for this row, and the page prints the
        # colour's hex on it (`vv.textContent = d.v`: '#070707'), which the app did not.
        labelled = LabelledRow('Canvas background', holder)
        labelled.set_value_inset(SWATCH_HEADER_VALUE_INSET)
        self._canvas_label = labelled
        self._refresh_canvas_text()
        return labelled

    def _refresh_canvas_text(self, colour: str | None = None) -> None:
        """The row's value: the canvas colour's hex, as the page shows it. Called when a
        swatch is picked and by the Ctrl+L cycle (which changes it without a click)."""
        label = getattr(self, '_canvas_label', None)
        if label is None:
            return


        shown = colour or str(app_settings()
                              .value('canvas_background', '') or '') or DEFAULT_CANVAS_BACKGROUND
        label.set_value_text(str(shown).upper())


#: the page ends the value of a swatch-header row 8px short of the row's edge
SWATCH_HEADER_VALUE_INSET = 8


class ShortcutsSectionView(QtWidgets.QWidget):
    """`CFG_GROUPS` -> Shortcuts: collapsed, one button.

    The page's whole group is
    `{name:'Shortcuts', collapsed:true, rows:[{t:'btns', items:['View cheat
    sheet']}]}`. The cheat sheet already exists and is already bound to a
    key; what was missing was a way to FIND it without knowing the key,
    which is exactly what a Config row is for.
    """

    def __init__(self, on_cheat_sheet=None) -> None:
        super().__init__()
        self.setObjectName('shortcutsSectionView')

        section = DyeworksSection('快捷键速查', expanded=False, compact=True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section)

        self.button = WideButton('查看所有快捷键')
        self.button.setToolTip('按分组显示所有键盘快捷键说明')
        if on_cheat_sheet is not None:
            self.button.clicked.connect(lambda *_: on_cheat_sheet())
        else:
            # Never a button that does nothing: with no handler it opens the
            # sheet itself, parented to this panel.
            from spektrafilm_gui.shortcut_help import toggle_shortcut_help

            self.button.clicked.connect(
                lambda *_: toggle_shortcut_help(self.window()))
        section.add(self.button)
        self.section = section
