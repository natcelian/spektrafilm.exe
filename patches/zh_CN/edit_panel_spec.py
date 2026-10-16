"""The edit panel's structure, transcribed from the spec page (Phase 18F).

The spec page (`design/pages/phase-20.3-full-ui.html`; first transcribed from
the July page, retired 2026-10-07) defines its right panel
as data -- a `STAGES` object of stages, each holding groups with a name, a
collapsed flag, a `meta:()` summary formatter and an ordered row list. This
module is that object in Python.

2026-07-31; see design/DECISIONS.md#d-edit_panel_spec-536

Rows are either:
  - a `'section.field'` string, naming a real state field; or
  - a `Special(kind, **options)`, for the rows the design draws itself (the
    stock chip, the metering strip, the couplers button, an Advanced
    disclosure...). Each is built by the panel, not by a generic editor.

A row naming a field the app does not have is a FINDING, not a crash: the
builder skips it and reports it, so a spec/app divergence surfaces as a
list rather than as a missing control nobody noticed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from spektrafilm_gui import library_index, params_auto


@dataclass(frozen=True)
class Special:
    """A row the design draws itself rather than from a field spec."""

    kind: str
    options: dict = field(default_factory=dict)

    def __hash__(self) -> int:      # dataclasses with a dict are unhashable
        return hash((self.kind, tuple(sorted(self.options))))


@dataclass(frozen=True)
class Group:
    name: str
    rows: tuple = ()
    collapsed: bool = False
    #: takes a value-lookup callable, returns the header's right-aligned text
    meta: Callable[[Callable[[str], object]], str] | None = None
    note: str = ''


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    groups: tuple[Group, ...]


# ==========================================================================
# the page's own formatters
# ==========================================================================


def fmt_num(value, *, decimals: int = 2, unit: str = '',
            signed: bool = False) -> str:
    """`fmtNum` — fixed decimals, an explicit + when the range crosses zero,
    then the unit."""
    text = f'{float(value):.{decimals}f}'
    if signed and float(value) > 0:
        text = '+' + text
    return text + (unit or '')


def fmt_one(value, *, decimals: int = 2, unit: str = '',
            signed: bool = False) -> str:
    """`fmtOne` — a tuple joins with spaces, a scalar formats alone."""
    if isinstance(value, (tuple, list)):
        return ' '.join(fmt_num(v, decimals=decimals, unit=unit, signed=signed)
                        for v in value)
    return fmt_num(value, decimals=decimals, unit=unit, signed=signed)


def _stops(value) -> str:
    """`filmMeta`'s push/pull suffix: nothing at 0, else a signed value that
    stays an integer when it is one."""
    stops = float(value)
    if stops == 0:
        return ''
    body = str(int(stops)) if stops.is_integer() else f'{stops:.2f}'
    return ' ' + ('+' if stops > 0 else '') + body


def film_meta(get) -> str:
    """`filmMeta()` — the stock's label, plus the push/pull stops."""
    return f"{get('simulation.film_stock')}{_stops(get('push_pull.stops') or 0)}"


def head_meta(get) -> str:
    """`headMeta()` — 'C0 M-4 Y12'."""
    return (f"C{get('simulation.print_c_filter_shift')}"
            f" M{get('simulation.print_m_filter_shift')}"
            f" Y{get('simulation.print_y_filter_shift')}")


def tone_meta(get) -> str:
    """'S 220° 0.00 · H 45° 0.00', and M between them once it is doing
    something.

    The midtone band is shown ONLY when its strength is non-zero. The
    summary sits on a 262px popover button and all three bands spelled out
    would elide before the third was readable -- and a band at zero has
    nothing to report, which is the same reason the row exists at all.
    """
    parts = [f"S {float(get('scan_finishing.shadow_tint_hue')):.0f}° "
             f"{float(get('scan_finishing.shadow_tint_strength')):.2f}"]
    try:
        midtone = float(get('scan_finishing.midtone_tint_strength'))
    except (TypeError, ValueError):
        midtone = 0.0
    if midtone:
        parts.append(f"M {float(get('scan_finishing.midtone_tint_hue')):.0f}° "
                     f"{midtone:.2f}")
    parts.append(f"H {float(get('scan_finishing.highlight_tint_hue')):.0f}° "
                 f"{float(get('scan_finishing.highlight_tint_strength')):.2f}")
    return ", ".join(parts)


