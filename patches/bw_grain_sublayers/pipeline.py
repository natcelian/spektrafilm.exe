# -------------------------------------------------------------------------
# FORK CONTRACT -- upstream has this file too, and both sides edit it.
# OURS: in __init__, the curve bake GATED on chemistry.active (upstream
#   refreshes curves from the model unconditionally; we keep shipped arrays
#   byte-identical when inactive), Phase 7 expiration riding the same bake
#   (+ the rms grain-gain fix, B2), the coupler-inversion margin warning
#   (2026-09-20); `process` -> `_process_nodes` with DeviceImage
#   materialization and _DEVICE_TAP_MAX_BYTES (G5, 16C taps); the single-copy
#   `_preprocess` with auto_exposure(owned=True), and its f32 preview host
#   path (`preview_host_f32`, NAT 2026-09-28), and its one NaN/Inf clamp
#   (`clamp_non_finite`); the grain sub-layer reconcile ahead of the bake
#   (`_reconcile_grain_sublayers`, for the stale B&W sub-layers).
# UPSTREAM: the topology, services, stages, select_development_time.
# ON CONFLICT: keep ours for __init__'s bake and `process`; port their new
#   nodes/services in by hand. Do NOT restore refresh_density_curves_from_model
#   unconditionally -- it moves the default render -- and keep
#   `_reconcile_grain_sublayers`, which upstream does not need. Untouched
#   upstream since 28bf883.
# Convention and rationale: docs/reports/2026.08.20_fork_contracts.md
# -------------------------------------------------------------------------

from __future__ import annotations

import math
import copy
import os
from time import perf_counter

import colour
import numpy as np

from spektrafilm.model.density_curves import evaluate_density_curves_layers, select_development_time
from spektrafilm.utils.finite_input import clamp_non_finite
from spektrafilm.utils.morph_curves import apply_print_curves_morph_with_layers
from spektrafilm.runtime.services import (
    EnlargerService,
    ResizingService,
    SpectralLUTService,
    ColorReferenceService,
)
from spektrafilm.runtime.stages import ConvertingStage, FilmingStage, PrintingStage, ScanningStage
from spektrafilm.runtime.topology import Node, Tap, run_topology
from spektrafilm.utils.timings import format_timings



