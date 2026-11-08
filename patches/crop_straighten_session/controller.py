# -------------------------------------------------------------------------
# FORK CONTRACT -- upstream has this file too, and both sides edit it.
# OURS: effectively the whole file. 5,664 lines of ours against upstream's,
#   47% of the fork's entire merge risk. Every Dyeworks action lives here --
#   the loupe, test strips, rolls, versions, batch, crop, the GPU seams and
#   the worker lifetimes. Upstream's controller drives a different widget
#   layer that this app does not build.
# UPSTREAM: nothing load-bearing survives here. Their file is not a base we
#   edited; it is a different application under the same module name.
# ON CONFLICT: KEEP OURS, wholesale. Then read their diff for PARAMETERS
#   they added and wire those -- the signal worth having from this file is
#   new engine fields, and a schema-coverage test is the honest carrier for
#   it (strategy M7), not a line-by-line merge of a file we replaced.
# Convention and rationale: docs/reports/2026.08.20_fork_contracts.md
# -------------------------------------------------------------------------

from __future__ import annotations

# 2026-08-28; see design/DECISIONS.md#d-controller-240
from spektrafilm_gui import notices

import copy
import logging
import os
import time
import weakref
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from qtpy import QtCore, QtWidgets

from spektrafilm_gui import export_guard
from spektrafilm_gui import controller_persistence as persistence_actions
from spektrafilm_gui import controller_profile_sync as profile_sync
from spektrafilm_gui import controller_runtime as runtime
from spektrafilm_gui.controller_layers import (
    INPUT_LAYER_NAME,
    INPUT_PREVIEW_LAYER_NAME,
    ViewerLayerService,
)
from spektrafilm_gui.persistence import (
    clear_saved_default_gui_state,
    load_dialog_dir,
    load_gui_state_from_path,
    save_default_gui_state,
    save_dialog_dir,
    save_gui_state_to_path,
)
from spektrafilm_gui.state import (
    PROJECT_DEFAULT_GUI_STATE,
    clone_gui_state,
    digest_after_selection,
    gui_state_from_params,
)
from spektrafilm_gui.state import apply_app_look_defaults  # 2026-09-28; see design/DECISIONS.md#d-controller-241
from spektrafilm_gui.shell_layout import dialog_parent, reset_viewer_camera, set_canvas_background, set_status
from spektrafilm_gui.params_mapper import build_params_from_state, configure_export_params
from spektrafilm_gui.options import export_format_spec
from spektrafilm_gui.recover_section import decode_recipe, recover_settings
from spektrafilm_gui.roll_session import RollSession
from spektrafilm_gui.state_bridge import apply_gui_state, collect_gui_state
from spektrafilm_gui.widgets import WidgetBundle

OUTPUT_FLOAT_DATA_KEY = 'pipeline_float_output'
OUTPUT_COLOR_SPACE_KEY = 'pipeline_output_color_space'
OUTPUT_CCTF_ENCODING_KEY = 'pipeline_output_cctf_encoding'
OUTPUT_DISPLAY_TRANSFORM_KEY = 'pipeline_use_display_transform'
PROFILE_SYNC_FIELDS = profile_sync.PROFILE_SYNC_FIELDS

# The FILM-side style sections: what the emulsion does, as opposed to the
# print/scan chain. A print-paper change must not re-seed these from the stock
# constants (see apply_profile_defaults).
FILM_STYLE_SECTIONS: tuple[str, ...] = ('couplers', 'halation', 'grain')
if TYPE_CHECKING:
    pass


QThreadPool = getattr(QtCore, 'QThreadPool')
QTimer = getattr(QtCore, 'QTimer')
QSettings = getattr(QtCore, 'QSettings')

from spektrafilm_gui.widget_primitives import CollapsibleSection
from spektrafilm_gui.controller_white_balance import LiveWhiteBalanceMixin
from spektrafilm_gui.controller_roll import RollMixin
from spektrafilm_gui.controller_look_clipboard import LookClipboardMixin
from spektrafilm_gui.controller_autosave import AutosaveMixin
from spektrafilm_gui.controller_roll_file import RollFileMixin
from spektrafilm_gui.controller_ratings import RatingsMixin
from spektrafilm_gui.controller_frames import FramesMixin
from spektrafilm_gui.controller_crop import CropMixin
from spektrafilm_gui.controller_compare import CompareMixin
from spektrafilm_gui.controller_stock_editor import StockEditorMixin
from spektrafilm_gui.controller_history import HistoryMixin
from spektrafilm_gui.controller_attempts import AttemptsMixin
from spektrafilm_gui.controller_strips import StripsMixin
from spektrafilm_gui.controller_neutral_sample import NeutralSampleMixin
from spektrafilm_gui.controller_recover import RecoverDenoiseMixin
from spektrafilm_gui.controller_loupe import LoupeMixin
from spektrafilm_gui.controller_shelf import ShelfMixin
from spektrafilm_gui.controller_export import ExportMixin
from spektrafilm_gui.controller_presets_canvas import PresetsCanvasMixin, paper_of_look
import hashlib
from spektrafilm_gui.widget_slider import set_drag_end_callback
from spektrafilm_gui.loupe import frozen_autoexposure_ev
from spektrafilm.profiles.raw import has_raw_sibling, load_raw_sibling, raw_carries_couplers
from spektrafilm_gui.test_strip import STRIP_STEPS, compose_strip, draw_band_chrome, strip_deltas
from copy import deepcopy
from spektrafilm_gui.exif_summary import camera_settings, display_make, recover_facts
from spektrafilm_gui.left_panel_config import render_backend_text
from spektrafilm_gui.persistence import gui_state_to_dict
from spektrafilm.runtime.topology import Tap
from spektrafilm.runtime.services import size_guard
from spektrafilm_gui.dyeworks_panels import SCOPES_MIN_HEIGHT, SCOPES_WIDTH, ScopesPanel
from spektrafilm_gui.dyeworks_popover import Popover


def _open_collapsible(section) -> None:
    """Expand a section's own disclosure and hide its header.

    Used where a section is re-homed under a header that already names it.
    """

    for child in section.findChildren(CollapsibleSection):
        toggle = getattr(child, '_toggle', None)
        if toggle is None:
            continue
        # setChecked emits `toggled`, which is what the section listens to
        toggle.setChecked(True)
        for part in ('_toggle', '_icon_label', '_title_button',
                     '_header_line'):
            widget = getattr(child, part, None)
            if widget is not None:
                widget.setVisible(False)
QFileDialog = QtWidgets.QFileDialog
SimulationRequest = runtime.SimulationRequest
SimulationResult = runtime.SimulationResult
ExportRequest = runtime.ExportRequest
ExportWorker = runtime.ExportWorker
STARTUP_PREVIEW_ASPECT_RATIO = (3, 2)


class _DirMemoryDialog:
    """Wraps QFileDialog to open in the last-used directory via QSettings."""

    def __init__(self, key: str) -> None:
        self._key = key

    def get_save_file_name(self, parent, title, filename, file_filter):
        last_dir = load_dialog_dir(self._key)
        initial = str(Path(last_dir) / Path(filename).name) if last_dir else filename
        path, fmt = QFileDialog.getSaveFileName(parent, title, initial, file_filter)
        if path:
            save_dialog_dir(self._key, str(Path(path).parent))
        return path, fmt

    def get_open_file_name(self, parent, title, _initial, file_filter):
        path, fmt = QFileDialog.getOpenFileName(
            parent, title, load_dialog_dir(self._key), file_filter
        )
        if path:
            save_dialog_dir(self._key, str(Path(path).parent))
        return path, fmt


class _LazyModuleProxy:
    def __init__(self, loader):
        self._loader = loader
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = self._loader()
        return self._module

    def __getattr__(self, name: str):
        return getattr(self._load(), name)


def _import_colour_module():
    return import_module('colour')


def _import_pil_image_module():
    return import_module('PIL.Image')


def _import_imagecms_module():
    return import_module('PIL.ImageCms')


def runtime_simulator(*args, **kwargs):
    return import_module('spektrafilm.runtime.api').Simulator(*args, **kwargs)


def digest_params(*args, **kwargs):
    return import_module('spektrafilm.runtime.api').digest_params(*args, **kwargs)


def load_image_oiio(*args, **kwargs):
    try:
        return import_module('spektrafilm.utils.io').load_image_oiio(*args, **kwargs)
    except Exception as exc:
        # 2026-10-06; see design/DECISIONS.md#d-controller-242
        if type(exc) is Exception:
            raise OSError(str(exc)) from exc
        raise


def save_image_oiio(filename, image_data, *args, **kwargs):
    # 2026-10-06; see design/DECISIONS.md#d-controller-243
    started_at = time.time()
    bit_depth = kwargs.get('bit_depth', args[0] if args else 16)
    export_guard.check_destination(
        filename, needed_bytes=export_guard.minimum_bytes(image_data, bit_depth))
    result = import_module("spektrafilm.utils.io").save_image_oiio(
        filename, image_data, *args, **kwargs)
    export_guard.confirm_written(filename, started_at)
    return result


class _GpuLossBridge(QtCore.QObject):
    """Carries a GPU device loss from whichever thread reported it (a wgpu
    callback, a render worker) to the GUI thread, where it can be said."""
    lost = QtCore.Signal(str)


_gpu_loss_bridges: "weakref.WeakSet[_GpuLossBridge]" = weakref.WeakSet()
_gpu_loss_hooked = False


def _announce_gpu_loss(reason: str) -> None:
    for bridge in list(_gpu_loss_bridges):
        try:
            bridge.lost.emit(reason)
        except RuntimeError:        # its controller and C++ side are gone
            pass


_SOURCE_ICC_EXIF_KEY = "Exif.Image.InterColorProfile"


def _saving_space_for(ext: str, color_space: str, cctf_encoding: bool):
    """The space an export renders in. A linear DNG (C5, 2026-09-26) is
    ALWAYS linear BT.2020 -- its colour matrices say so, whatever the
    profile chip reads; every other format keeps the user's choice."""
    if ext == 'dng':
        from spektrafilm_gui.linear_dng import DNG_COLOR_SPACE

        return DNG_COLOR_SPACE, False
    return color_space, cctf_encoding


def read_image_metadata(*args, **kwargs):
    return import_module("spektrafilm.utils.io").read_image_metadata(*args, **kwargs)


def drop_source_icc_profile(metadata):
    """Strip the SOURCE file's embedded ICC profile from copied metadata.

    NAT 2026-08-02: exports of a photo loaded from a TIFF came out colour
    shifted. write_image_metadata copies the source's EXIF wholesale onto the
    export, and an embedded ICC lives in EXIF as InterColorProfile — so an
    export whose pixels are in the SAVING colour space shipped the source
    image's profile alongside the correct one that save_image_oiio embedded.
    Measured: a ProPhoto source produced a JPEG/PNG carrying the correct sRGB
    profile in APP2/iCCP AND the source's ProPhoto in EXIF. A reader that
    prefers the EXIF copy renders sRGB pixels as ProPhoto.

    RAW sources never showed it, which is why it survived: a RAF has no
    output profile to copy.

    TIFF stores its OWN ICC in this very tag, so erasing it from the copied
    SOURCE data (not from the destination) is the safe cut — exiv2 re-asserts
    the destination's profile on write. Verified for tif/jpg/png.

    The real fix belongs in utils/io.py's write_image_metadata, but that file
    is protected (the __file__ resource shim), so it is done here on the way
    in — the same containment rule the print-paper fix follows for app.py.
    """
    if metadata is None:
        return None
    try:
        import exiv2

        exif = metadata.exif
        position = exif.findKey(exiv2.ExifKey(_SOURCE_ICC_EXIF_KEY))
        if position != exif.end():
            exif.erase(position)
    except Exception:
        # Metadata is best-effort; a stale profile must never block the write.
        pass
    return metadata


def write_image_metadata(*args, **kwargs):
    return import_module("spektrafilm.utils.io").write_image_metadata(*args, **kwargs)


def load_and_process_raw_file(*args, **kwargs):
    return import_module('spektrafilm.utils.raw_file_processor').load_and_process_raw_file(*args, **kwargs)


def load_raw_daylight_base(*args, **kwargs):
    return import_module('spektrafilm.utils.raw_file_processor').load_raw_daylight_base(*args, **kwargs)


def apply_raw_white_balance(*args, **kwargs):
    return import_module('spektrafilm.utils.raw_file_processor').apply_raw_white_balance(*args, **kwargs)


def apply_rgb_white_balance(*args, **kwargs):
    return import_module('spektrafilm.utils.rgb_white_balance').apply_rgb_white_balance(*args, **kwargs)




def resize_for_preview(*args, **kwargs):
    return import_module('spektrafilm.utils.preview').resize_for_preview(*args, **kwargs)


colour = _LazyModuleProxy(_import_colour_module)
PILImage = _LazyModuleProxy(_import_pil_image_module)
ImageCms = _LazyModuleProxy(_import_imagecms_module)