def _base_meta(prefix: str) -> Callable[[Callable[[str], object]], str]:
    """'x1.00 · C1.00 M1.00 Y1.00', or 'neutral' when nothing is doing
    anything.

    The dye triple alone would read as three numbers with no scale, and the
    scale alone hides a colour cast -- and at the defaults both are noise, so
    the summary says so in one word instead.
    """
    def meta(get) -> str:
        scale = float(get(f'{prefix}.scale'))
        cyan = float(get(f'{prefix}.cyan'))
        magenta = float(get(f'{prefix}.magenta'))
        yellow = float(get(f'{prefix}.yellow'))
        tilt = float(get(f'{prefix}.tilt')) if prefix == 'film_base' else 0.0
        if (scale, cyan, magenta, yellow, tilt) == (1.0, 1.0, 1.0, 1.0, 0.0):
            return 'neutral'
        parts = [f'x{scale:.2f}']
        if tilt:
            parts.append(f'tilt {tilt:+.2f}')
        # 2026-09-05, 2026-09-24; see design/DECISIONS.md#d-edit_panel_spec-527
        parts.append(f'C{cyan:.2f} M{magenta:.2f} Y{yellow:.2f}')
        return ', '.join(parts)
    return meta


film_base_meta = _base_meta('film_base')
print_base_meta = _base_meta('print_base')


def _on_off(field_name: str) -> Callable[[Callable[[str], object]], str]:
    return lambda get: '开启' if get(field_name) else '关闭'


def _plain(field_name: str) -> Callable[[Callable[[str], object]], str]:
    def _trans(get):
        v = str(get(field_name))
        try:
            from spektrafilm_gui.zh_translation import translate_option
            return translate_option(v, v)
        except Exception:
            return v
    return _trans


def _one(field_name: str, **kwargs) -> Callable[[Callable[[str], object]], str]:
    return lambda get: fmt_one(get(field_name), **kwargs)


# ==========================================================================
# the stages
# ==========================================================================
# The kind strings are the PAGE's own (`{t:'bigsel'}`, `{t:'pp'}`, ...), so
# a reader can grep either file and land in the same place.
STOCK_SEL = Special('bigsel', {'id': 'simulation.film_stock',
                               'sub': 'C-41, colour negative', 'chip': 'film'})
PAPER_SEL = Special('bigsel', {'id': 'simulation.print_paper',
                               'sub': 'lustre, neutral base', 'chip': 'paper'})
PP_ROW = Special('pp')
METER_ROW = Special('meter')
FILTER_ROW = Special('filters')
HEAD_ROW = Special('head')
PREFLASH_ROW = Special('preflashhead')
# 2026-08-31; see design/DECISIONS.md#d-edit_panel_spec-528
FILM_BASE_ROW = Special('filmbase')
PRINT_BASE_ROW = Special('printbase')
CURVE_ROW = Special('curve')
HUESAT_ROW = Special('huesat')


def filter_stack_meta(get) -> str:
    """`filterStackMeta()`: the mounted filters' names joined with ' + ', or
    '无' (the phase-20 filter page's "Camera filtration" header)."""

    names = []
    for path, kind in (('input_image.color_filter', 'filter'),
                       ('input_image.color_filter_2', 'filter'),
                       ('input_image.nd_filter', 'nd')):
        value = str(get(path) or 'none')
        if value not in ('none', '', 'None'):
            try:
                names.append(library_index.item(kind, value).name)
            except Exception:
                names.append(value)
    return ' + '.join(names) if names else '无'