class SimulationPipeline:
    """Thin runtime orchestrator that composes stage objects around a
    tap-based topology dispatcher.

    The pipeline declares its stages as a list of :class:`Node` objects via
    :meth:`_build_topology`. Calling :meth:`process` walks the topology in
    declared order, firing each node whose input taps are present in state,
    and returns the value at the requested ``collect`` tap.
    """

    def __init__(self, params, update_params=False):
        self._params = copy.deepcopy(params)

        self.camera = self._params.camera
        self.film = self._params.film
        self.film_render = self._params.film_render
        self.enlarger = self._params.enlarger
        self.print = self._params.print
        self.print_render = self._params.print_render
        self.scanner = self._params.scanner
        self.io = self._params.io
        self.workflow = self._params.workflow
        self.debug = self._params.debug
        self.settings = self._params.settings
        self.taps = self._params.taps

        # 20C (B&W adopted from upstream): collapse a development-time family to
        # ONE member before anything reads the curves. The loader used to do
        # this and no longer does, so a B&W profile arrives here carrying every
        # column -- density_curves (n, N), base_density (n_wl, N) -- and the
        # first consumer (the midgray print balance in FilmingStage.__init__)
        # would try to broadcast (81, 5) against a spectrum and fail.
        #
        # Unconditional and BEFORE the chemistry bake: it is not a look choice,
        # it is what makes the profile renderable at all. A colour stock has no
        # development_time, so this is a no-op for every one of them.
        if self.film is not None:
            select_development_time(
                self.film.data, self.film_render.chemistry.development_time)
        if self.print is not None:
            select_development_time(
                self.print.data, self.print_render.chemistry.development_time)

        # The grain splits each pixel's density over the sub-layer curves and
        # sums the grained sub-layers back, so those curves must add up to the
        # total curve or the grain moves the mean density. The shipped B&W
        # stocks (kodak_trix, kodak_doublex, and kodak_2302 when selected as a
        # film) carry stale sub-layer arrays that do not (Tri-X is off by up
        # to 0.89 density), which moved the tonality of their grained renders
        # (outside push/pull, whose bake regenerates them) away from the
        # grain-free preview. Upstream misses it because it regenerates both
        # arrays from the model unconditionally. Here only the sub-layers are
        # regenerated, and only when they disagree with the total: the total,
        # and every colour stock (all within 0.0052), pass through
        # byte-identical. Before the bakes below, so expiration starts
        # from consistent sub-layers.
        if self.film is not None:
            _reconcile_grain_sublayers(self.film.data, self.film.info.type)

        # Bake the film chemistry morph (experimental push/pull: coupled gamma /
        # developer exhaustion) into the film curves, regenerating the grain
        # sublayers from the same morphed parameters so grain stays consistent.
        # GATED on chemistry.active (deviation from the upstream branch, which
        # regenerates curves from the model unconditionally): when inactive the
        # profile's shipped sampled arrays pass through byte-identical, so the
        # default render cannot move. Develop itself runs unchanged; the params
        # (and the profile inside them) are deep-copied above, so the bake never
        # touches caller state and re-bakes fresh on every (re)construction.
        #
        # Film expiration (Phase 7) rides the SAME bake: its gamma axes are
        # composed multiplicatively into the chemistry morph (so push/pull and
        # aging stack through the one engine), then its own axes — the ones the
        # D(0)/D_max-preserving morph forbids — are applied to the sampled
        # arrays: speed loss (log-exposure shift), per-channel base fog, and
        # dye-density loss. Grain coarsening folds into the grain params.
        # Inactive or all-neutral expiration resolves to None and changes
        # NOTHING on this path.
        expiration_effect = None
        expiration = getattr(self.film_render, 'expiration', None)
        if expiration is not None and expiration.active:
            from spektrafilm.utils.expiration import resolve_expiration
            expiration_effect = resolve_expiration(expiration)
        if self.film is not None:
            film_data = self.film.data
            film_model = film_data.density_curves_model
            chemistry = getattr(self.film_render, 'chemistry', None)
            morph_params = (
                chemistry if chemistry is not None and chemistry.active else None)
            if expiration_effect is not None and (
                    expiration_effect.gamma != 1.0
                    or tuple(expiration_effect.gamma_rgb) != (1.0, 1.0, 1.0)):
                from spektrafilm.utils.expiration import compose_chemistry_gamma
                morph_params = compose_chemistry_gamma(morph_params, expiration_effect)
            if (morph_params is not None
                    and film_model is not None and film_model.n_layers > 0):
                total, layers = apply_print_curves_morph_with_layers(
                    film_data.log_exposure,
                    film_model,
                    morph_params,
                    profile_type=self.film.info.type,
                )
                film_data.density_curves = total
                film_data.density_curves_layers = (
                    layers if film_model.n_layers > 1 else None)
            if expiration_effect is not None:
                from spektrafilm.utils.expiration import apply_expiration_to_curves
                total, layers = apply_expiration_to_curves(
                    film_data.log_exposure,
                    film_data.density_curves,
                    getattr(film_data, 'density_curves_layers', None),
                    expiration_effect,
                )
                film_data.density_curves = total
                if layers is not None:
                    film_data.density_curves_layers = layers
                if expiration_effect.grain_gain != 1.0:
                    grain = self.film_render.grain
                    gain = float(expiration_effect.grain_gain)
                    if getattr(grain, 'model', 'production') == 'rms':
                        # THE RMS TIER DISCARDS particle_area_um2. It derives
                        # the particle area from the granularity target and
                        # returns `particle_area_um2=1.0`
                        # (`model/grain.resolve_grain_model`), so multiplying
                        # that field here did nothing at all -- and rms is the
                        # fresh-session default, so an expired stock's coarser
                        # grain was silently missing for anyone who had not
                        # changed grain models.
                        #
                        # The gain enters through the target instead. The
                        # derived area goes as target squared
                        # (`areas = target**2 * aperture / denom`), so the
                        # granularity scales by the square root to land on the
                        # same particle area the other models get.
                        root = math.sqrt(max(gain, 0.0))
                        grain.rms_granularity = tuple(
                            float(v) * root for v in grain.rms_granularity)
                    else:
                        grain.particle_area_um2 = (
                            float(grain.particle_area_um2) * gain)

        # 2026-09-20; see design/DECISIONS.md#d-pipeline-67
        self.coupler_inversion_margin = None
        if self.film is not None and getattr(self.film_render, 'dir_couplers', None) is not None:
            from spektrafilm.model.coupler_margin import warn_if_folded

            curves = np.asarray(self.film.data.density_curves, dtype=float)
            self.coupler_inversion_margin = warn_if_folded(
                curves - np.nanmin(curves, axis=0),
                self.film.data.log_exposure,
                self.film_render.dir_couplers,
                self.film.info.type,
                stock=self.film.info.stock,
            )

        self.timings = {}
        self._last_elapsed_time = None

        self._resize_service = ResizingService(self.io, self.camera.film_format_mm)
        if not update_params:
            self._lut_service = SpectralLUTService(self.settings.lut_resolution)
        self._enlarger_service = EnlargerService(self.enlarger)
        self._color_reference_service = ColorReferenceService(self.film, self.film_render,
                                                              self.print, self.print_render,
                                                              self.scanner.black_correction, self.scanner.white_correction,
                                                              self.scanner.black_level, self.scanner.white_level,
                                                              self.io, self.workflow)

        self._filming_stage = FilmingStage(
            self.film,
            self.film_render,
            self.camera,
            self.io,
            self.settings,
            self._lut_service,
            self._resize_service,
            self._enlarger_service,
            self._color_reference_service,
        )
        self._printing_stage = PrintingStage(
            self.film,
            self.film_render,
            self.print,
            self.print_render,
            self.enlarger,
            self.settings,
            self._lut_service,
            self._enlarger_service,
            self._resize_service,
            self._color_reference_service,
        )
        # The convert-film front-end (scanned negative -> cmy_film). Only built
        # when the workflow route needs it; it owns no LUT service and is cheap.
        self._converting_stage = (
            ConvertingStage(self.film, self.film_render, self.io, self.settings, self._lut_service)
            if self.workflow.do_convert_film else None
        )
        self._scanning_stage = ScanningStage(
            self.film,
            self.film_render,
            self.print,
            self.print_render,
            self.scanner,
            self.io,
            self.workflow,
            self.settings,
            self._lut_service,
            self._color_reference_service,
        )

        # timing communication
        self._filming_stage.timings = self.timings
        self._printing_stage.timings = self.timings
        self._scanning_stage.timings = self.timings
        self._lut_service.timings = self.timings

        self._topology: list[Node] = self._build_topology()

    def process(self, image, *, inject: str | None = None, collect: str | None = None):
        # (device-tap cap: see _DEVICE_TAP_MAX_BYTES at the end of this module)
        """Run the pipeline.

        Defaults run end-to-end (``rgb_in`` → ``rgb_out``). Pass
        ``inject`` / ``collect`` to enter or exit at a named tap.
        Call-site kwargs override the persistent :class:`TapsParams`
        defaults.
        """
        inject = inject or self.taps.inject or Tap.RGB_IN
        # Most routes end at rgb_out; routes that terminate at an intermediate
        # tap (e.g. convert-film > scan) declare it via workflow.collect_tap.
        collect = collect or self.taps.collect or self.workflow.collect_tap
        # G5: a stage may hand the next one a GPU-resident DeviceImage;
        # whatever reaches the collected tap materializes to host HERE, so
        # inject/collect semantics (16C) are byte-exact regardless of how
        # the value travelled.
        result = self._process_nodes(image, inject, collect)
        try:
            from spektrafilm.gpu.device_image import DeviceImage, as_host
            # 2026-09-19; see design/DECISIONS.md#d-pipeline-68
            if isinstance(result, DeviceImage):
                if not getattr(self, 'device_result', False):
                    result = as_host(result)
                elif collect != Tap.RGB_OUT:
                    if result.nbytes <= _DEVICE_TAP_MAX_BYTES:
                        result.persistent = True
                    else:
                        result = as_host(result)
        except ImportError:
            pass
        return result

    def _process_nodes(self, image, inject: str, collect: str):

        self.timings.clear()
        start = perf_counter()
        try:
            return run_topology(
                self._topology, inject, collect, image,
                on_fire=self._record_node_timing,
            )
        finally:
            self._last_elapsed_time = perf_counter() - start

    def get_timings(self):
        return self.timings

    def get_total_elapsed_time(self):
        return self._last_elapsed_time

    def format_timings(self):
        return format_timings(
            self.get_timings(),
            total_elapsed_time=self.get_total_elapsed_time(),
        )

    def print_timings(self):
        print(self.format_timings())

    def update(self, params):
        """Update params and re-initialize stages that depend on them."""
        self.__init__(params, update_params=True)

    def soft_update(self,
                    exposure_compensation_ev=None,
                    print_exposure=None,
                    c_filter_neutral=None,
                    m_filter_neutral=None,
                    y_filter_neutral=None,
                    film_density_curves=None,
                    print_density_curves=None,):
        invalidates_print_balance_reference = False
        if exposure_compensation_ev is not None:
            self.camera.exposure_compensation_ev = exposure_compensation_ev
            invalidates_print_balance_reference = True
        if print_exposure is not None:
            self.enlarger.print_exposure = print_exposure
        if c_filter_neutral is not None:
            self.enlarger.c_filter_neutral = c_filter_neutral
        if m_filter_neutral is not None:
            self.enlarger.m_filter_neutral = m_filter_neutral
        if y_filter_neutral is not None:
            self.enlarger.y_filter_neutral = y_filter_neutral
        if film_density_curves is not None:
            self.film.data.density_curves = film_density_curves
            invalidates_print_balance_reference = True
        if print_density_curves is not None:
            self.print.data.density_curves = print_density_curves
        if invalidates_print_balance_reference:
            (
                self._enlarger_service.density_spectral_midgray,
                self._enlarger_service.density_spectral_midgray_comp,
            ) = self._filming_stage._compute_density_spectral_midgray_to_balance_print()

    # private methods

    def _build_topology(self) -> list[Node]:
        f, p, s = self._filming_stage, self._printing_stage, self._scanning_stage
        nodes = [
            Node((Tap.RGB_IN,), (Tap.RGB_PRE,), self._preprocess, "preprocess"),
        ]
        if self.workflow.do_filming:
            nodes += [
                Node((Tap.RGB_PRE,),    (Tap.LOG_E_FILM,), f.expose,  "filming.expose"),
                Node((Tap.LOG_E_FILM,), (Tap.CMY_FILM,),   f.develop, "filming.develop"),
            ]
        elif self.workflow.do_convert_film:
            # Convert workflow: convert a scanned negative directly to film cmy.
            nodes += [
                Node((Tap.RGB_PRE,), (Tap.CMY_FILM,),
                     self._converting_stage.convert, "converting.convert"),
            ]
        if self.workflow.do_printing:
            nodes += [
                Node((Tap.CMY_FILM,),    (Tap.LOG_E_PRINT,), p.expose,  "printing.expose"),
                Node((Tap.LOG_E_PRINT,), (Tap.CMY_PRINT,),   p.develop, "printing.develop"),
            ]
        if self.workflow.do_scan:
            if self.workflow.scan_film:
                nodes += [
                    Node((Tap.CMY_FILM,), (Tap.RGB_OUT,), s.scan, "scanning.scan_film"),
                ]
            else:
                nodes += [
                    Node((Tap.CMY_PRINT,), (Tap.RGB_OUT,), s.scan, "scanning.scan_print"),
                ]
        if self.workflow.passthrough:
            # No film/print/scan: just colour-manage the input to the output space.
            nodes += [
                Node((Tap.RGB_PRE,), (Tap.RGB_OUT,), self._passthrough, "passthrough"),
            ]
        return nodes

    def _preprocess(self, image):
        # ONE f64 materialization, not two. `np.double(np.array(x))` copied
        # the input twice — ~583 MB apiece at 24 MP — because np.array
        # already copies and np.double copies again. The remaining copy is
        # REQUIRED: it makes `image` ours, which is what lets auto_exposure
        # scale it in place below instead of allocating a third full frame.
        # The interactive preview on the GPU makes that copy float32 instead
        # (preview_host_f32): the resident expose chain uploads f32, so the
        # f64 frame was converted straight back. Exports never take it.
        dtype = np.float32 if preview_host_f32(self.settings, self.workflow) else np.float64
        image = np.asarray(image)[:, :, 0:3].astype(dtype, order="C")
        image = clamp_non_finite(image)
        image = self._filming_stage.auto_exposure(
            image, owned=True, expose_follows=bool(self.workflow.do_filming))
        image = self._resize_service.crop_and_rescale(image)
        return image

    def _passthrough(self, image):
        """The bare "input" route: colour-manage the (decoded) input straight into
        the output colour space so it is directly viewable, with no film / print /
        scan. Decodes the input CCTF if requested, converts input -> output
        primaries (with chromatic adaptation between their white points), and
        applies the output CCTF encoding if requested. No gamut compression — it is
        a faithful view of the input, so out-of-output-gamut colours may clip."""
        return colour.RGB_to_RGB(
            np.asarray(image, dtype=float),
            self.io.input_color_space,
            self.io.output_color_space,
            apply_cctf_decoding=self.io.input_cctf_decoding,
            apply_cctf_encoding=self.io.output_cctf_encoding,
        )

    def _record_node_timing(self, node: Node, elapsed: float) -> None:
        self.timings[node.label] = self.timings.get(node.label, 0.0) + elapsed