class GuiController(
        LiveWhiteBalanceMixin,
        RecoverDenoiseMixin,
        RollMixin,
        LookClipboardMixin,
        AutosaveMixin,
        RollFileMixin,
        RatingsMixin,
        FramesMixin,
        CropMixin,
        CompareMixin,
        StockEditorMixin,
        HistoryMixin,
        AttemptsMixin,
        StripsMixin,
        NeutralSampleMixin,
        LoupeMixin,
        ShelfMixin,
        ExportMixin,
        PresetsCanvasMixin,
):
    # Class-level default so partially-built controllers (tests use __new__)
    # see the closed gate too; __init__ re-asserts it per instance.
    _first_render_done = False
    # Same reason: apply_profile_defaults reads this on its first call, which
    # can land before __init__ has run on a controller built with __new__.
    _last_synced_film_stock: str | None = None
    # 2026-09-20; see design/DECISIONS.md#d-controller-244
    _last_synced_film_is_positive: bool | None = None
    #: 'section.field' -> the value the last profile sync WROTE. A field
    #: whose current value differs from this is one the user moved by hand,
    #: and a stock change leaves it alone (H5).
    _last_synced_values: dict | None = None

    def __init__(self, *, viewer: Any, widgets: WidgetBundle):
        self._viewer = viewer
        self._widgets = widgets
        self._layers = ViewerLayerService(
            viewer=viewer,
            output_float_data_key=OUTPUT_FLOAT_DATA_KEY,
            output_color_space_key=OUTPUT_COLOR_SPACE_KEY,
            output_cctf_encoding_key=OUTPUT_CCTF_ENCODING_KEY,
            output_display_transform_key=OUTPUT_DISPLAY_TRANSFORM_KEY,
        )
        self._thread_pool = QThreadPool.globalInstance()
        self._active_simulation_worker: runtime.SimulationWorker | None = None
        self._active_simulation_label: str | None = None
        self._active_export_worker: runtime.ExportWorker | None = None
        self._runtime_simulator = None
        # The last render's DIR-coupler inversion margin (see
        # _coupler_margin_warning); None until a render has run.
        self._last_coupler_margin = None
        # 2026-09-26; see design/DECISIONS.md#d-controller-245
        self._last_render_stats = None
        # 2026-04-10, 2026-09-09; see design/DECISIONS.md#d-controller-246
        self._next_runtime_digest_applies_stock_specifics = False
        # The film stock the last profile sync ran for. Both dropdowns are
        # wired to apply_profile_defaults (app.py, protected), so this is how
        # a PRINT-side activation is told apart from a film-stock switch:
        # same stock => print change => keep the tuned film-side style.
        self._last_synced_film_stock: str | None = None
        self._last_synced_film_is_positive: bool | None = None
        self._last_synced_values: dict = {}
        self._current_input_image: np.ndarray | None = None
        self._current_input_path: str | None = None
        self._current_preview_image: np.ndarray | None = None
        # Phase 17A live white balance. For RAW (non-as_shot) and RGB loads
        # the stored full-res image is the UN-balanced base (RAW: daylight
        # ACES decode; RGB: file pixels) and white balance is applied as
        # input preparation: live on the preview-size base, transiently on
        # the full-res base inside scan/export renders (never stored, so the
        # one-full-res-buffer memory rule holds). _current_input_kind None
        # means the stored image is fully baked (as_shot RAW, legacy/test
        # paths) and the WB tail is the identity.
        self._current_input_kind: str | None = None      # 'raw' | 'rgb' | None
        self._current_preview_base: np.ndarray | None = None
        self._preview_wb_signature: tuple | None = None
        self._auto_preview_scheduled = False
        self._pending_auto_preview = False
        # Phase 16B: while any slider/knob/fader is being dragged, preview
        # frames render at reduced resolution for a live stream; when the
        # drag ends, one full-preview-resolution frame follows.
        self._last_preview_reduced = False
        self._drag_preview_divisor = 2.0
        set_drag_end_callback(self._on_drag_ended)
        # Phase 16C: stage-dirty partial re-render. Two byte-identical
        # tiers reuse the latest still-valid pipeline tap since the last full
        # preview: SCAN (only scan_finishing changed -> re-run scan, ~4x) and
        # DEVELOP (only develop-and-later sections changed -> re-run from
        # LOG_E_FILM, skipping preprocess+expose, ~1.7x).
        self._stage_cache: dict | None = None
        self._pending_stage_cache_keys: tuple | None = None
        self._preview_image_version = 0
        # 2026-07-18; see design/DECISIONS.md#d-controller-247
        self._first_render_done = False
        # Phase 10C stock editor: the modeless dialog + the UNSAVED recipe it
        # is currently editing (rendered live in place of the selected film).
        self._stock_editor = None
        self._live_stock_recipe = None
        self._pending_live_render = False
        self._active_simulation_reports_status = True
        self._roll = RollSession()
        self._roll_thumbs: dict[str, Any] = {}   # path -> QPixmap (GUI thread only)
        self._thumb_tasks: dict[str, Any] = {}   # path -> ThumbnailTask until delivered
        self._library_roll = None      # the library panel's Roll read-out
        self._roll_analysis_ev: float | None = None
        self._roll_analysis_running = False
        self._last_film_curves = None
        # Phase 17D: the look dict captured when the current roll frame was
        # entered, so leaving a frame knows whether anything changed.
        self._roll_entry_look: dict | None = None
        self._connect_live_white_balance()
        self._connect_recover_decode()
        self._connect_recover_denoise()
        self._connect_attempts()
        self._connect_raw_toggle()
        self._connect_crop_session()
        # Phase 17C: the simulation footer's Test strip trigger (wired here,
        # controller-side, so the protected app.py stays untouched).
        strip_signal = getattr(getattr(self._widgets, 'simulation', None),
                               'test_strip_requested', None)
        if strip_signal is not None and hasattr(strip_signal, 'connect'):
            strip_signal.connect(self.run_test_strip)
        # THE SYNC BASELINE, before the user can touch a stock dropdown. See
        # `_seed_sync_baseline`: without one, the first stock change of a
        # session discards every edited profile-synced field. Guarded, and
        # re-attempted before the first render, because the profile data may
        # not be loadable this early.
        self._seed_sync_baseline()
        loupe_signal = getattr(getattr(self._widgets, 'simulation', None),
                               'loupe_requested', None)
        if loupe_signal is not None and hasattr(loupe_signal, 'connect'):
            loupe_signal.connect(self.toggle_loupe)
        shelf_signal = getattr(getattr(self._widgets, 'simulation', None),
                               'shelf_requested', None)
        if shelf_signal is not None and hasattr(shelf_signal, 'connect'):
            shelf_signal.connect(self.open_shelf)
        # 2026-07-29; see design/DECISIONS.md#d-controller-248
        step_editor = getattr(getattr(self._widgets, 'simulation', None),
                              'test_strip_step', None)
        bands_editor = getattr(getattr(self._widgets, 'simulation', None),
                               'test_strip_bands', None)
        for editor, signal_name in ((step_editor, 'currentIndexChanged'),
                                    (bands_editor, 'valueChanged')):
            signal = getattr(editor, signal_name, None)
            if signal is not None and hasattr(signal, 'connect'):
                signal.connect(self._on_strip_settings_changed)
        self._watch_gpu_loss()

    # ------------------------------------------------- GPU device loss
    def _watch_gpu_loss(self) -> None:
        """Be told when the GPU is retired mid-session (2026-10-06). The app
        went on rendering on the CPU without a word, and Config kept naming
        the lost card. One engine listener for the process; one bridge per
        controller, held weakly so a closed one is simply skipped."""
        global _gpu_loss_hooked
        try:
            # stdlib-only at import: wgpu loads lazily, on the GPU path alone
            from spektrafilm.gpu.device import GPUDevice
        except Exception:
            return
        self._gpu_loss_bridge = _GpuLossBridge()
        # 2026-10-06; see design/DECISIONS.md#d-controller-249
        self._gpu_loss_bridge.lost.connect(self._on_gpu_lost, QtCore.Qt.QueuedConnection)
        _gpu_loss_bridges.add(self._gpu_loss_bridge)
        if not _gpu_loss_hooked:
            GPUDevice.add_loss_listener(_announce_gpu_loss)
            _gpu_loss_hooked = True

    def _on_gpu_lost(self, reason: str) -> None:
        set_status(self._viewer, 'GPU device lost: renders now run on the CPU', timeout_ms=0)
        try:
            from spektrafilm_gui.left_panel_config import refresh_gpu_facts
            refresh_gpu_facts()
        except Exception:
            logging.getLogger('spektrafilm.gui').exception('GPU facts refresh failed')
        parent = dialog_parent(self._viewer)
        if parent is None:          # no window to speak in (headless, tests)
            return
        # 20.2: the viewer's system-message card when the window has one
        # (it never blocks); the non-blocking Notice otherwise.
        card = notices.system_message_sink(parent)
        if card is not None:
            card.show_message(
                'warn', 'Rendering on the CPU',
                'The GPU stopped responding, so this session renders on the '
                'CPU. Restart the app to try the GPU again.', detail=reason)
            return
        # Shown WITHOUT blocking (open, not exec): a loss can land mid-render,
        # and nothing should wait on the user to read it.
        self._gpu_loss_notice = notices.Notice(
            'Rendering on the CPU',
            'The GPU stopped responding, so this session renders on the CPU.',
            level='warn', sub='Restart the app to try the GPU again.',
            detail=reason, parent=parent)
        self._gpu_loss_notice.open()

    # ------------------------------------------------- Phase 17A live WB
    def show_startup_placeholder(self) -> None:
        if self._white_border_layer() is not None:
            return

        state = collect_gui_state(widgets=self._widgets)
        preview_height = max(int(state.display.preview_max_size), 1)
        preview_width = max(
            int(round(preview_height * STARTUP_PREVIEW_ASPECT_RATIO[1] / STARTUP_PREVIEW_ASPECT_RATIO[0])),
            1,
        )
        placeholder_preview = np.zeros((preview_height, preview_width, 3), dtype=np.uint8)
        self._layers.set_or_add_input_preview_layer(
            placeholder_preview,
            watermark_source_size=(preview_height, preview_width),
            white_padding=state.display.white_padding,
            hide_output=True,
            set_active=True,
        )
        self._home_input_stack()

    def load_input_image(self, path: str) -> None:
        if path != self._current_input_path:
            self.apply_crop_session('Crop applied')
        gui_state = collect_gui_state(widgets=self._widgets)
        set_status(self._viewer, "Loading image...", timeout_ms=0)
        try:
            # Phase 17A: store the file pixels as the un-balanced base; the
            # white balance/tint (raw-style, in the linear light of the
            # declared input colour space) is applied live as input
            # preparation, so a WB change never re-reads the file.
            image = load_image_oiio(path)[..., :3]
        except (OSError, ValueError) as exc:
            notices.fail(dialog_parent(self._viewer), 'Import RGB', f'Failed to load RGB image.\n\n{exc}')
            set_status(self._viewer, 'Import RGB failed')
            return
        self._current_input_path = path
        self._current_input_kind = 'rgb'
        self._set_or_add_input_stack(image)
        self._sync_empty_state()
        wb_adjusted = gui_state.load_rgb.white_balance not in ('none', 'as_shot')
        if wb_adjusted:
            set_status(self._viewer, 'Loaded RGB with white balance adjustment')
        else:
            set_status(self._viewer, 'Loaded RGB')
        self._refit_session_frame()         # a re-read keeps an open session
        self._request_auto_preview_if_enabled()

    def load_raw_image(self, path: str) -> None:
        if path != self._current_input_path:
            self.apply_crop_session('Crop applied')
        gui_state = collect_gui_state(widgets=self._widgets)
        set_status(self._viewer, "Loading raw...", timeout_ms=0)
        lens_info: dict[str, str] = {}
        try:
            if gui_state.load_raw.white_balance == 'as_shot':
                # as_shot bakes camera multipliers at demosaic: the stored
                # image is final and WB edits need a reprocess (kind None).
                image = load_and_process_raw_file(
                    path,
                    white_balance='as_shot',
                    temperature=gui_state.load_raw.temperature,
                    tint=gui_state.load_raw.tint,
                    lens_correction=gui_state.load_raw.lens_correction,
                    output_colorspace=gui_state.input_image.input_color_space,
                    output_cctf_encoding=gui_state.input_image.apply_cctf_decoding,
                    lens_info_out=lens_info,
                    recover=recover_settings(gui_state, path),
                )
                kind = None
            else:
                # Phase 17A: store the daylight-base decode; white balance is
                # applied live as input preparation (no reprocess on WB edits).
                image = load_raw_daylight_base(
                    path,
                    lens_correction=gui_state.load_raw.lens_correction,
                    lens_info_out=lens_info,
                    recover=recover_settings(gui_state, path),
                )
                kind = 'raw'
        except (OSError, ValueError) as exc:
            self._report_unreadable(path, exc)
            set_status(self._viewer, 'Load raw failed')
            return

        self._mark_unreadable(path, False)
        self._current_input_path = path
        self._current_input_kind = kind
        self._decoded_recipe = decode_recipe(gui_state, path)
        self._set_or_add_input_stack(image)
        self._input_turns = 0
        self._refresh_dust_marks()
        self._sync_empty_state()

        lens_summary = lens_info.get('summary')
        if lens_summary:
            set_status(
                self._viewer,
                f"Loaded raw and applied lens correction: {lens_summary}",
            )
        elif gui_state.load_raw.lens_correction:
            set_status(self._viewer, "Loaded raw, lens correction not applied")
        else:
            set_status(self._viewer, "Loaded raw")
        self._refit_session_frame()         # a re-decode keeps an open session
        self._request_auto_preview_if_enabled()

    # ------------------------------------------------------------------ roll
    #: set by app.py: the edit panel's FX prewarm, run once the session has
    #: rendered (see `_schedule_fx_prewarm`)
    _fx_prewarm = None
    _fx_prewarm_done = False

    # ------------------------------------------------------------------
    # Copy/paste look (Ctrl+Shift+C / Ctrl+Shift+V): the in-app clipboard
    # between 'this one frame' and 'apply to all'.
    # ------------------------------------------------------------------
    _look_clipboard = None
    #: the FIELD PATHS the clipboard carries (18): the picker can hand back
    #: individual settings, so the clipboard remembers exactly which ones and
    #: the paste writes only those over the target's own values.
    _look_clipboard_fields = None

    # ------------------------------------------------------------------
    # Logs + roll autosave / crash recovery
    # ------------------------------------------------------------------
    _autosave_timer = None
    AUTOSAVE_INTERVAL_MS = 3 * 60 * 1000

    # ------------------------------------------------------------------
    # Roll session file (Phase 8): save/open the roll as a .sfroll file.
    # ------------------------------------------------------------------
    #: the last description the user typed, so re-saving a roll does not
    #: ask them to write it again
    _roll_description = ''

    def _roll_measure_frame(self, job) -> float:
        """One frame's auto-exposure EV, measured exactly like the pipeline's
        auto_exposure stage (the same film-raw meter, through the frame's own
        film and settings) on a decimated preview -- the EV measurement is
        downscale-robust (see test_autoexposure_gpu_parity)."""

        state = job.state
        if job.kind == 'raw':
            image = load_and_process_raw_file(
                job.path,
                white_balance=state.load_raw.white_balance,
                temperature=state.load_raw.temperature,
                tint=state.load_raw.tint,
                lens_correction=state.load_raw.lens_correction,
                output_colorspace=state.input_image.input_color_space,
                output_cctf_encoding=state.input_image.apply_cctf_decoding,
                recover=recover_settings(state, job.path),
            )
        else:
            image = load_image_oiio(job.path)[..., :3]
            image = apply_rgb_white_balance(
                image,
                white_balance=state.load_rgb.white_balance,
                temperature=state.load_rgb.temperature,
                tint=state.load_rgb.tint,
                color_space=state.input_image.input_color_space,
                cctf_encoded=state.input_image.apply_cctf_decoding,
            )
        image = np.asarray(image)
        step = max(1, int(np.ceil(max(image.shape[0], image.shape[1]) / 640)))
        small = np.ascontiguousarray(image[::step, ::step])
        with runtime.PIPELINE_RENDER_LOCK:
            return frozen_autoexposure_ev(small, build_params_from_state(state))

    # ------------------------------------------------------------------
    # Star ratings (per frame) + the nav-bar minimum-rating filter
    # ------------------------------------------------------------------
    _rating_filter = 0
    _single_rating = 0    # rating of the loaded image when there is no roll

    # ------------------------------------------------------------------
    # The filmstrip's right-click menu, BY INDEX (NAT 2026-08-25: "copy
    # settings, paste settings, set as reference, export options should
    # appear in the popup").
    #
    # All three frame actions act on the frame that was CLICKED, not on the
    # frame that happens to be open, and none of them switches frames to do
    # it -- the same call `rate_frame` makes and for the same reason: the
    # strip is where you work on a frame you are only looking at. Switching
    # would also mean a load and a render before the action could run, so
    # "copy this frame's settings" would have copied the previous frame's.
    # ------------------------------------------------------------------
    #: `system_message.EmptyRollState`, set by app.py; None in tests
    _empty_roll = None

    def refresh_preview_cache(self, *_args) -> None:
        input_image = self._current_input_image
        if input_image is None:
            return
        self._update_preview_cache(
            input_image,
            home_input_stack=False,
            hide_output=False,
        )

    # ------------------------------------------------------------------
    # Interactive crop tool (NegPy's Manual/Move rubber-band, on napari)
    # ------------------------------------------------------------------
    _crop_tool_mode = 'off'                 # 'off' | 'draw' | 'move'
    _CROP_TOOL_LAYER_NAME = 'crop tool'

    #: connected once, the first time the crop tool is armed
    _crop_angle_connected = False

    # -- Phase 9: persistent Lightroom-style crop frame ----------------------
    _frame_rect_uv = None
    _frame_initial = None            # (crop_active, center, size, angle) for cancel
    _frame_initial_ratio = None      # the Frame group's ratio text, for cancel

    # 2026-09-19; see design/DECISIONS.md#d-controller-256
    _skew_drag_state = None          # (skew_at_press, frame_at_press) while turning

    #: Shift turns this much slower, like the frame's other drags
    SKEW_FINE_FACTOR = 0.2

    def rotate_input_image_clockwise(self) -> None:
        self._rotate_input_image(quarter_turns=-1)

    def rotate_input_image_counterclockwise(self) -> None:
        self._rotate_input_image(quarter_turns=1)

    def _rotate_input_image(self, *, quarter_turns: int) -> None:
        input_image = self._current_input_image
        if input_image is None:
            return
        self.cancel_crop_session('Crop cancelled (photo turned)',
                                 refresh=False)

        rotated_image = np.rot90(np.asarray(input_image), k=int(quarter_turns))
        self._input_turns = (self._input_turns + int(quarter_turns)) % 4
        self._update_preview_cache(
            rotated_image,
            home_input_stack=True,
            hide_output=True,
        )
        self._refresh_dust_marks()
        self._request_auto_preview_if_enabled()

    # 2026-07-11; see design/DECISIONS.md#d-controller-259
    AUTO_LEVELS_MARGIN = 0.05

    # Auto-Levels gamma: after the levels mapping the luma median should sit
    # near the middle of the encoded range; the master gamma (out**(1/g), >1
    # brightens) is set to put it there. Deadband so balanced images keep
    # g=1.0, clamped to a conservative auto range (the slider allows 0.2-3).
    AUTO_LEVELS_GAMMA_TARGET = 0.5
    AUTO_LEVELS_GAMMA_DEADBAND = 0.05
    AUTO_LEVELS_GAMMA_RANGE = (0.5, 2.0)

    # 2026-07-11; see design/DECISIONS.md#d-controller-261
    _COMPARE_LAYER_NAME = 'compare'

    # ------------------------------------------------- reference frame (18D)
    #: The pinned reference PRINT. Session state, not roll state: what
    #: persists in the .sfroll is WHICH frame is the reference, and the image
    #: is re-captured whenever that frame renders (see _capture_reference).
    _reference_image = None

    # ------------------------------------------------ last kept version (B8)
    #: The print on screen when Keep was last pressed, per image (the roll's
    #: attempt key). Session state, like `_reference_image`: the VERSION
    #: persists in the roll, its pixels do not, so Split offers it only once
    #: a version has been kept in this session. A few images, not one: a
    #: roll is worked frame by frame, and coming back to a frame should find
    #: its kept print still there. Preview-size float32, ~20 MB each.
    _kept_images = None
    _KEPT_IMAGES_MAX = 8

    #: Where the Split seam sits between visits, 0..1 across the image.
    _split_position = 0.5

    #: `oklch(0.20 0 0)` — the page's surround under the base input on Split.
    #: Measured from the final HTML because `design/tokens.md` has no such
    #: grey: it sits between BG_1 and BG_2 and belongs to neither.
    _INPUT_SURROUND = '#161616'

    #: False only while the NEGATIVE button's solo view is being rendered
    _compare_wants_split = True

    # ---------------------------------------------- Compare on the Negative
    #: the target a negative render in flight will be compared with
    _negative_left_pending: str | None = None
    #: the compare mode showing the live negative on the left, or None
    _negative_left_mode: str | None = None

    # 2026-07-11; see design/DECISIONS.md#d-controller-264
    _FILM_BASE_FALLBACK = (0.7985, 0.5234, 0.3702)
    _blank_film_cache: dict = {}
    _last_compare = None

    def apply_profile_defaults(self, _selected_value: str) -> None:
        # Phase 10C: activating the 'Custom...' creator item is an editor
        # request, not a stock selection (the selector reverts itself).
        if _selected_value == 'Custom...':
            return
        state = collect_gui_state(widgets=self._widgets)
        if not state.simulation.film_stock or not state.simulation.print_paper:
            return

        # 2026-09-20; see design/DECISIONS.md#d-controller-265
        neutral_state = copy.deepcopy(state)
        neutral_state.simulation.raw = False
        params = build_params_from_state(neutral_state)
        synced_state = gui_state_from_params(
            digest_after_selection(params),
            film_stock=state.simulation.film_stock,
            print_paper=state.simulation.print_paper,
        )
        if state.simulation.raw:
            self._overlay_raw_couplers(state, synced_state)
        # 2026-09-28, 2026-09-29; see design/DECISIONS.md#d-controller-266
        apply_app_look_defaults(synced_state)
        # Both dropdowns land here. When the FILM stock has not changed, this
        # activation came from the print-paper selector (or is a re-pick of the
        # same stock), and the film-side style the user tuned must survive it:
        # digest_after_selection re-seeds the stock constants for every synced
        # field, which is why changing only the paper used to visibly reset the
        # couplers/halation/grain on screen.
        # Snapshot BEFORE either branch mutates `synced_state`: this is what
        # the sync would have written with no user edits in the way, and it
        # is the only honest baseline for 'the user moved this'.
        pristine_synced = copy.deepcopy(synced_state)
        print_side_only = self._last_synced_film_stock == state.simulation.film_stock
        carried, polarity_changed = 0, False
        if print_side_only:
            for section_name in FILM_STYLE_SECTIONS:
                tuned = getattr(state, section_name, None)
                target = getattr(synced_state, section_name, None)
                if tuned is None or target is None:
                    continue
                for field_name in PROFILE_SYNC_FIELDS.get(section_name, ()):
                    setattr(target, field_name, getattr(tuned, field_name))
        else:
            # 2026-08-29, 2026-09-20; see design/DECISIONS.md#d-controller-267
            polarity_changed = self._film_polarity_changed(params)
            carried = self._preserve_user_edits(
                state, synced_state, polarity_changed=polarity_changed)
            self._report_stock_change(carried, state.simulation.film_stock,
                                      polarity_changed=polarity_changed)
        self._last_synced_film_stock = state.simulation.film_stock
        self._remember_film_polarity(state.simulation.film_stock, params)
        self._apply_profile_sync_state(synced_state)
        # 2026-09-24; see design/DECISIONS.md#d-controller-268
        if not print_side_only and carried <= 0 and not polarity_changed:
            film_info = getattr(getattr(params, 'film', None), 'info', None)
            estimate = getattr(film_info, 'grain_estimate', None)
            if estimate:
                set_status(self._viewer, f'{state.simulation.film_stock}: {estimate}')
        self._refresh_raw_availability()
        # 2026-09-04; see design/DECISIONS.md#d-controller-269
        self._remember_synced_values(pristine_synced)
        self._point_baselines_at_the_stock()
        if not print_side_only:
            # 2026-08-29, 2026-09-03, 2026-09-04; see design/DECISIONS.md#d-controller-270
            self._next_runtime_digest_applies_stock_specifics = (carried == 0)
            if state.simulation.raw:
                # 2026-09-20; see design/DECISIONS.md#d-controller-271
                self._next_runtime_digest_applies_stock_specifics = False

    # 2026-09-20; see design/DECISIONS.md#d-controller-272
    POLARITY_RESEEDED_SECTIONS = ('couplers',)

    # 2026-09-11; see design/DECISIONS.md#d-controller-273
    _STOCK_SELECTION_KEYS = ('simulation.film_stock', 'simulation.print_paper')

    def apply_film_profile_defaults(self, film_stock: str) -> None:
        self.apply_profile_defaults(film_stock)

    # 2026-09-20; see design/DECISIONS.md#d-controller-275
    def _raw_editor(self):
        return getattr(getattr(self._widgets, 'simulation', None), 'raw', None)

    def _connect_raw_toggle(self) -> None:
        """Wired here so the protected app.py stays untouched."""
        editor = self._raw_editor()
        toggled = getattr(editor, 'toggled', None)
        if toggled is not None and hasattr(toggled, 'connect'):
            toggled.connect(self._on_raw_toggled)
        stock = getattr(getattr(self._widgets, 'simulation', None),
                        'film_stock', None)
        changed = getattr(stock, 'currentTextChanged', None)
        if changed is not None and hasattr(changed, 'connect'):
            # Programmatic changes too (a frame, a preset, a roll): the
            # switch is greyed out the moment the stock has no raw version.
            changed.connect(lambda _text: self._refresh_raw_availability())
        self._refresh_raw_availability()

    def raw_available(self) -> bool:
        """Does the selected film have a raw sibling? Never for B&W (a rule)."""
        stock = getattr(getattr(self._widgets, 'simulation', None),
                        'film_stock', None)
        slug = getattr(stock, 'value', None)
        if not slug:
            return False
        try:
            return has_raw_sibling(str(slug))
        except Exception:
            return False

    def _refresh_raw_availability(self) -> None:
        editor = self._raw_editor()
        if editor is None or not hasattr(editor, 'setEnabled'):
            return
        available = self.raw_available()
        editor.setEnabled(available)
        editor.setToolTip(
            '' if available else
            'This film has no raw version. Black-and-white films never do.')
        shelf = getattr(self, '_shelf', None)
        if shelf is not None and hasattr(shelf, 'set_raw_state'):
            shelf.set_raw_state(checked=bool(getattr(editor, 'value', False)),
                                available=available)

    def _shelf_set_raw(self, checked: bool) -> None:
        """The Shelf's Raw switch writes the ONE state, through the editor,
        so the render, the coupler re-seed and the panel row all follow."""
        editor = self._raw_editor()
        if editor is not None and bool(editor.value) != bool(checked):
            editor.value = bool(checked)

    def _raw_coupler_state(self, state):
        """The synced state the RAW film would give, or None when the selected
        film's raw sibling brings no couplers of its own (every negative)."""
        neutral = copy.deepcopy(state)
        neutral.simulation.raw = False
        raw_film = load_raw_sibling(build_params_from_state(neutral).film)
        if raw_film is None or not raw_carries_couplers(raw_film):
            return None
        raw_state = copy.deepcopy(state)
        raw_state.simulation.raw = True
        return gui_state_from_params(
            digest_after_selection(build_params_from_state(raw_state)),
            film_stock=state.simulation.film_stock,
            print_paper=state.simulation.print_paper,
        )

    def _overlay_raw_couplers(self, state, synced_state) -> bool:
        """Lay the raw file's own DIR couplers over a synced state.

        2026-09-20; see design/DECISIONS.md#d-controller-311
        """
        source = self._raw_coupler_state(state)
        if source is None:
            return False
        for field_name in PROFILE_SYNC_FIELDS.get('couplers', ()):
            setattr(synced_state.couplers, field_name,
                    getattr(source.couplers, field_name))
        return True

    def _on_raw_toggled(self, checked: bool) -> None:
        """Raw on/off re-seeds the coupler block where the raw file brings
        its own (NAT 2026-09-20), and says so. Everything else is untouched:
        the switch picks which of two files one stock is, nothing more."""
        self._refresh_raw_availability()
        state = collect_gui_state(widgets=self._widgets)
        if not state.simulation.film_stock or not state.simulation.print_paper:
            return
        if self._raw_coupler_state(state) is None:
            return
        target = copy.deepcopy(state)
        if checked:
            self._overlay_raw_couplers(state, target)
            note = 'Raw: the DIR couplers took the raw film\u2019s own values.'
        else:
            neutral = copy.deepcopy(state)
            neutral.simulation.raw = False
            source = gui_state_from_params(
                digest_after_selection(build_params_from_state(neutral)),
                film_stock=state.simulation.film_stock,
                print_paper=state.simulation.print_paper,
            )
            for field_name in PROFILE_SYNC_FIELDS.get('couplers', ()):
                setattr(target.couplers, field_name,
                        getattr(source.couplers, field_name))
            note = 'Raw off: the DIR couplers took the film\u2019s own values back.'
        self._apply_profile_sync_state(target)
        baseline = dict(self._last_synced_values or {})
        for field_name in PROFILE_SYNC_FIELDS.get('couplers', ()):
            baseline[f'couplers.{field_name}'] = self._as_the_widget_holds(
                'couplers', field_name, getattr(target.couplers, field_name))
        self._last_synced_values = baseline
        set_status(self._viewer, f'{state.simulation.film_stock}: {note}')

    # 2026-09-25; see design/DECISIONS.md#d-controller-277
    _MISSING_SELECTIONS = (
        ('simulation', 'film_stock', 'film stock'),
        ('simulation', 'print_paper', 'paper'),
        ('input_image', 'color_filter', 'taking filter'),
        ('input_image', 'color_filter_2', 'second taking filter'),
    )

    def _apply_profile_sync_state(self, synced_state) -> None:
        profile_sync.apply_profile_sync_state(
            widgets=self._widgets,
            synced_state=synced_state,
            profile_sync_fields=PROFILE_SYNC_FIELDS,
        )

    def run_preview(self) -> None:
        # Phase 17D: an explicit render is a natural expose moment.
        self._auto_capture_attempt()
        self._run_preview(report_status=True)

    def _run_preview(self, *, report_status: bool) -> None:
        self._start_simulation(
            source_layer_name=INPUT_PREVIEW_LAYER_NAME,
            mode_label='Preview',
            report_status=report_status,
        )

    def run_scan(self) -> None:
        # Phase 17D: an explicit render is a natural expose moment.
        self._auto_capture_attempt()
        self._start_simulation(source_layer_name=INPUT_LAYER_NAME, mode_label='Scan')

    def request_auto_preview(self, *_args) -> None:
        # Every control change funnels through here, so it doubles as the
        # undo-history hook (debounced; skipped while undo/redo re-applies).
        self._schedule_history_push()
        if self._auto_preview_scheduled:
            return
        self._auto_preview_scheduled = True
        QTimer.singleShot(0, self._run_scheduled_auto_preview)

    # ------------------------------------------------------------------
    # Undo / redo: a debounced stack of GUI-state snapshots. Covers every
    # parameter change (anything wired to auto-preview); loading files and
    # roll membership are not part of the stack.
    # ------------------------------------------------------------------
    _HISTORY_LIMIT = 100
    _history: list | None = None
    _history_index: int = -1
    _history_applying: bool = False
    _history_timer = None

    # ------------------------------------------------------------------
    # Test strip (Phase 17C): EV bands on the image. A triggered
    # instrument, not a live mode — N full-width preview renders (one per
    # band value) composited into labeled vertical bands. Full-width
    # renders make every content-dependent reference identical across
    # bands by construction (autoexposure measures the same full image;
    # the print-exposure midgray factor is profile-derived and never
    # varies with content), so a band IS the full render at that EV,
    # cropped — the EXACT grade of the proposal.
    # ------------------------------------------------------------------
    _strip_bands = None            # list[StripBand] while a strip is shown
    _strip_worker = None
    _strip_worker_retired = None   # emit-unwind parking slot (see loupe note)

    def run_test_strip(self) -> None:
        """Toggle: render an EV strip over the print area (button/T)."""

        if self._strip_bands is not None:
            self._dismiss_test_strip('Test strip dismissed')
            return
        if self._strip_worker is not None:
            set_status(self._viewer, 'Test strip already rendering...')
            return
        if self._active_simulation_worker is not None or self._active_export_worker is not None:
            set_status(self._viewer, 'Busy rendering; try the test strip afterwards')
            return
        image_data = self._simulation_input_image(
            source_layer_name=INPUT_PREVIEW_LAYER_NAME)
        if image_data is None:
            set_status(self._viewer, 'Load an image before a test strip')
            return

        state = collect_gui_state(widgets=self._widgets)
        simulation_section = getattr(self._widgets, 'simulation', None)
        step_editor = getattr(simulation_section, 'test_strip_step', None)
        bands_editor = getattr(simulation_section, 'test_strip_bands', None)
        step = STRIP_STEPS[step_editor.currentIndex()] if step_editor is not None else 1.0
        bands = int(bands_editor.value()) if bands_editor is not None else 5
        base_ev = float(state.simulation.exposure_compensation_ev)
        deltas = strip_deltas(step, bands)

        # Per-band params, prepared on the GUI thread (they read controller
        # state); the worker only digests + renders + display-transforms.
        from spektrafilm_gui.loupe import drop_placed_lamp

        band_params = []
        for delta in deltas:
            band_state = clone_gui_state(state)
            band_state.simulation.exposure_compensation_ev = base_ev + delta
            # The params mapper freezes the print normalization at EV 0 for
            # EVERY GUI render (NAT: the strip look IS the look), so bands
            # and adopted renders share one normalization by construction.
            band_params.append(self._configure_simulation_params(
                build_params_from_state(band_state),
                source_layer_name=INPUT_PREVIEW_LAYER_NAME,
            ))
            # 2026-09-24; see design/DECISIONS.md#d-controller-278
            drop_placed_lamp(band_params[-1])
        image = np.double(np.asarray(image_data))
        output_color_space = state.simulation.output_color_space
        use_display_transform = state.display.use_display_transform

        def render_bands():
            # FREEZE the auto-exposure reference for the whole strip.
            #
            # Auto-exposure is content-dependent: it measures the frame it
            # is handed. Every band renders the same full frame today, so
            # they already agree -- but the freeze makes that a PROPERTY
            # rather than a coincidence, and it is what a band-cropped
            # strip needs to stay honest (a band measuring its own slice
            # would expose that slice differently from the full frame, so
            # the ladder would no longer be a pure EV ladder).
            #
            # Measured once, here, from the same image every band renders.
            frozen_ev = None
            try:
                first = band_params[0]
                held = getattr(first.camera, 'auto_exposure_ev_override', None)
                if getattr(first.camera, 'auto_exposure', False) and held is not None:
                    frozen_ev = float(held)      # a held reading IS the frozen EV
                elif getattr(first.camera, 'auto_exposure', False):
                    from spektrafilm_gui.loupe import frozen_autoexposure_ev

                    # 2026-09-06, 2026-09-29; see design/DECISIONS.md#d-controller-279
                    with runtime.PIPELINE_RENDER_LOCK:
                        frozen_ev = frozen_autoexposure_ev(image, first)
                    for params in band_params:
                        params.camera.auto_exposure_ev_override = frozen_ev
            except Exception:
                frozen_ev = None          # measure per band, as before

            # BAND CROP: render each band plus a halo instead of N full
            # frames. Only legal because (a) the EV is frozen above, so no
            # band measures its own slice, (b) io.source_frame_long_side
            # keeps the film-plane pitch the WHOLE frame's, and (c)
            # settings.render_origin keys the grain/glare RNG on absolute
            # position. Drop any one and the bands stop being pieces of one
            # photograph. Measured edge-trimmed agreement with the
            # full-width render: ~1.4e-3 with grain active.
            crops = None
            try:
                from spektrafilm_gui.test_strip import plan_band_crops

                frame = np.asarray(image)
                crops = plan_band_crops(band_params[0], frame.shape,
                                        len(band_params), frozen_ev)
                if crops is not None:
                    long_side = int(max(frame.shape[0], frame.shape[1]))
                    for params in band_params:
                        params.io.source_frame_long_side = long_side
            except Exception:
                crops = None            # any doubt -> the exact full-width path

            display_renders = []
            float_renders = []
            band_sources = ([np.asarray(image)[:, c.crop_x0:c.crop_x1] for c in crops]
                            if crops is not None else [image] * len(band_params))
            for band_index, params in enumerate(band_params):
                if crops is not None:
                    params.settings.render_origin = (crops[band_index].crop_x0, 0)
                # Per-band lock: one pipeline at a time across threads
                # (shared wgpu device), and a pending preview can slot in
                # between bands.
                with runtime.PIPELINE_RENDER_LOCK:
                    # False: the state already carries the selection defaults
                    # (synced when the stock was picked) plus the user's
                    # tuning. True would re-stomp them to stock constants and
                    # the strip would stop matching the tuned preview.
                    digested = digest_params(params, apply_stocks_specifics=False)
                    scan = np.asarray(runtime_simulator(digested).process(
                        band_sources[band_index]))
                display, _status = runtime.prepare_output_display_image(
                    scan,
                    output_color_space=output_color_space,
                    use_display_transform=use_display_transform,
                    padding_pixels=0.0,
                    imagecms_module=ImageCms,
                    colour_module=colour,
                    pil_image_module=PILImage,
                )
                float_renders.append(scan)
                display_renders.append(display)
            if crops is not None:
                from spektrafilm_gui.test_strip import compose_strip_from_crops

                display_strip, bands_geometry = compose_strip_from_crops(
                    display_renders, crops, base_value=base_ev, deltas=deltas)
                float_strip, _ = compose_strip_from_crops(
                    float_renders, crops, base_value=base_ev, deltas=deltas)
            else:
                display_strip, bands_geometry = compose_strip(
                    display_renders, base_value=base_ev, deltas=deltas)
                float_strip, _ = compose_strip(
                    float_renders, base_value=base_ev, deltas=deltas)
            return (draw_band_chrome(display_strip, bands_geometry),
                    float_strip, bands_geometry)

        set_status(self._viewer,
                   f'Test strip: rendering {bands} bands ({base_ev:+.2f} EV center)...',
                   timeout_ms=0)
        worker = runtime.TaskWorker(render_bands)
        worker.signals.finished.connect(
            lambda result: self._on_test_strip_ready(
                result, output_color_space=output_color_space,
                use_display_transform=use_display_transform))
        worker.signals.failed.connect(self._on_test_strip_failed)
        self._strip_worker = worker
        self._thread_pool.start(worker)

    # ------------------------------------------------------------------
    # The white-balance eyedropper (NAT 2026-09-04: "White balance picker
    # doesn`t work either").
    #
    # It never worked. `FileSection.sample_neutral_requested` was emitted
    # and NOTHING WAS CONNECTED TO IT anywhere in the app -- the signal`s
    # own comment said "the controller listens, puts the canvas into
    # sampling mode, and calls `sample_landed` with the result", and the
    # controller had no such method. The strip half was written, the
    # listening half never was.
    # ------------------------------------------------------------------
    #: how far either side of the click to average, in image pixels. A
    #: single pixel of a RAW is noise; a small patch is what every other
    #: eyedropper samples and what makes the result repeatable.
    NEUTRAL_SAMPLE_RADIUS = 3

    _neutral_sample_armed = False

    _strip_hud = None

    # ------------------------------------------------------------------
    # Loupe (Phase 17B core): one tile of the image at 1:1 input
    # resolution through the FULL production pipeline, with frame-level
    # references frozen from the full image (never the patch). The
    # interactive circle UI builds on this; the renderer stands alone so
    # its gates are pinned headless.
    # ------------------------------------------------------------------
    _loupe_frozen_ev_cache: tuple | None = None

    _loupe_sim_cache = None      # (sim_key, Simulator) — worker-serialized

    # 17B: True when the last loupe tile's grain was produced by the
    # position-stable GPU path at the tile's true frame origin — the only
    # case where 'grain as scanned' is an honest claim.
    _loupe_grain_as_scanned = False

    def _run_loupe_job(self, job):
        """Heavy half (worker-safe: one loupe render at a time via the busy
        flag, and the shared render lock — the wgpu device is
        single-threaded). The digested simulator is cached per state
        signature, so same-look hovers pay only the tile render."""
        from spektrafilm_gui.loupe import render_loupe_tile

        with runtime.PIPELINE_RENDER_LOCK:
            cached = self._loupe_sim_cache
            if cached is not None and cached[0] == job['sim_key']:
                simulator = cached[1]
            else:
                # False: render the tuned state, not the stock constants, so
                # the loupe matches the preview it magnifies.
                simulator = runtime_simulator(
                    digest_params(job['params'], apply_stocks_specifics=False))
                self._loupe_sim_cache = (job['sim_key'], simulator)
            # Re-aim the (possibly cached) simulator at THIS tile's position.
            try:
                simulator.set_render_origin(job['origin'])
            except AttributeError:
                pass
            # Did a position-stable GPU grain path actually run, at THIS
            # tile's origin? Only then is 'as scanned' true. ASKED of the
            # render rather than read out of a module global afterwards:
            # the global belonged to the fused chain, which 20C5 declined,
            # so this claim was permanently False while the keying itself
            # had moved to the layered chain. See gpu/provenance.py.
            try:
                from spektrafilm.gpu import provenance as _prov
            except Exception:
                _prov = None
            if _prov is None:
                self._loupe_grain_as_scanned = False
                return render_loupe_tile(
                    job['padded'],
                    tile=job['tile'],
                    simulator=simulator,
                    pixel_size_um=job['pixel_size_um'],
                    autoexposure_ev=job['autoexposure_ev'],
                )
            with _prov.capture() as ran:
                rendered = render_loupe_tile(
                    job['padded'],
                    tile=job['tile'],
                    simulator=simulator,
                    pixel_size_um=job['pixel_size_um'],
                    autoexposure_ev=job['autoexposure_ev'],
                )
            tile = job['tile']
            origin = (int(tile.pad_x0), int(tile.pad_y0))
            honest = ran.keyed_origin('grain') == origin
            if job.get('veil_active'):
                honest = honest and ran.keyed_origin('glare') == origin
            if job.get('lamp_placed'):
                honest = False
            self._loupe_grain_as_scanned = honest
            return rendered

    # ---- interactive loupe mode (two-fidelity drag) -------------------
    _loupe_active = False
    _loupe_canvas_mode = False   # circle on the canvas overlay vs napari layer
    _loupe_serial = 0            # bumps on every move: the stale guard
    _loupe_rest_timer = None
    _loupe_render_busy = False
    # STRONG reference to the in-flight worker (NAT crash 3, python313.dll
    # AV at one fixed offset, PRE-DATING every lock): with no reference,
    # the TaskWorker wrapper — and its signals QObject — could be
    # garbage-collected while the worker thread was still emitting from
    # it. Strip/export workers always kept refs; the loupe did not.
    _loupe_worker = None
    # RETIREMENT slot (NAT round 5, 'Signal source has been deleted'): a
    # queued completion can be DELIVERED while the worker thread is still
    # unwinding inside emit(), so the handler must NOT drop the last
    # reference immediately — the finished worker parks here for one more
    # completion cycle, by which time its thread has long unwound.
    _loupe_worker_retired = None
    _loupe_center_uv = (0.5, 0.5)
    _LOUPE_REST_MS = 150

    _loupe_size_px = None        # logical diameter (Ctrl+scroll, 180-420)

    def _on_loupe_rest(self) -> None:
        """AT REST: kick ONE full-quality tile render to the worker;
        coalesced (a busy render defers to its own completion) and
        stale-guarded (a move after kick drops the result)."""
        if not self._loupe_active or self._loupe_render_busy:
            return
        job = self._prepare_loupe_job(self._loupe_center_uv,
                                      tile_px=self._loupe_tile_px())
        if job is None:
            return
        serial = self._loupe_serial

        def heavy():
            image = self._run_loupe_job(job)
            display, _status = runtime.prepare_output_display_image(
                image,
                output_color_space=job['output_color_space'],
                use_display_transform=job['use_display_transform'],
                padding_pixels=0.0,
                imagecms_module=ImageCms,
                colour_module=colour,
                pil_image_module=PILImage,
            )
            return display

        worker = runtime.TaskWorker(heavy)
        worker.signals.finished.connect(
            lambda display: self._on_loupe_fine_ready(serial, display))
        worker.signals.failed.connect(self._on_loupe_fine_failed)
        self._loupe_render_busy = True
        self._loupe_worker = worker      # keep the signals QObject alive
        self._thread_pool.start(worker)

    # ------------------------------------------------------------------
    # The Shelf (Phase 17E): one modeless library browser over the
    # existing stores. Every action routes through the EXISTING apply
    # paths, so a shelf apply is identical to the ordinary one by
    # construction; browsing emits nothing and renders nothing.
    # ------------------------------------------------------------------
    _shelf = None
    _shelf_popover = None

    def _request_auto_preview_if_enabled(self) -> None:
        # 2026-07-25; see design/DECISIONS.md#d-controller-283
        if self._input_mode:
            return
        if not self._auto_preview_enabled() or self._current_preview_image is None:
            return
        self.request_auto_preview()

    def report_display_transform_status(self, enabled: bool) -> None:
        if enabled and not self.sync_display_transform_availability(report_status=True):
            return
        set_status(self._viewer, runtime.display_transform_status_message(enabled, imagecms_module=ImageCms))

    def set_gray_18_canvas_enabled(self, enabled: bool) -> None:
        set_canvas_background(self._viewer, gray_18_canvas=enabled)

    def set_output_interpolation_mode(self, mode: str) -> None:
        output_layer = self._output_layer()
        if output_layer is None:
            return
        self._layers.set_output_layer_interpolation(output_layer, mode)

    def sync_display_transform_availability(self, *, report_status: bool) -> bool:
        if runtime.display_profile_available(imagecms_module=ImageCms):
            return True

        self._set_display_transform_checked(False)
        if report_status:
            set_status(self._viewer, 'Display transform unavailable: no display profile detected, disabled')
        return False

    def save_output_layer(self) -> None:
        output_layer = self._output_layer()
        if output_layer is None:
            notices.warn(dialog_parent(self._viewer), 'Save output', 'Run a simulation before saving the output layer.')
            return

        if self._current_input_path is not None:
            default_name = Path(self._current_input_path).stem + '.jpg'
        else:
            default_name = 'output.jpg'

        filepath, _ = _DirMemoryDialog('save_output').get_save_file_name(
            dialog_parent(self._viewer),
            'Save output image',
            default_name,
            'Images (*.jpg *.jpeg *.png *.tif *.tiff *.exr)',
        )
        if not filepath:
            return

        gui_state = collect_gui_state(widgets=self._widgets)
        float_image_data = self._output_layer_float_data()
        if float_image_data is None:
            image_data = runtime.normalized_image_data(np.asarray(output_layer.data)[..., :3])
        else:
            image_data = np.asarray(float_image_data)[..., :3]

        source_color_space, source_cctf_encoding = self._output_layer_render_settings(
            default_color_space=gui_state.simulation.output_color_space,
            default_cctf_encoding=True,
        )
        saving_color_space = gui_state.simulation.saving_color_space
        saving_cctf_encoding = gui_state.simulation.saving_cctf_encoding
        if source_color_space != saving_color_space:
            image_data = colour.RGB_to_RGB(
                image_data,
                source_color_space,
                saving_color_space,
                apply_cctf_decoding=source_cctf_encoding,
                apply_cctf_encoding=saving_cctf_encoding,
            )
        elif source_cctf_encoding != saving_cctf_encoding:
            image_data = colour.RGB_to_RGB(
                image_data,
                source_color_space,
                saving_color_space,
                apply_cctf_decoding=source_cctf_encoding,
                apply_cctf_encoding=saving_cctf_encoding,
            )

        source_metadata = None

        if self._current_input_path is not None:
            source_metadata = drop_source_icc_profile(
                read_image_metadata(self._current_input_path))

        try:
            save_image_oiio(
                filepath,
                image_data,
                color_space=saving_color_space,
                cctf_encoding=saving_cctf_encoding,
                jpeg_quality=gui_state.simulation.export_jpeg_quality,
            )
        except (OSError, ValueError) as exc:
            notices.fail(dialog_parent(self._viewer), 'Save output', f'Failed to save output image.\n\n{exc}')
            return

        metadata_write_error = None
        try:
            write_image_metadata(
                filepath,
                source_metadata,
                saving_color_space=saving_color_space,
                saving_cctf_encoding=saving_cctf_encoding,
                custom_tags=self._custom_metadata_tags(),
            )
        except Exception as exc:
            metadata_write_error = exc

        if metadata_write_error is not None:
            set_status(
                self._viewer,
                f"Saved output image to {filepath}, but failed to copy metadata: {metadata_write_error}",
            )
        else:
            set_status(self._viewer, f"Saved output image to {filepath}")

    # ------------------------------------------------------------------ #
    # Export: full-precision (or fast-LUT) re-render written off the UI thread.
    # Acts as a film scanner -- latitude-preserving, high-bit base for grading
    # elsewhere. Unlike save_output_layer (which writes the already-rendered
    # interactive buffer), export re-renders the FULL-resolution input with the
    # export settings so 16-bit output is genuine high precision from the float
    # pipeline.
    # ------------------------------------------------------------------ #
    def export_output(self) -> None:
        if self._active_export_worker is not None:
            # Second click while exporting acts as Cancel (pre-render / pre-write
            # granularity; a single full-res render is not interrupted mid-pass).
            self.cancel_export()
            return
        if self._active_simulation_worker is not None:
            set_status(self._viewer, 'A render is already running; wait before exporting.')
            return

        state = collect_gui_state(widgets=self._widgets)
        ext, bit_depth = export_format_spec(state.simulation.export_format)
        saving_color_space, saving_cctf_encoding = _saving_space_for(
            ext, state.simulation.saving_color_space,
            state.simulation.saving_cctf_encoding)
        full_precision = state.simulation.export_full_precision

        # Source of the export pixels:
        #  - full precision -> re-render the full-resolution INPUT (slow, LUT-off);
        #  - otherwise      -> reuse the buffer from the last Scan/Preview, no recompute.
        image_data = None
        prerendered = None
        source_color_space = saving_color_space
        source_cctf_encoding = saving_cctf_encoding
        if full_precision:
            image_data = self._simulation_input_image(source_layer_name=INPUT_LAYER_NAME)
            if image_data is None:
                notices.warn(dialog_parent(self._viewer), 'Export', 'Load an input image before exporting.')
                return
        else:
            prerendered = self._output_layer_float_data()
            if prerendered is None:
                notices.warn(
                    dialog_parent(self._viewer),
                    'Export',
                    'Run a Scan first, or tick "Full-precision re-render" to render from the input.',
                )
                return
            source_color_space, source_cctf_encoding = self._output_layer_render_settings(
                default_color_space=state.simulation.output_color_space,
                default_cctf_encoding=True,
            )

        if self._current_input_path is not None:
            default_name = Path(self._current_input_path).stem + '.' + ext
        else:
            default_name = 'export.' + ext

        file_filter = self._export_file_filter(ext)
        filepath, _ = _DirMemoryDialog('save_output').get_save_file_name(
            dialog_parent(self._viewer),
            'Export image',
            default_name,
            file_filter,
        )
        if not filepath:
            return
        # The export-panel format is the source of truth: force the extension so
        # the on-disk format and bit depth always match the chosen preset.
        filepath = str(Path(filepath).with_suffix('.' + ext))

        params = None
        if full_precision:
            self._sync_white_border(white_padding=state.display.white_padding)
            params = configure_export_params(
                build_params_from_state(state),
                saving_color_space=saving_color_space,
                saving_cctf_encoding=saving_cctf_encoding,
                full_precision=True,
            )
            # v1.1.1 fix: exports do NOT go through _configure_simulation_params,
            # so the per-photo grain seed must be stamped here too — otherwise
            # the exported negative's grain field differs from the scan's.
            self._stamp_auto_grain_seed(params)

        source_metadata = None
        if self._current_input_path is not None:
            source_metadata = drop_source_icc_profile(
                read_image_metadata(self._current_input_path))

        request = ExportRequest(
            image=np.double(image_data) if image_data is not None else None,
            params=params,
            filepath=filepath,
            saving_color_space=saving_color_space,
            saving_cctf_encoding=saving_cctf_encoding,
            bit_depth=bit_depth,
            source_metadata=source_metadata,
            full_precision=full_precision,
            prerendered=(np.asarray(prerendered) if prerendered is not None else None),
            source_color_space=source_color_space,
            source_cctf_encoding=source_cctf_encoding,
            custom_tags=self._custom_metadata_tags(),
            jpeg_quality=state.simulation.export_jpeg_quality,
        )
        worker = ExportWorker(request, render_fn=self._render_export, write_fn=self._write_export)
        worker.signals.progress.connect(self._on_export_progress)
        worker.signals.finished.connect(self._on_export_finished)
        worker.signals.failed.connect(self._on_export_failed)
        self._active_export_worker = worker
        self._set_export_controls_enabled(False)
        quality = 'full precision' if full_precision else 'current scan'
        set_status(self._viewer, f'Exporting {Path(filepath).name} ({quality})...', timeout_ms=0)
        self._thread_pool.start(worker)

    def export_roll(self) -> None:
        """Batch export: render and write EVERY frame of the roll (full
        precision, each frame with its own stored look) into a chosen folder.
        Runs on a single BatchExportWorker; cancel applies between frames."""
        if self._active_export_worker is not None:
            self.cancel_export()
            return
        if self._active_simulation_worker is not None:
            set_status(self._viewer, 'A render is already running; wait before exporting.')
            return
        if not self._roll.files:
            notices.warn(dialog_parent(self._viewer), 'Export roll', 'The roll is empty. Add files first.')
            return


        # Freeze the on-screen look into the current frame before resolving.
        current_state = collect_gui_state(widgets=self._widgets)
        self._roll.store_state_for_current(current_state)

        export_section = getattr(self._widgets, 'export', None)
        sync_settings = True
        sync_metadata = True
        if export_section is not None and callable(getattr(export_section, 'sync_export_settings_value', None)):
            sync_settings = export_section.sync_export_settings_value()
        if export_section is not None and callable(getattr(export_section, 'sync_metadata_value', None)):
            sync_metadata = export_section.sync_metadata_value()

        out_dir = QFileDialog.getExistingDirectory(dialog_parent(self._viewer), 'Export roll to folder')
        if not out_dir:
            return

        # Sync metadata ON: the Metadata panel values stamp every frame (the
        # per-frame star rating is merged per job below, never shared).
        # OFF: frames keep only their own source-file metadata (no panel tags).
        panel_tags = self._custom_metadata_tags() if sync_metadata else None
        if panel_tags is not None:
            panel_tags.pop('Exif.Image.Rating', None)   # per-frame, see below
        jobs = []
        skipped_by_filter = 0
        for roll_file in self._roll.files:
            if not self._passes_rating_filter(roll_file):
                skipped_by_filter += 1
                continue
            frame_state = self._roll.state_for(roll_file) or deepcopy(current_state)
            prefs = current_state.simulation if sync_settings else frame_state.simulation
            ext, bit_depth = export_format_spec(prefs.export_format)
            frame_space, frame_cctf = _saving_space_for(
                ext, prefs.saving_color_space, prefs.saving_cctf_encoding)
            frame_tags = dict(panel_tags) if panel_tags is not None else None
            rating = self._roll.rating_for(roll_file)
            if rating:
                frame_tags = dict(frame_tags or {})
                frame_tags['Exif.Image.Rating'] = str(rating)
            jobs.append(runtime.BatchFrameJob(
                name=roll_file.name,
                path=roll_file.path,
                kind=roll_file.kind,
                state=frame_state,
                saving_color_space=frame_space,
                saving_cctf_encoding=frame_cctf,
                ext=ext,
                bit_depth=bit_depth,
                custom_tags=frame_tags,
                jpeg_quality=prefs.export_jpeg_quality,
            ))
        if not jobs:
            notices.warn(dialog_parent(self._viewer), 'Export roll',
                                'The star filter excludes every frame of the roll.')
            return
        if skipped_by_filter:
            set_status(self._viewer,
                       f'Star filter >= {self._rating_filter}: exporting '
                       f'{len(jobs)} of {len(self._roll.files)} frames')

        worker = runtime.BatchExportWorker(
            jobs,
            render_fn=self._batch_render_frame,
            write_fn=lambda job, buffer: self._batch_write_frame(job, buffer, out_dir),
        )
        worker.signals.progress.connect(self._on_export_progress)
        worker.signals.finished.connect(self._on_export_finished)
        worker.signals.failed.connect(self._on_export_failed)
        self._active_export_worker = worker
        self._set_export_controls_enabled(False)
        set_status(self._viewer, f'Exporting roll: {len(jobs)} frame(s) to {out_dir}...', timeout_ms=0)
        self._thread_pool.start(worker)

    def _batch_render_frame(self, job) -> np.ndarray:
        """Load + full-precision render of one roll frame with ITS OWN look.
        Mirrors load_raw_image / load_input_image processing and the
        full-precision branch of _render_export; runs on the worker thread
        (all callees are pure / thread-safe, a dedicated simulator per frame)."""
        state = job.state
        if job.kind == 'raw':
            image = load_and_process_raw_file(
                job.path,
                white_balance=state.load_raw.white_balance,
                temperature=state.load_raw.temperature,
                tint=state.load_raw.tint,
                lens_correction=state.load_raw.lens_correction,
                output_colorspace=state.input_image.input_color_space,
                output_cctf_encoding=state.input_image.apply_cctf_decoding,
                recover=recover_settings(state, job.path),
            )
        else:
            image = load_image_oiio(job.path)[..., :3]
            image = apply_rgb_white_balance(
                image,
                white_balance=state.load_rgb.white_balance,
                temperature=state.load_rgb.temperature,
                tint=state.load_rgb.tint,
                color_space=state.input_image.input_color_space,
                cctf_encoded=state.input_image.apply_cctf_decoding,
            )
        params = configure_export_params(
            build_params_from_state(state),
            saving_color_space=job.saving_color_space,
            saving_cctf_encoding=job.saving_cctf_encoding,
            full_precision=True,
        )
        # v1.1.1 fix: the FRAME's own path, not _current_input_path — each
        # roll frame keeps its own grain field, same as when it is on screen.
        self._stamp_auto_grain_seed(params, path=job.path)
        # Shared render lock: the wgpu device is single-threaded.
        with runtime.PIPELINE_RENDER_LOCK:
            # False: each roll frame exports the look stored for it, not the
            # stock constants (see _render_export).
            digested_params = digest_params(params, apply_stocks_specifics=False)
            simulator = runtime_simulator(digested_params)
            return np.asarray(simulator.process(np.double(image)))

    def _batch_write_frame(self, job, buffer: np.ndarray, out_dir: str) -> str:
        target = runtime.unique_export_path(out_dir, Path(job.name).stem, job.ext)
        image_data = np.asarray(buffer)[..., :3]
        # 2026-10-10; see design/DECISIONS.md#d-controller-963
        paper = paper_of_look(job.state)
        if paper is not None:
            image_data = self.apply_paper_finish_to_export(image_data, paper=paper, padding=False)
        if job.ext == 'dng':
            from spektrafilm_gui.linear_dng import write_linear_dng

            write_linear_dng(str(target), image_data, custom_tags=job.custom_tags)
            return target.name
        save_image_oiio(
            str(target),
            image_data,
            bit_depth=job.bit_depth,
            color_space=job.saving_color_space,
            cctf_encoding=job.saving_cctf_encoding,
            jpeg_quality=job.jpeg_quality,
        )
        try:
            write_image_metadata(
                str(target),
                drop_source_icc_profile(read_image_metadata(job.path)),
                saving_color_space=job.saving_color_space,
                saving_cctf_encoding=job.saving_cctf_encoding,
                custom_tags=job.custom_tags,
            )
        except Exception:  # metadata is non-fatal; pixels + ICC are written
            pass
        return target.name

    _scanner_popover = None

    _scanner_panel = None

    _lut_popover = None
    _lut_dialog = None

    def _render_export(self, request: ExportRequest) -> np.ndarray:
        if request.full_precision:
            # A dedicated simulator isolates the export from the interactive
            # self._runtime_simulator (different LUT/precision settings, separate
            # thread). configure_export_params already set the output to the
            # saving space + encoding, so the buffer needs no further colour
            # conversion.
            # apply_stocks_specifics=False, like the live path: the params were
            # built from the GUI state, which already holds the stock's
            # selection defaults PLUS the user's tuning. Digesting with True
            # here re-applied the raw stock constants over the tuned values --
            # the red/cold export against a warm preview.
            digested_params = digest_params(request.params, apply_stocks_specifics=False)
            simulator = runtime_simulator(digested_params)
            # A SEPARATE SIMULATOR IS NOT A SEPARATE DEVICE. This runs on the
            # export worker while interactive renders run on theirs, and the
            # one wgpu device was being submitted to from both -- the
            # condition the lock exists for. Unheld here, an export started
            # during a preview could lose the device mid-submit, which
            # aborts the process rather than raising (NAT 2026-08-03:
            # 'wgpuQueueSubmit ... Parent device is lost').
            with runtime.PIPELINE_RENDER_LOCK:
                return np.asarray(simulator.process(request.image))

        # Fast path: reuse the buffer from the last Scan/Preview (no recompute);
        # only convert it from its render space/encoding to the saving choice.
        buffer = np.asarray(request.prerendered)[..., :3]
        if (
            request.source_color_space != request.saving_color_space
            or request.source_cctf_encoding != request.saving_cctf_encoding
        ):
            buffer = colour.RGB_to_RGB(
                buffer,
                request.source_color_space,
                request.saving_color_space,
                apply_cctf_decoding=request.source_cctf_encoding,
                apply_cctf_encoding=request.saving_cctf_encoding,
            )
        return np.asarray(buffer)

    def _write_export(self, request: ExportRequest, buffer: np.ndarray) -> str:
        # The render already produced pixels in the saving colour space + encoding
        # (configure_export_params set io.output_color_space / output_cctf_encoding),
        # so there is no colour conversion here -- only bit-depth encoding + ICC.
        image_data = np.asarray(buffer)[..., :3]
        # 2026-07-26; see design/DECISIONS.md#d-controller-285
        image_data = self.apply_paper_finish_to_export(image_data)
        if str(request.filepath).lower().endswith('.dng'):
            from spektrafilm_gui.linear_dng import write_linear_dng

            info = write_linear_dng(request.filepath, image_data,
                                    custom_tags=getattr(request, 'custom_tags', None))
            headroom = info['headroom_stops']
            return (f'Exported {request.filepath} (linear DNG, BT.2020'
                    + (f', {headroom} stop headroom)' if headroom else ')'))
        save_image_oiio(
            request.filepath,
            image_data,
            bit_depth=request.bit_depth,
            color_space=request.saving_color_space,
            cctf_encoding=request.saving_cctf_encoding,
            jpeg_quality=getattr(request, 'jpeg_quality', None),
        )
        metadata_error = None
        try:
            write_image_metadata(
                request.filepath,
                request.source_metadata,
                saving_color_space=request.saving_color_space,
                saving_cctf_encoding=request.saving_cctf_encoding,
                custom_tags=getattr(request, 'custom_tags', None),
            )
        except Exception as exc:  # metadata is non-fatal; pixels + ICC are written
            metadata_error = exc
        if metadata_error is not None:
            return f'Exported {request.filepath} (metadata copy failed: {metadata_error})'
        return f'Exported {request.filepath}'

    def save_current_as_default(self) -> None:
        persistence_actions.save_current_as_default(
            viewer=self._viewer,
            widgets=self._widgets,
            collect_gui_state_fn=collect_gui_state,
            save_default_gui_state_fn=save_default_gui_state,
            set_status_fn=set_status,
            dialog_parent_fn=dialog_parent,
            message_box=notices,
        )

    def save_current_state_to_file(self) -> None:
        persistence_actions.save_current_state_to_file(
            viewer=self._viewer,
            widgets=self._widgets,
            file_dialog=_DirMemoryDialog('gui_state'),
            collect_gui_state_fn=collect_gui_state,
            save_gui_state_to_path_fn=save_gui_state_to_path,
            set_status_fn=set_status,
            dialog_parent_fn=dialog_parent,
            message_box=notices,
        )

    def load_state_from_file(self) -> None:
        persistence_actions.load_state_from_file(
            viewer=self._viewer,
            widgets=self._widgets,
            file_dialog=_DirMemoryDialog('gui_state'),
            load_gui_state_from_path_fn=load_gui_state_from_path,
            apply_gui_state_fn=apply_gui_state,
            sync_canvas_background_fn=self._sync_canvas_background,
            set_status_fn=set_status,
            dialog_parent_fn=dialog_parent,
            message_box=notices,
        )
        self._warn_if_missing_custom_stock()

    # ------------------------------------------------------------------
    # Phase 14: preset browser (.sfpreset dropdown over the user dir)
    # ------------------------------------------------------------------
    def restore_factory_default(self) -> None:
        persistence_actions.restore_factory_default(
            viewer=self._viewer,
            widgets=self._widgets,
            project_default_gui_state=PROJECT_DEFAULT_GUI_STATE,
            clear_saved_default_gui_state_fn=clear_saved_default_gui_state,
            apply_gui_state_fn=apply_gui_state,
            sync_canvas_background_fn=self._sync_canvas_background,
            set_status_fn=set_status,
            dialog_parent_fn=dialog_parent,
            message_box=notices,
        )

    def _preview_input_layer(self) -> Any | None:
        return self._layers.preview_input_layer()

    def _white_border_layer(self) -> Any | None:
        return self._layers.white_border_layer()

    # Phase 6A: optional wgpu canvas mirror (flagged; napari stays authoritative
    # until 6C). The canvas shows whatever the output/preview funnel shows.
    _gpu_canvas = None

    #: Remembers that the 'could not detect your GPU memory' line has been
    #: said. ONCE PER INSTALL, not once per launch.
    GPU_MEMORY_NOTICE_KEY = 'gpu/vram_notice_shown'

    _last_canvas_image = None
    # Ctrl+R before/after (NAT): flip the canvas between the cached input
    # DISPLAY and the last render, without re-running the pipeline.
    _input_display_cached = None
    _output_display_cached = None
    _showing_input = False
    # 2026-07-25, 2026-07-26, 2026-09-05; see design/DECISIONS.md#d-controller-287
    _input_mode = False
    _input_mode_navigated = False
    _input_mode_button = None
    #: set by app.py to the window's `set_side_panels_visible` (see below)
    _set_side_panels = None
    _set_right_panel_locked = None
    #: set by app.py to the window's TopBar (the star, the view segment)
    _top_bar = None

    _last_canvas_is_output = False

    _peeking = False

    # 2026-07-26; see design/DECISIONS.md#d-controller-291
    _paper_texture_cache: dict | None = None

    def _set_or_add_output_layer(
        self,
        image: np.ndarray,
        *,
        float_image: np.ndarray,
        output_color_space: str,
        output_cctf_encoding: bool,
        use_display_transform: bool,
    ) -> None:
        self._layers.set_or_add_output_layer(
            image,
            float_image=float_image,
            output_color_space=output_color_space,
            output_cctf_encoding=output_cctf_encoding,
            use_display_transform=use_display_transform,
            output_interpolation_mode=self._output_interpolation_mode(),
        )
        self._mirror_to_gpu_canvas(image, is_output=True)

    def _set_or_add_input_stack(
        self,
        image: np.ndarray,
    ) -> None:
        self._update_preview_cache(
            image,
            home_input_stack=True,
            hide_output=True,
        )
        self._update_banner_and_metadata(image)

    def _update_banner_and_metadata(self, image: np.ndarray) -> None:
        banner = getattr(self._widgets, 'banner', None)
        if banner is not None:
            name = (os.path.basename(self._display_path(self._current_input_path))
                    if self._current_input_path else '')
            arr = np.asarray(image)
            height = int(arr.shape[0]) if arr.ndim >= 2 else 0
            width = int(arr.shape[1]) if arr.ndim >= 2 else 0
            banner.set_image_info(name, width, height)
        metadata = None
        if self._current_input_path is not None:
            try:
                metadata = read_image_metadata(self._current_input_path)
            except Exception:
                metadata = None
        metadata_panel = getattr(self._widgets, 'metadata', None)
        if metadata_panel is not None and metadata is not None:
            try:
                read_camera_lens = import_module('spektrafilm.utils.io').read_camera_lens_model
                camera, lens = read_camera_lens(metadata)
                metadata_panel.set_camera_lens(camera, lens)
            except Exception:
                pass
        self._update_file_facts(image, metadata)

    @staticmethod
    def _display_path(path: str) -> str:
        """The file as it is spelled on disk. The roll keeps paths normcased
        (lower case on Windows), which is right for matching and wrong for
        showing (QA 2026-10-07: 'c/users/.../test-image.raf')."""
        try:
            return str(Path(path).resolve())
        except OSError:
            return path

    def _update_file_facts(self, image, metadata) -> None:
        """The File section and the viewer's top-left corner, for the frame
        just loaded (QA 2026-10-07: the section said 'No frame loaded'
        whatever was open -- nothing ever fed it)."""

        path = self._current_input_path
        line, tooltip = camera_settings(metadata)
        banner = getattr(self._widgets, 'banner', None)
        set_camera = getattr(banner, 'set_camera_info', None)
        if callable(set_camera):
            set_camera(line, tooltip)
        reco = getattr(getattr(self, '_left_panel', None), 'recover_view', None)
        if reco is not None:
            (camera_text, camera_source), (lens_text, lens_source) = recover_facts(metadata)
            reco.set_camera_fact(camera_text, camera_source)
            reco.set_lens_fact(lens_text, lens_source)
        self._on_recover_frame_loaded()
        section = self._file_section()
        if section is None:
            return
        if not path:
            section.set_frame('', '')
            section.set_info()
            return
        arr = np.asarray(image)
        dims = (f'{int(arr.shape[1])} \u00d7 {int(arr.shape[0])} px'
                if arr.ndim >= 2 else '\u2014')
        extension = os.path.splitext(path)[1].lstrip('.').upper() or '\u2014'
        make = ''
        for datum in (getattr(metadata, 'exif', None) or ()):
            try:
                if datum.key() == 'Exif.Image.Make':
                    make = display_make(str(datum.toString()))
                    break
            except Exception:
                continue
        file_format = f'{make.split()[0]} {extension}' if make and self._current_input_kind == 'raw' else extension

        shown = self._display_path(path)
        section.set_frame(os.path.basename(shown), shown.replace(os.sep, '/'))
        section.set_info(dims, file_format, render_backend_text())

    def _update_preview_cache(
        self,
        image: np.ndarray,
        *,
        home_input_stack: bool,
        hide_output: bool,
    ) -> None:
        state = collect_gui_state(widgets=self._widgets)
        preview_base = self._resize_for_preview(image, max_size=state.display.preview_max_size)
        # Phase 17A: the stored image is the un-balanced base; the preview
        # (and everything downstream of it) carries the live white balance.
        preview_image = self._white_balanced_input(preview_base, state=state)
        preview_display_image = self._prepare_input_color_preview_image(
            preview_image,
            input_color_space=state.input_image.input_color_space,
            apply_cctf_decoding=state.input_image.apply_cctf_decoding,
        )
        self._current_input_image = image
        self._current_preview_base = preview_base
        self._current_preview_image = preview_image
        self._preview_wb_signature = self._wb_signature(state)
        # 2026-07-25; see design/DECISIONS.md#d-controller-293
        self._input_display_cached = preview_display_image
        # Phase 16C: a new preview image invalidates the scan-stage cache.
        self._preview_image_version += 1
        self._bump_autoexposure_token()
        self._stage_cache = None
        self._layers.set_or_add_input_preview_layer(
            preview_display_image,
            watermark_source_size=tuple(int(dimension) for dimension in image.shape[:2]),
            white_padding=state.display.white_padding,
            hide_output=hide_output,
            set_active=home_input_stack or self._output_layer() is None,
        )
        if hide_output:
            self._mirror_to_gpu_canvas(preview_display_image)
        if home_input_stack:
            self._home_input_stack()

    def _sync_white_border(self, *, white_padding: float) -> None:
        self._layers.sync_white_border(white_padding=white_padding)

    def _home_input_stack(self) -> None:
        if self._white_border_layer() is None:
            return
        reset_viewer_camera(self._viewer)
        self._set_active_layer(self._white_border_layer())
        # Versions are image-bound: show the freshly loaded image's history.
        self._refresh_attempts_view()

    # ------------------------------------------------- Phase 17A live WB
    def _wb_signature(self, state) -> tuple | None:
        """The white-balance inputs baked into the derived preview, used to
        detect a live change. None for baked inputs (identity tail)."""
        kind = self._current_input_kind
        if kind is None:
            return None
        if kind == 'raw':
            section = state.load_raw
        else:
            section = state.load_rgb
        return (
            kind,
            section.white_balance,
            section.temperature,
            section.tint,
            state.input_image.input_color_space,
            state.input_image.apply_cctf_decoding,
        )

    def _white_balanced_input(self, image, *, state=None):
        """Apply the live white balance to an un-balanced base (any size).

        Identity for baked inputs (kind None) and for RAW as_shot (a
        decode-time property: the stored base stays visible until the
        scheduled reprocess lands). Returns the input object untouched in
        the identity cases, so legacy/test paths are bit-exact."""
        kind = self._current_input_kind
        if kind is None or image is None:
            return image
        if state is None:
            state = collect_gui_state(widgets=self._widgets)
        try:
            if kind == 'raw':
                if state.load_raw.white_balance == 'as_shot':
                    return image
                return apply_raw_white_balance(
                    image,
                    white_balance=state.load_raw.white_balance,
                    temperature=state.load_raw.temperature,
                    tint=state.load_raw.tint,
                    output_colorspace=state.input_image.input_color_space,
                    output_cctf_encoding=state.input_image.apply_cctf_decoding,
                )
            return apply_rgb_white_balance(
                image,
                white_balance=state.load_rgb.white_balance,
                temperature=state.load_rgb.temperature,
                tint=state.load_rgb.tint,
                color_space=state.input_image.input_color_space,
                cctf_encoded=state.input_image.apply_cctf_decoding,
            )
        except (OSError, ValueError) as exc:
            set_status(self._viewer, f'White balance not applied: {exc}')
            return image

    def _ensure_preview_white_balance(self) -> None:
        """Re-derive the balanced preview from the stored preview base when
        the white-balance inputs changed since it was last derived. Called
        lazily from the render input path, so every preview render (incl.
        the 16B drag stream) picks up live WB with no extra wiring."""
        if self._current_input_kind is None or self._current_preview_base is None:
            return
        state = collect_gui_state(widgets=self._widgets)
        signature = self._wb_signature(state)
        if signature == self._preview_wb_signature:
            return
        preview_image = self._white_balanced_input(
            self._current_preview_base, state=state)
        self._current_preview_image = preview_image
        self._preview_wb_signature = signature
        # A new preview input invalidates the 16C stage cache (the cache key
        # would flip too — load_raw/load_rgb are outside both exclusion
        # sets — but the version bump keeps the invariant explicit).
        self._preview_image_version += 1
        self._bump_autoexposure_token()
        self._stage_cache = None
        preview_display_image = self._prepare_input_color_preview_image(
            preview_image,
            input_color_space=state.input_image.input_color_space,
            apply_cctf_decoding=state.input_image.apply_cctf_decoding,
        )
        self._input_display_cached = preview_display_image
        self._layers.set_or_add_input_preview_layer(
            preview_display_image,
            watermark_source_size=tuple(
                int(dimension)
                for dimension in np.asarray(self._current_input_image).shape[:2]
            ) if self._current_input_image is not None else preview_display_image.shape[:2],
            white_padding=state.display.white_padding,
            hide_output=False,
            set_active=False,
        )
        if self._showing_input or self._input_mode:
            self._mirror_to_gpu_canvas(preview_display_image)

    def _simulation_input_image(self, *, source_layer_name: str) -> np.ndarray | None:
        if source_layer_name == INPUT_PREVIEW_LAYER_NAME:
            self._ensure_preview_white_balance()
            return self._current_preview_image
        if source_layer_name == INPUT_LAYER_NAME:
            # Full-res renders (Scan/export) balance the base transiently:
            # the result is streamed into the render, never stored, so the
            # one-full-res-buffer memory rule holds.
            return self._white_balanced_input(self._current_input_image)
        return None

    def _auto_preview_enabled(self) -> bool:
        # 2026-07-18; see design/DECISIONS.md#d-controller-294
        if not self._first_render_done:
            return False
        simulation_section = getattr(self._widgets, 'simulation', None)
        auto_preview_value = getattr(simulation_section, 'auto_preview_value', None)
        return bool(auto_preview_value()) if callable(auto_preview_value) else False

    def _run_scheduled_auto_preview(self) -> None:
        self._auto_preview_scheduled = False
        if not self._auto_preview_enabled() or self._current_preview_image is None:
            self._pending_auto_preview = False
            return
        if self._active_simulation_worker is not None or self._active_export_worker is not None:
            self._pending_auto_preview = True
            return
        self._run_preview(report_status=False)

    def _replay_pending_auto_preview(self) -> None:
        if self._pending_live_render:
            # Editor drags that landed while a render was in flight: replay
            # through the live-recipe path (not gated by auto preview).
            self._pending_live_render = False
            self._pending_auto_preview = False
            self._request_live_recipe_render()
            return
        if not self._pending_auto_preview:
            return
        self._pending_auto_preview = False
        self.request_auto_preview()

    def _output_layer(self) -> Any | None:
        return self._layers.output_layer()

    def _set_active_layer(self, layer: Any | None) -> None:
        self._layers.set_active_layer(layer)

    def _output_layer_float_data(self) -> np.ndarray | None:
        output_layer = self._output_layer()
        if output_layer is None:
            return None
        float_data = output_layer.metadata.get(OUTPUT_FLOAT_DATA_KEY)
        if float_data is None:
            return None
        if hasattr(float_data, 'to_host'):      # lazy GPU result: read back now
            return np.asarray(float_data, dtype=np.float32)
        return np.asarray(float_data)

    def _output_layer_render_settings(
        self,
        *,
        default_color_space: str,
        default_cctf_encoding: bool,
    ) -> tuple[str, bool]:
        output_layer = self._output_layer()
        if output_layer is None:
            return default_color_space, default_cctf_encoding
        color_space = output_layer.metadata.get(OUTPUT_COLOR_SPACE_KEY, default_color_space)
        cctf_encoding = output_layer.metadata.get(OUTPUT_CCTF_ENCODING_KEY, default_cctf_encoding)
        return str(color_space), bool(cctf_encoding)

    def _output_interpolation_mode(self) -> str:
        display_section = getattr(self._widgets, 'display', None)
        editor = getattr(display_section, 'output_interpolation', None)
        value = getattr(editor, 'value', None)
        if isinstance(value, str) and value:
            return value
        current_text = getattr(editor, 'currentText', None)
        if callable(current_text):
            text = current_text()
            if isinstance(text, str) and text:
                return text
        return 'spline36'

    @staticmethod
    def _resize_for_preview(image_data: np.ndarray, *, max_size: int) -> np.ndarray:
        return resize_for_preview(image_data, max_size)

    @staticmethod
    def _prepare_input_color_preview_image(
        image_data: np.ndarray,
        *,
        input_color_space: str,
        apply_cctf_decoding: bool,
    ) -> np.ndarray:
        return runtime.prepare_input_color_preview_image(
            image_data,
            input_color_space=input_color_space,
            apply_cctf_decoding=apply_cctf_decoding,
            colour_module=colour,
        )

    @staticmethod
    def _prepare_output_display_image(
        image_data: np.ndarray,
        *,
        output_color_space: str,
        use_display_transform: bool,
        padding_pixels: float = 0.0,
    ) -> tuple[np.ndarray, str]:
        return runtime.prepare_output_display_image(
            image_data,
            output_color_space=output_color_space,
            use_display_transform=use_display_transform,
            padding_pixels=padding_pixels,
            imagecms_module=ImageCms,
            colour_module=colour,
            pil_image_module=PILImage,
        )

    # Phase 16C partial re-render — the two proven-byte-identical cache tiers.
    # SCAN: only scan_finishing changed -> re-run just the scan stage from the
    # cached pre-scan tap (scan_finishing feeds only that stage). DEVELOP: only
    # develop-and-later sections changed -> re-run develop+print+scan from the
    # cached LOG_E_FILM tap (skipping preprocess + the ~54ms spectral expose).
    # halation is EXCLUDED from develop — it is applied in the EXPOSE stage.
    # Each set is a PARTITION-by-exclusion of the gui state, so any unlisted
    # change flips the key and forces a full render (safe by construction).
    _SCAN_ONLY_SECTIONS = ('scan_finishing',)
    _DEVELOP_AND_LATER_SECTIONS = (
        'scan_finishing', 'grain', 'couplers', 'chemistry', 'aging')

    @staticmethod
    def _partial_tiers_eligible(state) -> bool:
        """SCAN-tier eligibility. The SCAN tier resumes at pre_scan_tap
        (CMY_FILM or CMY_PRINT), skipping BOTH filming and printing entirely
        -- whichever of those two stages would have populated the scanner
        black/white-correction reference for this workflow route, it's
        always the one skipped. So this stays a blanket gate: whenever
        either correction is on, force a FULL render for the SCAN tier so
        the reference is always computed -- else the scan stage subtracts
        None - None (bug 2026-07-22). Corrections default off, so the
        common case keeps the speedup. See _develop_tier_eligible for the
        narrower DEVELOP-tier case (2026-09-18), which can stay eligible
        for part of this same combination."""
        simulation = getattr(state, 'simulation', None)
        return not (getattr(simulation, 'scan_white_correction', False)
                    or getattr(simulation, 'scan_black_correction', False))

    @staticmethod
    def _scan_tier_eligible(state) -> bool:
        """SCAN-tier eligibility since 2026-09-25 (old-plans trace B3,
        "Option A" of the 09-18 scoping): always.

        The SCAN tier used to decline whenever a scanner b/w correction was on
        (`_partial_tiers_eligible`), because the references the correction
        reads are computed as a side effect of filming or printing -- both of
        which the tier skips -- and the rebuilt service held None (the
        None - None crash, 2026-07-22). `_run_with_stage_cache` now caches
        them with the pre-scan tap and restores them before resuming, so the
        tier is valid with a correction on. Valid because the scan key covers
        every field except scan_finishing: a tap and its references are only
        reused for the upstream state that produced them.

        The DEVELOP tier does NOT get this: it re-bakes the film curves (aging,
        chemistry), and on the scan-film route the references come from those
        very curves, so a restored copy could be stale. It keeps
        `_develop_tier_eligible`."""
        del state
        return True

    def _develop_tier_eligible(self, state) -> bool:
        """DEVELOP-tier eligibility (2026-09-18). The DEVELOP tier resumes
        at LOG_E_FILM, skipping preprocess + filming.expose but re-running
        filming.develop -> printing (in full, when the workflow prints) ->
        scanning every time. The scanner b/w-correction reference is
        populated inside filming.expose (scan-film + POSITIVE film route)
        OR printing.expose (print route + NEGATIVE print) --
        printing.expose is NOT skipped here, so the print route stays safe
        even with a correction on; only the scan-film route is not (its
        populating stage, filming.expose, IS what this tier skips).

        Reads the PREVIOUS render's film/print types off
        `self._runtime_simulator` rather than digesting `state` again --
        safe because a film/print STOCK change is not in
        `_DEVELOP_AND_LATER_SECTIONS`'s exclusion set, so it already flips
        `develop_key` and forces a full render (which rebuilds the
        simulator with the new stock) independently of this check. Falls
        back to the conservative `_partial_tiers_eligible` answer whenever
        the route isn't known yet (no previous render)."""
        if self._partial_tiers_eligible(state) or self._scanner_correction_is_noop(state):
            return True
        simulation = getattr(state, 'simulation', None)
        sim = getattr(self, '_runtime_simulator', None)
        get_types = getattr(sim, 'workflow_film_print_types', None)
        if get_types is None:
            return False
        film_type, print_type = get_types()
        if not getattr(simulation, 'scan_film', True):
            return print_type == 'negative'
        return False   # scan-film route, positive film: filming.expose is what's skipped

    def _scanner_correction_is_noop(self, state) -> bool:
        """True when a scanner b/w correction is switched on but can't do
        anything: a scan-film route with a NEGATIVE film. ColorReferenceService
        early-returns for exactly that case at every entry point --
        `black_white_xyz_correction` passes xyz through, the reference update
        and `black_white_scan_correction_mq` return before reading
        `_y_black`/`_y_white`, and the filming exposure correction returns
        1.0 -- so no reference is ever read, and BOTH partial tiers are as safe
        as with the corrections off. Same previous-render film type as
        `_develop_tier_eligible` (a stock or route change flips both cache
        keys on its own); False when unknown."""
        simulation = getattr(state, 'simulation', None)
        if not getattr(simulation, 'scan_film', False):
            return False
        sim = getattr(self, '_runtime_simulator', None)
        get_types = getattr(sim, 'workflow_film_print_types', None)
        if get_types is None:
            return False
        film_type, _ = get_types()
        return film_type == 'negative'

    def _tier_keys(self, state, rendered_long_edge: int):
        """The (scan_key, develop_key) a preview render hands
        `_run_with_stage_cache`, each replaced by a fresh sentinel when its
        tier is ineligible (see the comment at the call site in
        `_start_simulation`); None when neither tier is.

        2026-09-25 (old-plans trace B3): the SCAN tier restores the cached
        scanner b/w references (`_scan_tier_eligible`), so a correction no
        longer costs it, and the DEVELOP tier keeps its own, narrower rule --
        which is why the develop key now needs a sentinel of its own:
        `scan_ok` no longer implies `develop_ok`."""
        scan_ok = self._scan_tier_eligible(state)
        develop_ok = self._develop_tier_eligible(state)
        if not (scan_ok or develop_ok):
            return None
        scan_key, develop_key = self._stage_cache_keys(state, rendered_long_edge)
        return (scan_key if scan_ok else object(),
                develop_key if develop_ok else object())

    @classmethod
    def _stage_cache_keys(cls, state, rendered_long_edge: int) -> tuple:
        """Return (scan_key, develop_key). A key changes whenever ANYTHING
        outside its section set does, so a match proves the corresponding tap
        (pre-scan / LOG_E_FILM) is byte-valid to reuse."""
        import json


        payload = gui_state_to_dict(state)

        def signature(exclude):
            kept = {k: v for k, v in payload.items() if k not in exclude}
            return (rendered_long_edge,
                    json.dumps(kept, sort_keys=True, default=str))

        return (signature(cls._SCAN_ONLY_SECTIONS),
                signature(cls._DEVELOP_AND_LATER_SECTIONS))

    def _process_image_with_runtime(self, image_data: np.ndarray, params) -> np.ndarray:
        import time as _time

        timing = bool(os.environ.get('SPEKTRAFILM_TIMING'))
        # Only the live custom-stock editor asks for the stock constants (its
        # look overrides land at digest time). The old `simulator is None` arm
        # made a session's FIRST render stomp the freshly loaded tuned state
        # with stock constants, which then snapped to the tuned look on the
        # next incremental render -- the same defect as the export paths, one
        # frame long.
        apply_stocks_specifics = self._next_runtime_digest_applies_stock_specifics
        t0 = _time.perf_counter()
        digested_params = digest_params(
            params,
            apply_stocks_specifics=apply_stocks_specifics,
        )
        t1 = _time.perf_counter()
        try:
            # Stash the active film's characteristic curves (thread-safe numpy
            # copies) for the Analysis tab; applied on the GUI thread when the
            # render finishes.
            film_data = digested_params.film.data
            self._last_film_curves = (
                np.asarray(film_data.log_exposure).copy(),
                np.asarray(film_data.density_curves).copy(),
            )
        except Exception:
            self._last_film_curves = None
        cache_key = self._pending_stage_cache_keys
        self._pending_stage_cache_keys = None    # consume once
        try:
            built = self._runtime_simulator is None
            if built:
                self._runtime_simulator = runtime_simulator(digested_params)
                self._stage_cache = None        # fresh pipeline: drop stale cache
            else:
                self._runtime_simulator.update_params(digested_params)
            self._next_runtime_digest_applies_stock_specifics = False
            # 2026-09-20; see design/DECISIONS.md#d-controller-295
            try:
                self._last_coupler_margin = (
                    self._runtime_simulator.coupler_inversion_margin())
            except Exception:
                self._last_coupler_margin = None
            t2 = _time.perf_counter()
            # 2026-09-19; see design/DECISIONS.md#d-controller-296
            set_device_result = getattr(self._runtime_simulator, 'set_device_result', None)
            if callable(set_device_result):
                set_device_result(cache_key is not None)
            result, mode = self._run_with_stage_cache(image_data, cache_key)
            t3 = _time.perf_counter()
            if cache_key is not None:
                self._last_render_stats = (
                    1000.0 * (t3 - t0),
                    image_data.shape[0] * image_data.shape[1] / 1e6,
                    self._render_backend_name(), mode)
        except Exception:
            self._runtime_simulator = None
            self._stage_cache = None
            raise
        if timing:
            try:
                stage = self._runtime_simulator.format_timings()
            except Exception:
                stage = '(stage timings unavailable)'
            setup = 'build' if built else 'update'
            print(
                f"[spektrafilm-timing] digest={t1 - t0:.2f}s {setup}(LUT)={t2 - t1:.2f}s "
                f"process[{mode}]={t3 - t2:.2f}s\n{stage}",
                flush=True,
            )
        return result

    @staticmethod
    def _render_backend_name() -> str:
        """'GPU' or 'CPU' for the Config panel's 'Last render' row: the
        backend the engine had to hand, not a per-kernel account (the
        'Resident' row above it says which chains ran on the device)."""
        env = os.environ.get('SPEKTRAFILM_GPU', '')
        if env.strip().lower() in ('0', 'cpu', 'false', 'off', 'no'):
            return 'CPU'
        try:
            from spektrafilm.gpu.device import GPUDevice
            return 'GPU' if GPUDevice.get().is_available else 'CPU'
        except Exception:
            return 'CPU'

    def _show_last_render(self) -> None:
        stats = getattr(self, '_last_render_stats', None)
        if stats is None:
            return
        self._last_render_stats = None       # each render reports once
        try:
            from spektrafilm_gui.left_panel_config import format_last_render, show_last_render
            show_last_render(*format_last_render(*stats))
        except Exception:
            pass                             # a readout never fails a render

    def _run_with_stage_cache(self, image_data, keys):
        """Phase 16C: return (result, mode). Reuse the latest byte-valid tap:
        the pre-scan tap when only scan_finishing changed, else the LOG_E_FILM
        tap when only develop-and-later sections changed, else a full render
        (split at both taps to refill the cache for free). Every partial path
        is byte-identical to a cold full render (test-pinned)."""

        sim = self._runtime_simulator
        if keys is None:
            # Exports / scans / live-recipe: plain end-to-end, no caching.
            return sim.process(image_data), 'full'

        scan_key, develop_key = keys
        pre_tap = sim.pre_scan_tap()
        cache = self._stage_cache
        res = image_data.shape[:2]

        # 1. SCAN tier: only scan_finishing changed -> re-run just the scan
        #    stage from the cached pre-scan densities.
        if (cache is not None and cache.get('scan_key') == scan_key
                and cache.get('pre_tap') == pre_tap
                and cache.get('pre') is not None
                and cache['pre'].shape[:2] == res):
            # The scanner b/w-correction references the full render computed
            # (None when both corrections are off): the stage that computes
            # them is skipped here, and update_params rebuilt the service
            # empty. See _scan_tier_eligible.
            if cache.get('scan_refs') is not None:
                sim.restore_scan_references(cache['scan_refs'])
            # Arm the DEVELOP tier late when it has just become eligible (the
            # route is unknown on a session's first render, so that render
            # stored a sentinel). The cached LOG_E_FILM tap is already right:
            # a scan-key match means nothing but scan_finishing changed since
            # it was collected, so its develop key is this one.
            if (isinstance(develop_key, tuple)
                    and not isinstance(cache.get('develop_key'), tuple)):
                cache['develop_key'] = develop_key
            return sim.process(cache['pre'], inject=pre_tap), 'scan'

        # 2. DEVELOP tier: only develop-and-later sections changed -> re-run
        #    develop+print+scan from the cached LOG_E_FILM, skipping the
        #    expensive preprocess + spectral expose. Restore the one preprocess
        #    side effect (pixel_size_um) the develop stage reads.
        if (cache is not None and cache.get('develop_key') == develop_key
                and cache.get('log_e_film') is not None
                and cache['log_e_film'].shape[:2] == res
                and cache.get('pixel_size_um') is not None):
            sim.restore_pixel_size_um(cache['pixel_size_um'])
            pre = sim.process(cache['log_e_film'], inject=Tap.LOG_E_FILM,
                              collect=pre_tap)
            result = sim.process(pre, inject=pre_tap)
            # refresh the scan tier under the (now-current) scan_key -- and
            # its references, which this render's printing just recomputed
            self._stage_cache = {**cache, 'pre': pre, 'pre_tap': pre_tap,
                                 'scan_key': scan_key,
                                 'scan_refs': self._scan_references(sim)}
            return result, 'develop'

        # 3. FULL render, split at BOTH taps so both tiers refill for free.
        log_e = sim.process(image_data, collect=Tap.LOG_E_FILM)
        pixel_size_um = sim.pixel_size_um()
        pre = sim.process(log_e, inject=Tap.LOG_E_FILM, collect=pre_tap)
        result = sim.process(pre, inject=pre_tap)
        self._stage_cache = {
            'scan_key': scan_key, 'develop_key': develop_key,
            'log_e_film': log_e, 'pixel_size_um': pixel_size_um,
            'pre': pre, 'pre_tap': pre_tap,
            'scan_refs': self._scan_references(sim)}
        return result, 'full'

    @staticmethod
    def _scan_references(sim):
        """The scanner b/w references to cache with the pre-scan tap, or None
        (both corrections off, or a simulator that cannot say)."""
        get = getattr(sim, 'scan_references', None)
        return get() if callable(get) else None

    def _set_display_transform_checked(self, enabled: bool) -> None:
        display_section = getattr(self._widgets, 'display', None)
        toggle = getattr(display_section, 'use_display_transform', None)
        if toggle is None:
            return

        block_signals = getattr(toggle, 'blockSignals', None)
        set_checked = getattr(toggle, 'setChecked', None)
        if not callable(set_checked):
            return

        previous_block_state = None
        if callable(block_signals):
            previous_block_state = block_signals(True)
        try:
            set_checked(enabled)
        finally:
            if callable(block_signals):
                block_signals(bool(previous_block_state))

    def _sync_canvas_background(self) -> None:
        # 2026-08-04; see design/DECISIONS.md#d-controller-297
        from spektrafilm_gui.shell_layout import resolve_canvas_background

        self.set_canvas_background_color(resolve_canvas_background())

    def _execute_simulation_request(self, request: SimulationRequest) -> SimulationResult:
        return runtime.execute_simulation_request(
            request,
            run_simulation_fn=self._process_image_with_runtime,
            prepare_output_display_image_fn=self._prepare_output_display_image,
        )


    def _grain_seed_for_input(self) -> int:
        """Each photo gets its OWN grain field, derived from which photo it
        is (NAT 2026-07-30: "every new image gets a new seed... but it stays
        reproducible").

        Derived rather than drawn: hashing the source path means the seed
        needs no UI, no state field and nothing saved, yet the same photo
        gets the same negative forever — reopen it in a year, or reload a
        roll, and the grain comes back. A random draw would have to be
        stored in gui_state and persisted into .sfroll/.sfpreset, and a
        roll saved before that existed would come back with the wrong
        grain.

        The PATH, not the pixels: for a RAW the loaded pixels depend on the
        white-balance choice, so a content hash would re-roll the grain
        every time the user touched WB. Renaming a file does change its
        grain; that is the honest trade and it is invisible in practice.

        0 (no image loaded) keeps the historical constants, so every
        default/test render stays exactly what it was.
        """
        return self._derived_grain_seed(getattr(self, '_current_input_path', None))

    @staticmethod
    def _derived_grain_seed(path) -> int:
        """The path-hash itself, callable with ANY path. The batch roll
        export renders frames whose path is NOT `_current_input_path`, so
        the derivation cannot live only on the controller's current photo."""
        if not path:
            return 0

        digest = hashlib.blake2b(str(path).encode('utf-8', 'replace'),
                                 digest_size=4).digest()
        return int.from_bytes(digest, 'big') or 1

    _GRAIN_SEED_SENTINEL = object()

    def _stamp_auto_grain_seed(self, params, *, path=_GRAIN_SEED_SENTINEL) -> None:
        """One photo, one grain field — EVERY path that renders pixels must
        stamp the seed, or the negative it renders is not the one on screen.
        Call sites: _configure_simulation_params (preview, scan, test-strip
        bands), export_output (full-precision export), _batch_render_frame
        (roll export, with the FRAME's own path), and the loupe.

        Only when the user has left the Grain seed control at 0 (AUTO).
        A pinned seed is an explicit choice and must survive: it is saved
        with the roll, so overriding it here would make a reloaded roll
        render a different negative than the one that was saved.
        """
        try:
            if not int(getattr(params.film_render.grain, 'seed', 0) or 0):
                if path is self._GRAIN_SEED_SENTINEL:
                    params.film_render.grain.seed = self._grain_seed_for_input()
                else:
                    params.film_render.grain.seed = self._derived_grain_seed(path)
        except Exception:
            pass

    def _configure_simulation_params(self, params, *, source_layer_name: str,
                                     rendered_image=None):
        self._stamp_auto_grain_seed(params)
        settings = getattr(params, 'settings', None)
        is_preview = source_layer_name == INPUT_PREVIEW_LAYER_NAME
        if settings is not None and hasattr(settings, 'preview_mode'):
            settings.preview_mode = is_preview
        # 2026-07-22; see design/DECISIONS.md#d-controller-298
        if source_layer_name == INPUT_PREVIEW_LAYER_NAME:
            try:
                # Shape-only use: read the stored base directly (Phase 17A —
                # white balance is per-pixel, so the balanced image has the
                # same shape and materializing it here would be pure waste).
                full = self._current_input_image
                rendered = rendered_image if rendered_image is not None else \
                    self._simulation_input_image(source_layer_name=INPUT_PREVIEW_LAYER_NAME)
                if full is not None and rendered is not None and rendered.shape[1]:
                    params.film_render.grain.preview_scale = max(
                        1.0, float(full.shape[1]) / float(rendered.shape[1]))
            except Exception:
                pass
            # H-C1: the preview (and each strip band, and every reduced drag
            # frame) boosts its highlights against the WHOLE frame's max.
            if settings is not None:
                settings.boost_frame_probe = self._boost_frame_probe(params)
        return params

    # (key, probe): the full frame's brightest pixels and their coordinates,
    # un-balanced. Found once per IMAGE (a full-frame search, ~0.8 s at 24 MP);
    # the crop narrows them and the live white balance is applied per render,
    # both on these few pixels.
    _boost_probe_cache = None

    def _boost_frame_probe(self, params, *, state=None):
        """H-C1 (NAT 2026-09-24): the whole frame's brightest input pixels for
        a render of less than it, or None.

        None when the boost is off (nothing to normalise) or there is no
        image. Taken from the full-resolution base INSIDE the camera crop --
        a speck the export crops away must not set its max -- and balanced
        with the same live white balance the preview gets, so they reach the
        engine as the preview's own pixels would. See utils/boost_frame_max.
        """
        try:
            if float(params.film_render.halation.boost_ev) == 0.0:
                return None
            base = self._current_input_image
            if base is None:
                return None
            from spektrafilm.utils.boost_frame_max import frame_probe, within

            frame = np.asarray(base)
            key = (self._preview_image_version, id(base))
            cached = self._boost_probe_cache
            if cached is None or cached[0] != key:
                cached = (key, frame_probe(frame))
                self._boost_probe_cache = cached
            io_params = params.io
            height, width = frame.shape[:2]
            if io_params.crop:
                # The rectangle upstream's own crop takes, read off index
                # arrays it slices exactly as it slices the image (broadcast
                # views: no frame-sized allocation).
                from spektrafilm.utils.crop_resize import crop_image

                rows = crop_image(np.broadcast_to(
                    np.arange(height)[:, None, None], (height, width, 1)),
                    center=io_params.crop_center, size=io_params.crop_size)[:, 0, 0]
                cols = crop_image(np.broadcast_to(
                    np.arange(width)[None, :, None], (height, width, 1)),
                    center=io_params.crop_center, size=io_params.crop_size)[0, :, 0]
                if rows.size == 0 or cols.size == 0:
                    return None
                pixels = within(cached[1], (rows[0], rows[-1] + 1),
                                (cols[0], cols[-1] + 1))
            else:
                pixels = cached[1][0]
            if pixels.size == 0:
                return None
            balanced = self._white_balanced_input(pixels[None, :, :], state=state)
            pixels = np.asarray(balanced, dtype=np.float64)[0, :, :3]
            return tuple(map(tuple, pixels.tolist()))
        except Exception:
            return None

    # Phase 16B: drag frames never go below this long-edge (px) — past this
    # the pipeline overhead dominates and the render stops getting faster.
    _DRAG_MIN_LONG_EDGE = 384

    def _on_drag_ended(self) -> None:
        """Phase 16B: the last slider/knob/fader drag ended. If the stream
        rendered reduced-resolution frames, render once at the full preview
        size (coalesced by the scheduler if a frame is still in flight)."""
        if not self._last_preview_reduced:
            return
        self._last_preview_reduced = False
        self.request_auto_preview()

    def _confirm_input_size(self, image_data, params, retry=None) -> bool:
        """Pre-flight memory check; True means proceed with the render.

        On a predicted over-RAM render the user gets the actual numbers and
        three real choices: adopt the largest upscale that fits (default),
        render anyway (arms the engine guard's one-shot override), or cancel.

        2026-10-07; see design/DECISIONS.md#d-controller-316
        """

        # Best-effort UX layer only: the engine guard in crop_and_rescale is
        # the safety net, so an exotic params shape (test doubles, future
        # refactors) must never block a render here.
        io = getattr(params, "io", None)
        if io is None:
            return True
        verdict, info = size_guard.check_input_size(image_data.shape, io)
        # Freeze fix set item 2: hand THIS sample of free memory forward, so
        # the engine-side guard in crop_and_rescale judges the render against
        # the same number rather than taking its own a moment later. Without
        # it a render cleared here could be refused on a worker thread, where
        # there is no way to tell the user why.
        size_guard.carry_available(info.get("available_bytes"))
        if verdict != "refuse":
            return True
        if getattr(self, '_memory_guard_pass_once', False):
            # the card's Render anyway, coming back round: the user owns the
            # outcome, once (the engine guard's bypass is armed here, at the
            # render it is for)
            self._memory_guard_pass_once = False
            size_guard.override_once()
            return True

        need_gb = info["required_bytes"] / 2**30
        have_gb = (info["available_bytes"] or 0) / 2**30
        mp = info["final_pixels"] / 1e6
        fits = max(float(info.get("max_upscale_that_fits", 1.0)), 0.0)
        # Floor to one decimal so the suggestion is guaranteed to fit.
        fits_floored = max(int(fits * 10) / 10.0, 1.0)
        upscale = float(getattr(params.io, "upscale_factor", 1.0) or 1.0)
        possible = bool(info.get("possible_on_this_machine", True))
        total_gb = (info.get("total_bytes") or 0) / 2**30

        card = notices.system_message_sink(dialog_parent(self._viewer))
        if card is not None and retry is not None:
            self._show_memory_guard_card(
                card, retry=retry, need_gb=need_gb, have_gb=have_gb,
                total_gb=total_gb, mp=mp, upscale=upscale,
                fits_floored=fits_floored, possible=possible)
            return False

        # 2026-08-28; see design/DECISIONS.md#d-controller-299
        from spektrafilm_gui.memory_guard_dialog import MemoryGuardDialog

        dialog = MemoryGuardDialog(
            needed_gb=need_gb,
            available_gb=have_gb,
            total_gb=total_gb,
            megapixels=mp,
            upscale=upscale,
            fits=fits,
            fits_floored=fits_floored,
            possible=possible,
            parent=dialog_parent(self._viewer),
        )
        dialog.exec()
        choice = dialog.choice()

        if choice == MemoryGuardDialog.REDUCE:
            # Write through the widget so gui state, undo history and any
            # re-render all see the new value; then this render proceeds with
            # the reduced params too.
            try:
                self._widgets.input_image.upscale_factor.value = fits_floored
            except Exception:
                pass
            params.io.upscale_factor = fits_floored
            return True
        if choice == MemoryGuardDialog.ANYWAY:
            # The engine-side guard in crop_and_rescale would refuse again;
            # arm its one-shot bypass. The user owns the outcome, once.
            size_guard.override_once()
            return True
        return False

    def _show_memory_guard_card(self, card, *, retry, need_gb: float, have_gb: float,
                                total_gb: float, mp: float, upscale: float,
                                fits_floored: float, possible: bool) -> None:
        """The memory guard as the viewer's card (`SYSMSG.membusy` /
        `SYSMSG.memhard` in the 20.2 page): the facts in one line, the gauge
        (what you have on what it needs), the recommendation first, Render
        anyway only where it can succeed, Cancel last."""
        def reduce() -> None:
            try:
                self._widgets.input_image.upscale_factor.value = fits_floored
            except Exception:
                pass
            retry()

        def anyway() -> None:
            self._memory_guard_pass_once = True
            retry()

        actions = []
        if upscale > 1.0 and fits_floored < upscale:
            actions.append(('Reduce upscale to %g×' % fits_floored, reduce))
        facts = f'{need_gb:.1f} GB needed for {mp:.1f} MP at {upscale:.2f}×'
        if possible:
            short = max(need_gb - have_gb, 0.0)
            actions.append(('Render anyway', anyway))
            card.show_message(
                'warn', 'Not enough memory for this render',
                f'{facts}, {have_gb:.1f} GB free: {short:.1f} GB short. Close other apps '
                'and it runs normally. Rendering anyway often succeeds, but if memory '
                'does run out the attempt is lost.',
                actions=actions, close_label='Cancel',
                gauge=have_gb / need_gb if need_gb > 0 else 0.0)
        else:
            card.show_message(
                'neg', 'Too large for this machine',
                f'{facts}, and this machine has {total_gb:.1f} GB in total, so it cannot '
                'fit even with everything else closed. Reduce the upscale or the crop.',
                actions=actions, close_label='Cancel',
                gauge=total_gb / need_gb if need_gb > 0 else 0.0)

    def _bump_autoexposure_token(self) -> None:
        """Tell the engine which image is loaded, so it may memoise the
        auto-exposure EV (utils/autoexposure_cache).

        The EV costs ~300 ms at 24 MP — an anti-aliased downscale of the
        whole frame — and is recomputed on every render of the same photo,
        which is exactly the slider-tweak workflow. The cache is inert
        until this is called, keyed on the frame shape as well as the
        token, and returns the same EV the render would have measured, so
        no rendered pixel moves.

        _preview_image_version is the token: it bumps wherever a new input
        image is prepared, which is conservative (a bump the EV did not
        need only costs one recomputation) and never stale in the direction
        that matters.
        """
        try:
            from spektrafilm.utils import autoexposure_cache

            autoexposure_cache.set_image_token(self._preview_image_version)
        except Exception:
            pass

    def _start_simulation(self, *, source_layer_name: str, mode_label: str, report_status: bool = True) -> None:
        # A no-op once a baseline exists; this is the belt to `__init__`'s
        # braces, for the case where the profiles were not loadable then.
        self._seed_sync_baseline()
        if self._active_simulation_worker is not None:
            set_status(self._viewer, 'Simulation already running')
            return
        if self._active_export_worker is not None:
            set_status(self._viewer, 'Export running; wait before rendering.')
            return
        # A strip that is STILL RENDERING, as opposed to one already shown.
        # `run_test_strip` refuses while a simulation runs; this is the same
        # guard in the other direction, which was missing -- so a preview
        # started during a strip put two workers on the wgpu device at once
        # (tools/race_stress.py aborted the process that way, 2026-09-06).
        # The lock now serialises them regardless; this keeps the two from
        # queueing behind each other in the first place.
        if self._strip_worker is not None:
            set_status(self._viewer,
                       'Test strip rendering; wait before rendering.')
            return
        # Phase 17C: any new render supersedes a shown test strip.
        if self._strip_bands is not None:
            self._dismiss_test_strip(None)

        image_data = self._simulation_input_image(source_layer_name=source_layer_name)
        if image_data is None:
            notices.warn(dialog_parent(self._viewer), 'Run simulation', 'Load an input image before running the simulation.')
            return

        # Phase 16B: a live drag streams reduced-resolution frames; the full
        # preview frame follows on release (via _on_drag_ended). Only the
        # preview path scales down — Scan always renders full.
        from spektrafilm_gui.widget_slider import drag_active
        reduced = False
        # (not while cropping: there an angle turns the kept frame instead)
        if (source_layer_name == INPUT_PREVIEW_LAYER_NAME and drag_active()
                and not self.crop_session_active()):
            long_edge = int(max(image_data.shape[:2]))
            target = int(round(long_edge / self._drag_preview_divisor))
            if self._DRAG_MIN_LONG_EDGE <= target < long_edge:
                image_data = self._resize_for_preview(image_data, max_size=target)
                reduced = True
        self._last_preview_reduced = reduced

        state = collect_gui_state(widgets=self._widgets)
        self._sync_white_border(white_padding=state.display.white_padding)
        params = self._configure_simulation_params(
            build_params_from_state(state),
            source_layer_name=source_layer_name,
            rendered_image=image_data,
        )
        # While cropping, the film is rendered once without the turn and an
        # angle only turns that frame (controller_crop): the turn is the last
        # thing the pipeline does, so nothing before it needs to run again.
        if self._crop_turn_fast_path(state, params, image_data, source_layer_name,
                                     mode_label):
            return
        # Input-size guard, SYNCHRONOUSLY, before a worker is spun up: the
        # engine raises the same ImageTooLargeError from crop_and_rescale as a
        # safety net for every caller, but pre-checking here puts the dialog
        # on screen immediately with the real numbers and lets the user adopt
        # the largest upscale that fits, instead of reading a dead worker's
        # error later. Previews pass trivially (small pixel counts).
        if not self._confirm_input_size(
                image_data, params,
                retry=lambda: self._start_simulation(
                    source_layer_name=source_layer_name, mode_label=mode_label,
                    report_status=report_status)):
            return
        # Phase 16C: the scan-stage fast path is eligible only for PREVIEW
        # renders with no live-recipe override (the recipe isn't in gui
        # state). The key changes whenever ANYTHING but scan_finishing does,
        # so a match proves the pre-scan tap is byte-valid.
        #
        # 2026-09-18: SCAN-tier and DEVELOP-tier eligibility are no longer
        # the same question (see _develop_tier_eligible) -- a scanner b/w
        # correction that's active on the print route (printing.expose
        # still runs on the DEVELOP tier) stays fast there even though the
        # SCAN tier must still decline. A FRESH object() when SCAN is
        # ineligible, NOT None: two consecutive ineligible renders would
        # both cache/compare `None`, and `None == None` is True, so a stale
        # 'pre' tap (whose scan already ran through the OLD, now-stale
        # ColorReferenceService reference) would wrongly look byte-valid
        # and get reused -- exactly the None-None crash this whole
        # eligibility gate exists to prevent. A fresh object() is never
        # equal to any other object (including another render's own fresh
        # sentinel), so `_run_with_stage_cache`'s SCAN-tier equality check
        # can never accidentally match while the DEVELOP-tier check (keyed
        # separately, a real signature) still can.
        # 2026-09-25 (old-plans trace B3): the decision is `_tier_keys`, one
        # method the stage-cache tests call too, so they pin this code rather
        # than a copy of it.
        if (source_layer_name == INPUT_PREVIEW_LAYER_NAME
                and self._live_stock_recipe is None):
            self._pending_stage_cache_keys = self._tier_keys(
                state, int(max(image_data.shape[:2])))
        else:
            self._pending_stage_cache_keys = None
        if self._live_stock_recipe is not None:
            # Phase 10C: the stock editor's UNSAVED recipe replaces the film
            # profile for this render (live preview while handles drag).
            try:
                from spektrafilm.profiles.custom_stocks import bake_recipe_into_profile
                from spektrafilm.profiles.io import load_profile as _load_profile
                params.film = bake_recipe_into_profile(
                    _load_profile(self._live_stock_recipe.base_stock),
                    self._live_stock_recipe,
                )
            except (OSError, ValueError, NotImplementedError):
                set_status(self._viewer, 'Live stock preview failed; rendering the saved stock')

        was_first = not self._first_render_done
        self._first_render_done = True
        if was_first:
            self._schedule_fx_prewarm()
        # The preview hands its (float32) base straight in: the pipeline's
        # _preprocess makes the one owned copy -- float64 on the exact path,
        # the same values np.double gave, or float32 on the GPU preview host
        # path -- so a float64 copy here was a whole frame thrown away.
        # Scan keeps np.double: it renders full resolution, exactly as before.
        if source_layer_name == INPUT_PREVIEW_LAYER_NAME:
            image = np.asarray(image_data)
        else:
            image = np.double(image_data)
        request = SimulationRequest(
            mode_label=mode_label,
            image=image,
            params=params,
            output_color_space=state.simulation.output_color_space,
            use_display_transform=state.display.use_display_transform,
        )

        worker = runtime.SimulationWorker(request, execute_request=self._execute_simulation_request)
        worker.signals.finished.connect(self._on_simulation_finished)
        worker.signals.failed.connect(self._on_simulation_failed)
        self._active_simulation_worker = worker
        self._active_simulation_label = mode_label
        self._active_simulation_reports_status = report_status
        self._set_simulation_controls_enabled(False)
        if report_status:
            set_status(self._viewer, f'Computing {mode_label.lower()}...', timeout_ms=0)
        self._thread_pool.start(worker)

    def _on_simulation_finished(self, result: SimulationResult) -> None:
        result = self._crop_turn_result(result)     # a crop's unturned frame, kept and turned
        report_status = self._active_simulation_reports_status
        self._active_simulation_worker = None
        self._active_simulation_label = None
        self._active_simulation_reports_status = True
        self._set_simulation_controls_enabled(True)
        # A completed render always lands on the 'after' side and leaves the
        # sorting mode (the user asked to see a result, so show it).
        self._showing_input = False
        if self._input_mode:
            self._input_mode = False
            self._sync_input_mode_button()
        self._output_display_cached = result.display_image
        self._set_or_add_output_layer(
            result.display_image,
            float_image=result.float_image,
            output_color_space=result.output_color_space,
            output_cctf_encoding=True,
            use_display_transform=result.use_display_transform,
        )
        # The scopes all decimate to <= 512 px with one rule; a device frame
        # carries exactly that copy (`small`), so they need no readback.
        self._update_analysis(getattr(result.display_image, 'small', result.display_image))
        # Phase 18D: if this frame is the reference, this render IS the
        # reference print. Re-pins it after a roll reload, where the flag
        # survived the save but the pixels did not.
        self._capture_reference()
        if report_status:
            message = f'{result.mode_label} completed. {result.status_message}'
            set_status(self._viewer, message + self._coupler_margin_warning())
            # 2026-10-07; see design/DECISIONS.md#d-controller-300
            QtCore.QTimer.singleShot(0, self._develop_print)
        self._show_last_render()
        # Phase 17B live loop: while the loupe is up, every completed
        # preview cycle re-arms one coalesced full-quality loupe refresh.
        if self._loupe_active and self._loupe_rest_timer is not None:
            self._loupe_rest_timer.start()
        self._replay_pending_auto_preview()

    def _develop_print(self) -> None:
        stage = getattr(self, '_stage_area', None)
        develop = getattr(stage, 'develop', None)
        if callable(develop):
            try:
                develop()
            except RuntimeError:            # torn down with the window
                pass

    def _coupler_margin_warning(self) -> str:
        """What to add to the status line when the DIR-coupler inversion has
        folded on the curves the last render baked (NAT 2026-09-20).

        Empty when the margin is positive, unknown, or the couplers are off
        -- so an ordinary render's status line reads exactly as it did. The
        engine logs the same finding with its full explanation
        (`model/coupler_margin.py`); this is the half the user sees without
        opening the log.
        """
        margin = getattr(self, '_last_coupler_margin', None)
        if margin is None or margin > 0:
            return ''
        return (f'  Development too steep for the DIR couplers '
                f'(margin {margin:+.2f}): colours can band. Reduce push or '
                f'coupler amount.')

    def _custom_metadata_tags(self) -> dict:
        panel = getattr(self._widgets, 'metadata', None)
        getter = getattr(panel, 'custom_tags', None)
        tags = {}
        if callable(getter):
            try:
                tags = dict(getter())
            except Exception:
                tags = {}
        # The CURRENT frame's star rating (batch export merges per frame).
        rating = self._current_rating()
        if rating:
            tags['Exif.Image.Rating'] = str(rating)
        return tags

    def toggle_scopes(self) -> None:
        """Open or close the Scopes popover (Phase 18H).

        The popover holds the analysis panel's OWN histogram and H&D plot,
        so `_update_analysis` goes on feeding them wherever they are shown.
        Created once and kept, so it remembers where it was dragged to.
        """

        popover = getattr(self, '_scopes_popover', None)
        try:
            alive = popover is not None and popover.objectName() is not None
        except RuntimeError:
            alive = False
        if not alive:
            analysis = getattr(self._widgets, 'analysis', None)
            if analysis is None:
                return
            # 2026-09-08; see design/DECISIONS.md#d-controller-301
            popover = Popover('Scopes', meta='histogram', width=SCOPES_WIDTH,
                              resizable=True, settings_key='scopes',
                              min_height=SCOPES_MIN_HEIGHT,
                              parent=dialog_parent(self._viewer))
            panel = ScopesPanel(analysis)
            panel.changed.connect(popover.set_meta)
            popover.add(panel)
            popover.closed.connect(self._on_scopes_closed)
            popover._add_size_grip()
            popover.restore_size()
            popover.closed.connect(popover.save_size)
            self._scopes_popover = popover

        button = getattr(self, '_scopes_button', None)
        if popover.isVisible():
            popover.close()
            return
        if button is not None:
            popover.show_beside(button)
        else:
            popover.show()
        if button is not None:
            button.setChecked(True)

    def _on_scopes_closed(self) -> None:
        button = getattr(self, '_scopes_button', None)
        if button is not None and button.isChecked():
            button.setChecked(False)

    def _update_analysis(self, display_image) -> None:
        # The edit panel's levels plot draws against the same render, so it
        # is fed here rather than from a second hook that could drift.
        try:
            from spektrafilm_gui.edit_panel import feed_preview_image

            feed_preview_image(self._widgets, display_image)
        except Exception:
            pass

        analysis = getattr(self._widgets, 'analysis', None)
        if analysis is None:
            return
        try:
            analysis.set_image(display_image)
            if self._last_film_curves is not None:
                log_exposure, density_curves = self._last_film_curves
                analysis.set_curves(log_exposure, density_curves)
        except Exception:
            pass

    def _on_simulation_failed(self, message: str) -> None:
        self._active_simulation_worker = None
        mode_label = self._active_simulation_label or 'Simulation'
        self._active_simulation_label = None
        self._active_simulation_reports_status = True
        self._set_simulation_controls_enabled(True)
        notices.fail(dialog_parent(self._viewer), 'Run simulation', f'Simulation failed.\n\n{message}')
        set_status(self._viewer, f'{mode_label} failed')
        self._replay_pending_auto_preview()

    def _set_simulation_controls_enabled(self, enabled: bool) -> None:
        # 20.2: while something renders, a thin line runs along the frame
        # bar (`.framebar.is-busy`) -- the app reports no percentage, so
        # neither does the bar
        frame_bar = getattr(self, '_frame_bar', None)
        if frame_bar is not None:
            try:
                frame_bar.set_busy(not enabled)
            except RuntimeError:            # torn down with the window
                pass

    def _run_simulation(self, *, source_layer_name: str) -> None:
        image_data = self._simulation_input_image(source_layer_name=source_layer_name)
        if image_data is None:
            notices.warn(dialog_parent(self._viewer), 'Run simulation', 'Load an input image before running the simulation.')
            return

        state = collect_gui_state(widgets=self._widgets)
        self._sync_white_border(white_padding=state.display.white_padding)
        params = self._configure_simulation_params(
            build_params_from_state(state),
            source_layer_name=source_layer_name,
        )

        image = np.double(image_data)
        # Held here too. This method has no caller today, but it runs a full
        # pipeline on the GUI thread, so wiring it to anything without the
        # lock would put GPU dispatches beside a worker's.
        with runtime.PIPELINE_RENDER_LOCK:
            scan = self._process_image_with_runtime(image, params)
        scan_display, display_status = self._prepare_output_display_image(
            scan,
            output_color_space=state.simulation.output_color_space,
            use_display_transform=state.display.use_display_transform,
        )
        self._set_or_add_output_layer(
            scan_display,
            float_image=scan,
            output_color_space=state.simulation.output_color_space,
            output_cctf_encoding=True,
            use_display_transform=state.display.use_display_transform,
        )
        set_status(self._viewer, display_status)