MAIN = Stage('main', 'Main', (
    Group('Film', meta=film_meta, rows=(
        STOCK_SEL, 'simulation.raw', PP_ROW,
        'simulation.exposure_compensation_ev', 'simulation.film_exposure_ev',
        'simulation.auto_exposure', METER_ROW)),
    # 2026-09-11, 2026-09-26; see design/DECISIONS.md#d-edit_panel_spec-529
    Group('Camera filtration', collapsed=True, meta=filter_stack_meta,
          rows=(FILTER_ROW,)),
    Group('Input', collapsed=True,
          meta=_plain('input_image.spectral_upsampling_method'),
          rows=(Special('method'), 'input_image.upscale_factor')),
    # 2026-09-11, 2026-09-30; see design/DECISIONS.md#d-edit_panel_spec-530
    Group('Enlarger', meta=lambda get: str(get('simulation.print_paper')), rows=(
        PAPER_SEL, 'simulation.print_neutral_filters_from_database', HEAD_ROW,
        'simulation.print_exposure')),
    # 20.3: a header carries a value, not a word ('finishing')
    Group('Scan', meta=lambda get: fmt_one(get('scan_finishing.exposure_ev'), unit=' ev',
                                           signed=True), rows=(
        'scan_finishing.exposure_ev', 'scan_finishing.white_point')),
    Group('Grain', collapsed=True,
          meta=lambda get: f"{get('grain.model')} , {fmt_one(get('grain.amount'))}",
          rows=('grain.model', 'grain.amount', 'grain.saturation')),
))

FILM = Stage('film', 'Film', (
    Group('Emulsion', meta=lambda get: str(get('simulation.film_stock')), rows=(
        STOCK_SEL, 'simulation.raw', Special('format'))),
    Group('Camera', rows=(
        'simulation.camera_lens_blur_um', 'simulation.exposure_compensation_ev',
        'simulation.film_exposure_ev', 'simulation.auto_exposure', METER_ROW)),
    Group('Push / pull',
          meta=lambda get: f"{fmt_one(get('push_pull.stops'))} , {get('push_pull.mode')}",
          rows=('push_pull.active', 'push_pull.mode', 'push_pull.stops',
                # 2026-09-03; see design/DECISIONS.md#d-edit_panel_spec-531
                'special.film_gamma_factor')),
    # 2026-08-31; see design/DECISIONS.md#d-edit_panel_spec-532
    Group('Chemistry', collapsed=True, meta=_on_off('film_chemistry.active'),
          rows=('simulation.film_development_time', 'film_chemistry.active',
                'film_chemistry.gamma_factor', 'film_chemistry.gamma_layers',
                'film_chemistry.gamma_rgb',
                'film_chemistry.developer_exhaustion')),
    Group('Base', collapsed=True, meta=film_base_meta, rows=(
        'film_base.active', 'film_base.scale', 'film_base.tilt',
        FILM_BASE_ROW)),
    Group('Aging', collapsed=True,
          meta=lambda get: (f"{fmt_one(get('aging.age_years'))} , {get('aging.storage')}"
                            if get('aging.active') else 'off'),
          rows=('aging.active', 'aging.age_years', 'aging.storage', 'aging.fog_rgb',
                'aging.gamma', 'aging.gamma_rgb', 'aging.density_scale',
                'aging.grain_gain', 'aging.speed_loss_ev')),
    Group('DIR couplers', collapsed=True, meta=_on_off('couplers.active'), rows=(
        'couplers.active', 'couplers.amount', 'couplers.inhibition_samelayer',
        'couplers.inhibition_interlayer', Special('coupbtn'))),
    Group('Grain', collapsed=True,
          meta=lambda get: f"{get('grain.model')} , {fmt_one(get('grain.amount'))}",
          rows=('grain.active', 'grain.model', 'grain.amount', 'grain.saturation',
                'grain.sublayers_active', 'grain.particle_area_um2',
                'grain.particle_scale', 'grain.particle_scale_sublayers',
                'grain.density_min', 'grain.uniformity', 'grain.blur',
                'grain.blur_dye_clouds_um', 'grain.micro_structure',
                'grain.rms_granularity', 'grain.rms_strength',
                Special('adv', {'label': 'Synthesis (research)', 'rows': (
                    'grain.synthesis_size', 'grain.synthesis_amount',
                    'grain.synthesis_sharpness', 'grain.synthesis_quality',
                    'grain.synthesis_samples', 'grain.synthesis_mean_radius_um',
                    'grain.synthesis_radius_stddev_ratio',
                    'grain.synthesis_aperture_sigma_um',
                    'grain.synthesis_cell_size_ratio',
                    'grain.synthesis_max_radius_quantile',
                    'grain.synthesis_coverage_epsilon',
                    'grain.synthesis_max_grains_per_cell',
                    'grain.synthesis_radius_scale', 'grain.synthesis_layered',
                    'grain.synthesis_layer_scale')}))),
    Group('Halation', collapsed=True, meta=_on_off('halation.active'), rows=(
        'halation.active', Special('halpreview'), 'halation.scatter_amount',
        'halation.scatter_spatial_scale', 'halation.halation_amount',
        'halation.halation_spatial_scale', 'halation.boost_ev',
        'halation.protect_ev', 'halation.boost_range',
        Special('adv', {'label': 'Model (research)', 'rows': (
            'halation.scatter_core_um', 'halation.scatter_tail_um',
            'halation.scatter_tail_weight', 'halation.halation_strength',
            'halation.halation_first_sigma_um', 'halation.halation_n_bounces',
            'halation.halation_bounce_decay', 'halation.halation_renormalize')}))),
    Group('Bleach bypass', collapsed=True,
          meta=_one('simulation.negative_bleach_bypass'),
          rows=('simulation.negative_bleach_bypass',
                'simulation.negative_leuco_cyan_coupling')),
    Group('Camera diffusion', collapsed=True,
          meta=_on_off('simulation.camera_diffusion_filter_active'), rows=(
              'simulation.camera_diffusion_filter_active',
              Special('glowpreview', {'stage': 'camera'}),
              'simulation.camera_diffusion_filter_family',
              'simulation.camera_diffusion_filter_strength',
              'simulation.camera_diffusion_filter_spatial_scale',
              'simulation.camera_diffusion_filter_halo_warmth',
              Special('adv', {'rows': (
                  'simulation.camera_diffusion_filter_core_intensity',
                  'simulation.camera_diffusion_filter_core_size',
                  'simulation.camera_diffusion_filter_halo_intensity',
                  'simulation.camera_diffusion_filter_halo_size',
                  'simulation.camera_diffusion_filter_bloom_intensity',
                  'simulation.camera_diffusion_filter_bloom_size')}))),
))