# 2026-09-28; see design/DECISIONS.md#d-pipeline-69
PREVIEW_F32_FLAG = 'SPEKTRAFILM_PREVIEW_F32'


def preview_host_f32(settings, workflow) -> bool:
    """True when this render may carry its working image on the host as
    float32: a preview (`settings.preview_mode`, which exports clear) whose
    RGB_PRE tap feeds filming.expose, on the GPU backend with the resident
    expose chain on -- the one consumer that uploads f32 without a
    conversion. Anything else keeps the exact float64 path; if the chain
    then declines, filming.expose widens the frame back to float64 before
    the per-stage path runs. Measured drift and the pin:
    tests/test_preview_f32_host.py."""
    if not getattr(settings, 'preview_mode', False):
        return False
    if not getattr(workflow, 'do_filming', False):
        return False
    if os.environ.get(PREVIEW_F32_FLAG, '1').strip().lower() in ('0', 'off', 'false', 'no'):
        return False
    try:
        from spektrafilm.gpu.backend import Backend, resolve_backend
        from spektrafilm.gpu.expose_resident import _resident_expose_enabled
        return _resident_expose_enabled() and resolve_backend() is Backend.GPU
    except Exception:   # no GPU package or no device: the exact path
        return False


#: Largest intermediate tap kept on the device in device_result mode (f32 RGB):
#: 256 MiB is a 22 MP frame. Bigger taps come back as host arrays, as before.
_DEVICE_TAP_MAX_BYTES = 256 * 1024 * 1024