PRINT = Stage('print', 'Print', (
    Group('Paper', meta=lambda get: str(get('simulation.print_paper')), rows=(
        PAPER_SEL, 'simulation.print_illuminant',
        Special('note', {'text': 'Paper texture finish lives on Adv.'}))),
    # 2026-07-29, 2026-09-04; see design/DECISIONS.md#d-edit_panel_spec-533
    Group('Enlarger head', meta=head_meta, rows=(
        HEAD_ROW, 'simulation.print_neutral_filters_from_database',
        'simulation.print_exposure')),
    Group('Shaping', collapsed=True, rows=(
        'simulation.print_shadow_shape', 'simulation.print_highlight_shape')),
    Group('Chemistry', collapsed=True, meta=_on_off('chemistry.active'), rows=(
        'chemistry.active', 'chemistry.gamma_factor', 'chemistry.gamma_layers',
        'chemistry.gamma_rgb', 'chemistry.developer_exhaustion')),
    Group('Base', collapsed=True, meta=print_base_meta, rows=(
        'print_base.active', 'print_base.scale', PRINT_BASE_ROW)),
    Group('Glare', collapsed=True, meta=_on_off('glare.active'), rows=(
        'glare.active', Special('glarepad'), 'glare.percent',
        'glare.roughness', 'glare.blur')),
    Group('Preflash', collapsed=True, meta=_one('preflashing.exposure'), rows=(
        'preflashing.active', 'preflashing.exposure', PREFLASH_ROW)),
    Group('Bleach bypass', collapsed=True,
          meta=_one('simulation.print_bleach_bypass'),
          rows=('simulation.print_bleach_bypass',)),
    Group('Print diffusion', collapsed=True,
          meta=_on_off('simulation.diffusion_filter_active'), rows=(
              'simulation.diffusion_filter_active',
              Special('glowpreview', {'stage': 'print'}),
              'simulation.diffusion_filter_family',
              'simulation.diffusion_filter_strength',
              'simulation.diffusion_filter_spatial_scale',
              'simulation.diffusion_filter_halo_warmth',
              Special('adv', {'rows': (
                  'simulation.diffusion_filter_core_intensity',
                  'simulation.diffusion_filter_core_size',
                  'simulation.diffusion_filter_halo_intensity',
                  'simulation.diffusion_filter_halo_size',
                  'simulation.diffusion_filter_bloom_intensity',
                  'simulation.diffusion_filter_bloom_size')}))),
))

SCAN = Stage('scan', 'Scan', (
    Group('Frame', collapsed=True, rows=(
        'scan_finishing.crop', 'scan_finishing.crop_ratio',
        'scan_finishing.skew_deg', Special('desq'),
        Special('btns', {'items': ('Crop', 'Straighten', 'Swap', 'Reset')}),
        Special('note', {'text': 'Swap also lives in the top bar while the '
                                 'crop is armed.'}))),
    Group('Scanner', collapsed=True, rows=(
        'simulation.scan_lens_blur', 'simulation.scan_white_correction',
        'simulation.scan_white_level', 'simulation.scan_black_correction',
        'simulation.scan_black_level', 'simulation.scan_unsharp_mask')),
    # the page's `fmtOne` carries the unit: '0.00 ev', as Main > Scan now reads
    Group('Exposure', meta=lambda get: fmt_one(get('scan_finishing.exposure_ev'), unit=' ev',
                                               signed=True), rows=(
        'scan_finishing.active', 'scan_finishing.exposure_ev',
        'scan_finishing.gain_rgb', 'scan_finishing.color_separation')),
    Group('Levels', collapsed=True, rows=(
        Special('levels'), 'scan_finishing.white_point_rgb',
        'scan_finishing.black_point_rgb')),
    # Gamma and contrast are full sliders; toe and shoulder are ONE tuple
    # row of mini sliders under them.
    #
    # This went the long way round. The four were four full slider rows (the
    # page); then a 2x2 of drag cells above them ("side by side in boxes like
    # on the curve popover"); then the cells alone, moved here from under the
    # plot. NAT, seeing that: "Gamma and contrast should stay sliders. ...
    # shoulder and toe are smaller sliders like in the print chemistry
    # settings with fast and slow." Which reads the group correctly -- gamma
    # and contrast are the two you set, toe and shoulder are the trim you
    # nudge, and a row shape already exists that says exactly that.
    Group('Response',
          meta=lambda get: f"contrast {fmt_one(get('scan_finishing.contrast'))}",
          rows=('scan_finishing.gamma', 'scan_finishing.contrast',
                'scan_finishing.toe_shoulder',
                'scan_finishing.micro_contrast')),
    Group('Curve', collapsed=True, rows=(CURVE_ROW,)),
    # `wrap.append(fg, rowFor('scan_finishing.vibrance'))` — vibrance sits
    # UNDER the hue-saturation row, and was missing from the panel entirely.
    Group('Colour', collapsed=True,
          rows=(HUESAT_ROW, 'scan_finishing.vibrance')),
    Group('Toning', collapsed=True, meta=tone_meta, rows=(Special('toningbtn'),)),
))

ADV = Stage('adv', 'Adv', (
    Group('Input image', rows=(
        'input_image.input_color_space', 'input_image.apply_cctf_decoding',
        'input_image.upscale_factor', Special('prevcrop'))),
    Group('Spectral upsampling',
          meta=_plain('input_image.spectral_upsampling_method'), rows=(
              Special('method'),
              'input_image.apply_hanatos2025_adaptation_window',
              'input_image.apply_hanatos2025_adaptation_surface',
              'input_image.spectral_gaussian_blur',
              Special('specuv'), Special('specir'),
              # QA 09-12 item 6: a title where the IR slider ends and the
              # filter rows begin. Adv only -- on MAIN the group names them.
              Special('sectitle', {'text': 'Taking filters'}), FILTER_ROW)),
    Group('Input gamut compress', collapsed=True,
          meta=lambda get: (str(get('input_gamut_compress.algorithm'))
                            if get('input_gamut_compress.active') else 'off'),
          rows=('input_gamut_compress.active', 'input_gamut_compress.algorithm',
                'input_gamut_compress.knee')),
    Group('Output gamut compress', collapsed=True,
          meta=_plain('output_gamut_compress.algorithm'), rows=(
              'output_gamut_compress.algorithm', 'output_gamut_compress.knee',
              Special('note', {'text': 'Runs per pixel on the CPU: cam16ucs '
                                       'adds about 30 s to a 24 MP export.'}))),
    # NAT 2026-09-03: "can we remove film and print gamma factor from
    # advanced, tune". Both were second controls on fields that now have a
    # first one, and they went different ways because they drive DIFFERENT
    # engine mechanisms:
    #
    #   print_gamma_factor -> multiplied into print_render.chemistry
    #     .gamma_factor, which is exactly what the PRINT Chemistry group's own
    #     gamma factor writes. A true duplicate, so it is FOLDED (see
    #     persistence._fold_retired_tune_gamma) and gone.
    #
    #   film_gamma_factor -> film_render.density_curve_gamma, which is NOT the
    #     chemistry morph -- it is the runtime gamma handed to develop_simple,
    #     the same field Push / pull's standard mode multiplies its stops
    #     into. So it MOVED to Push / pull rather than being folded anywhere;
    #     folding it into the film Chemistry gamma would have changed the
    #     picture, because those are two different computations.
    #
    # What is left here is the two channel swaps, which is what Tune was.
    Group('Tune', collapsed=True, rows=(
        'special.film_channel_swap', 'special.print_channel_swap')),
    Group('Paper texture', collapsed=True, meta=_on_off('paper.active'), rows=(
        'paper.active', 'paper.padding_burn',
        Special('btns', {'items': ('Add texture…', 'Clear')}),
        Special('note', {'text': 'Lays a scanned paper texture over the finished '
                                 'print.'}))),
    # 2026-09-28; see design/DECISIONS.md#d-edit_panel_spec-534
    *((Group(params_auto.GROUP_TITLE, collapsed=True,
             rows=params_auto.group_rows()),)
      if params_auto.AUTO_PARAMS else ()),
))