#: Largest |sum(sub-layers) - total| (density) a film's grain sub-layers may
#: show before they are regenerated from the model. The shipped colour stocks
#: sit at <= 0.0052 (the total carries measurement ripples the layer sum does
#: not); the stale B&W ones at 0.08 to 0.89.
_SUBLAYER_SUM_TOLERANCE = 0.01


def _reconcile_grain_sublayers(film_data, profile_type) -> None:
    """Regenerate `density_curves_layers` from the density model when the
    shipped sub-layers do not sum to the total curve but the model's do.

    Leaves everything untouched when there are no sub-layers or no
    multi-layer model, when the shipped sub-layers are already consistent,
    when the model cannot be evaluated, or when the model would not be more
    consistent (a custom or edited total the model does not describe)."""
    layers = getattr(film_data, 'density_curves_layers', None)
    model = getattr(film_data, 'density_curves_model', None)
    if layers is None or model is None or getattr(model, 'n_layers', 0) < 2:
        return
    layers = np.asarray(layers, dtype=float)
    total = np.asarray(film_data.density_curves, dtype=float)
    if layers.ndim != 3 or layers.shape[0] != total.shape[0] or layers.shape[2] != total.shape[1]:
        return
    shipped_error = float(np.nanmax(np.abs(np.nansum(layers, axis=1) - total)))
    if not shipped_error > _SUBLAYER_SUM_TOLERANCE:    # also leaves an all-NaN error alone
        return
    try:
        candidate = evaluate_density_curves_layers(model, film_data.log_exposure, profile_type)
    except ValueError:
        return                          # unknown model type: keep the shipped arrays
    if candidate.shape != layers.shape:
        return
    model_error = float(np.nanmax(np.abs(np.nansum(candidate, axis=1) - total)))
    if model_error <= _SUBLAYER_SUM_TOLERANCE and model_error < shipped_error:
        film_data.density_curves_layers = candidate