#: The design's five stage tabs, in order. CONFIG is NOT here -- it moved to
#: the library panel in 18E, because app settings are a place, not a stage.
STAGES: tuple[Stage, ...] = (MAIN, FILM, PRINT, SCAN, ADV)


#: The spec names some rows as ONE tuple where the app stores separate
#: scalars -- `gain_rgb` for three EV fields, `gamma_rgb` for three gammas,
#: `gamma_layers` for the fast/slow pair. That is not a divergence: the app
#: already presents exactly these as channel-tinted knob triplets, which is
#: what the design draws too. This is the translation, kept here so the
#: transcription above can stay literally the spec's.
TUPLE_ROWS: dict[str, tuple[str, ...]] = {
    'chemistry.gamma_layers': (
        'chemistry.gamma_factor_fast', 'chemistry.gamma_factor_slow'),
    'chemistry.gamma_rgb': (
        'chemistry.gamma_factor_red', 'chemistry.gamma_factor_green',
        'chemistry.gamma_factor_blue'),
    'film_chemistry.gamma_layers': (
        'film_chemistry.gamma_factor_fast', 'film_chemistry.gamma_factor_slow'),
    'film_chemistry.gamma_rgb': (
        'film_chemistry.gamma_factor_red', 'film_chemistry.gamma_factor_green',
        'film_chemistry.gamma_factor_blue'),
    'scan_finishing.gain_rgb': (
        'scan_finishing.gain_red_ev', 'scan_finishing.gain_green_ev',
        'scan_finishing.gain_blue_ev'),
    # 2026-08-27; see design/DECISIONS.md#d-edit_panel_spec-535
    'scan_finishing.toe_shoulder': (
        'scan_finishing.toe', 'scan_finishing.shoulder'),
}

#: Rows the design shows that are UI-only in the app -- the crop ratio is a
#: selector on the crop tool, not a stored parameter.
WIDGET_ROWS: frozenset[str] = frozenset({'scan_finishing.crop_ratio'})


def resolve_row(row: str) -> tuple[str, ...]:
    """The real state field(s) a spec row names. Empty for a widget-only row."""
    if row in TUPLE_ROWS:
        return TUPLE_ROWS[row]
    if row in WIDGET_ROWS:
        return ()
    return (row,)


def field_rows(stage: Stage) -> tuple[str, ...]:
    """Every `'section.field'` a stage names, including inside Advanced
    disclosures. Used to check the spec against the app's real state."""
    found: list[str] = []

    def walk(rows) -> None:
        for row in rows:
            if isinstance(row, str):
                found.append(row)
            elif isinstance(row, Special):
                walk(row.options.get('rows', ()))

    for group in stage.groups:
        walk(group.rows)
    return tuple(found)
