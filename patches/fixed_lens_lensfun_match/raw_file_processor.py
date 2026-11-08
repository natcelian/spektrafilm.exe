# -------------------------------------------------------------------------
# FORK CONTRACT -- upstream has this file too, and both sides edit it.
# OURS: Phase 17A's split of the loader into `load_raw_daylight_base` +
#   `apply_raw_white_balance` (live WB, byte-identical by construction;
#   as_shot stays decode-time), `solve_neutral_white_balance` (WB
#   eyedropper), `_decode_raw` path-then-stream retry and readable LibRaw
#   errors (2026-07-22/25), `_lens_correct`, `_convert_output_colorspace`,
#   and the optional `recover` argument (highlight mode, cached denoise, dust
#   removal, Barrel, Vignette; `raw_recover.py`), which changes nothing when it
#   is None. The fixed-lens fallback at the end of `_find_lens_candidates`
#   (the one lens lensfun has for a Leica Q / Q2 / Q3 and the like).
#   The decode is not bit-reproducible, accepted (B11).
# UPSTREAM: _postprocess_params, the WB/tint math, lens correction, EXIF.
# ON CONFLICT: keep ours; port their changes into the helpers the split
#   calls, keeping load_and_process_raw_file == base + WB tail. Untouched
#   upstream since 28bf883.
# Convention and rationale: docs/reports/2026.08.20_fork_contracts.md
# -------------------------------------------------------------------------

from __future__ import annotations

import json
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

import colour
import exiv2
import lensfunpy
import numpy as np
import rawpy
from scipy.ndimage import map_coordinates

from spektrafilm.utils.raw_denoise import blend_linear, blend_mosaic
from spektrafilm.utils.raw_recover import RecoverSettings, apply_lens_adjust, highlight_gain, highlight_params
from spektrafilm.utils.sensordust import SOFT_FRACTION, DustSession

_TUNGSTEN_TEMPERATURE = 2850.0
_DAYLIGHT_REFERENCE_TEMPERATURE = 6504.0
_ACES_COLOURSPACE = colour.RGB_COLOURSPACES["ACES2065-1"]


@dataclass(frozen=True, slots=True)
class ExifData:
    make: str
    model: str
    lens_make: str
    lens_model: str
    focal_length: float
    f_number: float
    iso: float = 0.0


def _whitepoint_xyz_from_temperature(temperature: float) -> np.ndarray:
    """Convert a colour temperature to an XYZ whitepoint.

    Daylight whitepoints above 4000 K are better approximated by the CIE
    daylight locus than by a pure Planckian radiator. Warmer illuminants such as
    tungsten are modelled with the Kang 2002 Planckian approximation.
    """

    method = 'CIE Illuminant D Series' if temperature >= 4000.0 else 'Kang 2002'
    xy = colour.CCT_to_xy(np.float64(temperature), method=method)
    return np.asarray(colour.xy_to_XYZ(xy), dtype=np.float64)


def _apply_white_balance_adaptation(
    rgb: np.ndarray,
    source_white_xyz: np.ndarray,
    target_white_xyz: np.ndarray,
) -> np.ndarray:
    """Apply a colour-science chromatic adaptation in linear ACES RGB."""

    source_white_xyz = np.asarray(source_white_xyz, dtype=np.float64)
    target_white_xyz = np.asarray(target_white_xyz, dtype=np.float64)
    source_white_xyz = source_white_xyz / source_white_xyz[1]
    target_white_xyz = target_white_xyz / target_white_xyz[1]

    xyz = colour.RGB_to_XYZ(
        rgb,
        colourspace=_ACES_COLOURSPACE,
        chromatic_adaptation_transform=None,
        apply_cctf_decoding=False,
    )
    xyz = colour.chromatic_adaptation(
        xyz,
        source_white_xyz,
        target_white_xyz,
        method='Von Kries',
    )
    return colour.XYZ_to_RGB(
        xyz,
        colourspace=_ACES_COLOURSPACE,
        chromatic_adaptation_transform=None,
        apply_cctf_encoding=False,
    ).astype(np.float32)


def _apply_tint_adjustment(rgb: np.ndarray, tint: float | None) -> np.ndarray:
    """Apply a simple green-magenta tint adjustment in linear ACES RGB."""

    if tint is None or np.isclose(tint, 1.0):
        return rgb

    tint_scale = np.array([1.0, float(tint), 1.0], dtype=np.float32)
    return (rgb * tint_scale).astype(np.float32)


def _postprocess_params(
    white_balance: str | tuple[float, float],
    temperature: float | None,
    tint: float | None,
    recover: RecoverSettings | None = None,
) -> tuple[dict[str, object], tuple[np.ndarray, np.ndarray] | None, float | None]:
    """Build the ``rawpy.postprocess`` parameters for the requested settings.

    The output is always configured as linear 16-bit ACES RGB. White balance is
    handled in one of two ways:

    - ``'as_shot'`` uses LibRaw camera white balance directly during demosaic.
    - Other modes use LibRaw's daylight-balanced default output as the base and
      apply colour-science chromatic adaptation in linear ACES RGB.
    """

    params: dict[str, object] = {
        'output_color': getattr(rawpy, 'ColorSpace').ACES,
        'output_bps': 16,
        'no_auto_bright': True,
        'gamma': (1, 1),
    }
    if recover is not None:
        params.update(highlight_params(recover.highlights))
    postprocess_adaptation: tuple[np.ndarray, np.ndarray] | None = None
    tint_multiplier: float | None = None
    reference_white_xyz = _whitepoint_xyz_from_temperature(_DAYLIGHT_REFERENCE_TEMPERATURE)

    def set_colour_science_adjustment(target_temperature: float, target_tint: float | None) -> None:
        nonlocal postprocess_adaptation, tint_multiplier

        scene_white_xyz = _whitepoint_xyz_from_temperature(target_temperature)
        if not np.allclose(reference_white_xyz, scene_white_xyz):
            postprocess_adaptation = (scene_white_xyz, reference_white_xyz)
        tint_multiplier = target_tint

    if white_balance == 'as_shot':
        params['use_camera_wb'] = True
    elif white_balance == 'daylight':
        pass
    elif white_balance == 'tungsten':
        set_colour_science_adjustment(_TUNGSTEN_TEMPERATURE, 1.0)
    elif white_balance == 'custom':
        if temperature is None:
            raise ValueError('A custom raw white balance requires a temperature value.')
        set_colour_science_adjustment(temperature, tint)
    else:
        custom_temperature, custom_tint = white_balance
        set_colour_science_adjustment(custom_temperature, custom_tint)

    return params, postprocess_adaptation, tint_multiplier


def _read_exif_metadata(raw_path: str | PathLike[str]) -> ExifData:
    """Read basic EXIF data from a RAW file for lens correction.

    The function uses ``exiv2`` to extract standard EXIF tags for camera make
    and model, lens make and model, as well as focal length and aperture.

    Parameters
    ----------
    raw_path
        Path to the RAW image file.

    Returns:
    -------
    ExifData
        Frozen dataclass with camera and lens metadata. Fields that could not be
        read default to ``""`` for strings and ``0.0`` for numeric values.

    """
    try:
        image = exiv2.ImageFactory.open(str(raw_path))
        image.readMetadata()
        exif = image.exifData()
    except Exception:
        return ExifData(
            make="",
            model="",
            lens_make="",
            lens_model="",
            focal_length=0.0,
            f_number=0.0,
        )

    def _str(key: str) -> str:
        if key not in exif:
            return ""

        return str(exif[key].value()).strip()

    def _float(key: str) -> float:
        if key not in exif:
            return 0.0
        return exif[key].toFloat()

    return ExifData(
        make=_str("Exif.Image.Make"),
        model=_str("Exif.Image.Model"),
        lens_make=_str("Exif.Photo.LensMake"),
        lens_model=_str("Exif.Photo.LensModel"),
        focal_length=_float("Exif.Photo.FocalLength"),
        f_number=_float("Exif.Photo.FNumber"),
        iso=_float("Exif.Photo.ISOSpeedRatings") or _float("Exif.Photo.PhotographicSensitivity"),
    )


def _normalize_lens_text(value: object) -> str:
    """Normalize lens metadata strings for case-insensitive comparisons."""

    return ' '.join(str(value).strip().lower().split())


def _compact_lens_text(value: object) -> str:
    """Collapse lens metadata to alphanumeric characters for loose matching."""

    return ''.join(character.lower() for character in str(value) if character.isalnum())


def _lens_model_score(lens_model: object, exif_lens_model: str) -> tuple[int, int, int]:
    """Score how closely a database lens model matches the EXIF lens model."""

    normalized_model = _normalize_lens_text(exif_lens_model)
    compact_model = _compact_lens_text(exif_lens_model)
    normalized_lens_model = _normalize_lens_text(lens_model)
    compact_lens_model = _compact_lens_text(lens_model)
    return (
        int(normalized_lens_model == normalized_model),
        int(bool(compact_model) and compact_model in compact_lens_model),
        len(set(normalized_model.split()) & set(normalized_lens_model.split())),
    )


def _lens_matches_focal(lens: object, focal_length: float) -> bool:
    """Return whether the focal length falls within the lens calibration range."""

    min_focal = getattr(lens, "min_focal", None)
    max_focal = getattr(lens, "max_focal", None)
    if min_focal is None or max_focal is None or focal_length <= 0:
        return False
    return float(min_focal) <= focal_length <= float(max_focal)


def _lens_matches_aperture(lens: object, f_number: float) -> bool:
    """Return whether the aperture falls within the lens calibration range."""

    min_aperture = getattr(lens, "min_aperture", None)
    max_aperture = getattr(lens, "max_aperture", None)
    if min_aperture is None or f_number <= 0:
        return False
    upper_bound = float(max_aperture) if max_aperture is not None else float('inf')
    return float(min_aperture) <= f_number <= upper_bound


def _find_lens_candidates(db: object, camera: object, exif_metadata: ExifData) -> list[object]:
    """Find lensfun candidates with direct queries before a broad fallback search."""

    queries = [
        (exif_metadata.lens_make or None, exif_metadata.lens_model, False),
        (exif_metadata.lens_make or None, exif_metadata.lens_model, True),
        (None, exif_metadata.lens_model, True),
    ]

    def dedupe(lenses: list[object]) -> list[object]:
        candidates: list[object] = []
        seen: set[tuple[object, ...]] = set()
        for lens in lenses:
            identity = (
                _normalize_lens_text(getattr(lens, 'maker', '')),
                _normalize_lens_text(getattr(lens, 'model', '')),
                getattr(lens, 'min_focal', None),
                getattr(lens, 'max_focal', None),
                getattr(lens, 'min_aperture', None),
                getattr(lens, 'max_aperture', None),
            )
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(lens)
        return candidates

    for lens_make, lens_model, loose_search in queries:
        direct_candidates = dedupe(db.find_lenses(camera, lens_make, lens_model, loose_search=loose_search))
        if direct_candidates:
            return direct_candidates

    broader_candidates = dedupe(db.find_lenses(camera, None, None, loose_search=True))
    named_candidates = [
        lens for lens in broader_candidates
        if _lens_model_score(getattr(lens, 'model', ''), exif_metadata.lens_model) > (0, 0, 0)
    ]
    if named_candidates:
        return named_candidates
    # A fixed-lens camera has ONE lens in lensfun, named after the camera
    # ("LEICA Q2 & compatibles", used for the Q2 and the Q3), while the camera
    # writes its lens as "SUMMILUX 1:1.7/28 ASPH." or "28.0 mm f/1.7": no word
    # in common, so the test above drops the right profile and no correction
    # is applied. That one lens is the lens, when the focal length is in its
    # range. (A camera with interchangeable lenses lists every lens of its
    # mount; in lensfun's database only fixed-lens cameras have exactly one.)
    if len(broader_candidates) == 1 and _lens_matches_focal(broader_candidates[0], exif_metadata.focal_length):
        return broader_candidates
    return []


def _select_lens_candidate(lenses: list[object], exif_metadata: ExifData) -> object:
    """Pick the best lens candidate using model, maker, focal and aperture cues."""

    focal_matches = [lens for lens in lenses if _lens_matches_focal(lens, exif_metadata.focal_length)]
    candidates = focal_matches if focal_matches else lenses
    normalized_maker = _normalize_lens_text(exif_metadata.lens_make)

    def lens_score(lens: object) -> tuple[tuple[int, int, int], int, int, int, float]:
        return (
            _lens_model_score(getattr(lens, 'model', ''), exif_metadata.lens_model),
            int(bool(normalized_maker) and _normalize_lens_text(getattr(lens, 'maker', '')) == normalized_maker),
            int(_lens_matches_focal(lens, exif_metadata.focal_length)),
            int(_lens_matches_aperture(lens, exif_metadata.f_number)),
            float(getattr(lens, 'score', 0.0) or 0.0),
        )

    candidates.sort(key=lens_score, reverse=True)
    return candidates[0]


def _apply_lens_correction(
    rgb: np.ndarray,
    exif_metadata: ExifData,
) -> tuple[np.ndarray, str]:
    """Apply ``lensfun`` lens corrections to the image.

    Camera and lens are looked up in the ``lensfun`` database using the
    supplied EXIF metadata. If either the camera or lens is not found, no
    correction is applied and the original image is returned.

    The following corrections are applied in sequence, if supported by the
    identified lens: vignetting, chromatic aberration, distortion,
    geometry and scale.

    Parameters
    ----------
    rgb
        Linear ``float32`` RGB image array with shape ``(H, W, 3)``.
    exif_metadata
        Camera and lens metadata read from the RAW file.

    Returns:
    -------
    numpy.ndarray
        Corrected (or original) ``float32`` RGB image.
    str
        Human readable summary of the camera, lens, focal length and aperture
        used for the correction. Empty when the camera or lens was not found in
        the ``lensfun`` database.

    """
    if not exif_metadata.lens_model.strip():
        return rgb, ""

    db = lensfunpy.Database()
    cameras = db.find_cameras(exif_metadata.make, exif_metadata.model, loose_search=True)

    if not cameras:
        return rgb, ""

    camera = cameras[0]

    lenses = _find_lens_candidates(db, camera, exif_metadata)

    if not lenses:
        return rgb, ""

    lens = _select_lens_candidate(lenses, exif_metadata)
    lens_label = getattr(lens, 'model', None) or exif_metadata.lens_model or str(lens)
    lens_info = f"{lens_label} @ {exif_metadata.focal_length}mm f/{exif_metadata.f_number}"

    height, width = rgb.shape[:2]

    mod = lensfunpy.Modifier(lens, camera.crop_factor, width, height)
    mod.initialize(
        exif_metadata.focal_length,
        exif_metadata.f_number,
        pixel_format=np.float32,
        flags=lensfunpy.ModifyFlags.ALL,
    )

    mod.apply_color_modification(rgb)
    undist_coords = mod.apply_subpixel_geometry_distortion()

    if undist_coords is not None:
        corrected = np.empty_like(rgb)

        for c in range(rgb.shape[2]):
            corrected[:, :, c] = map_coordinates(
                rgb[:, :, c],
                [undist_coords[:, :, c, 1], undist_coords[:, :, c, 0]],
                order=1,
                mode="nearest",
            )

        rgb = corrected

    return rgb, lens_info


# Extensions LibRaw is expected to handle. Used ONLY to write a better error
# message (the decode itself never refuses a file on its name).
RAW_FILE_EXTENSIONS = frozenset({
    '.raf', '.nef', '.nrw', '.cr2', '.cr3', '.crw', '.dng', '.arw', '.srf',
    '.sr2', '.orf', '.rw2', '.pef', '.srw', '.kdc', '.dcr', '.3fr', '.iiq',
    '.erf', '.mef', '.mos', '.mrw', '.x3f', '.fff', '.rwl',
})


def _decode_raw(raw_path, params: dict, prepare=None) -> np.ndarray:
    """Demosaic ``raw_path`` to float32 linear RGB.

    ``prepare(raw)``, when given, may change the mosaic before it is developed.

    2026-07-25; see design/DECISIONS.md#d-raw_file_processor-100
    """
    try:
        with rawpy.imread(str(raw_path)) as raw:
            return _develop(raw, params, prepare)
    except rawpy.LibRawError:
        with open(raw_path, 'rb') as stream:
            with rawpy.imread(stream) as raw:
                return _develop(raw, params, prepare)


def _develop(raw, params: dict, prepare=None) -> np.ndarray:
    if prepare is not None:
        prepare(raw)
    rgb = raw.postprocess(**params).astype(np.float32) / np.float32(65535.0)
    gain = highlight_gain(raw, params)
    return rgb if gain == 1.0 else rgb * np.float32(gain)


def _unreadable_raw_message(raw_path, exc: Exception) -> str:
    """The user-facing failure, naming the file and calling out the obvious
    cause (an extension LibRaw never claims to read) instead of leaving the
    user to guess."""
    path = Path(str(raw_path))
    suffix = path.suffix.lower()
    if suffix and suffix not in RAW_FILE_EXTENSIONS:
        return (
            f"'{path.name}' is not a RAW file: '{suffix}' is not a camera RAW "
            "format. Use Import RGB for JPEG/TIFF/PNG images.")
    try:
        size = path.stat().st_size
    except OSError:
        size = -1
    if size == 0:
        return (f"'{path.name}' is empty (0 bytes). If it lives in a synced "
                "folder, it may not be downloaded to this machine yet.")
    detail = f"{size} bytes" if size >= 0 else "unreadable on disk"
    return (
        f"Not a readable RAW file ({exc}). File: '{path.name}' ({detail}). It "
        "may be corrupt, truncated, still syncing, or a format this build's "
        "LibRaw does not support.")


def load_and_process_raw_file(
    raw_path: str | PathLike[str],
    white_balance='as_shot',
    temperature: float | None = None,
    tint: float | None = None,
    lens_correction: bool = False,
    output_colorspace: str = "ACES2065-1",
    output_cctf_encoding: bool = False,
    lens_info_out: dict[str, str] | None = None,
    recover: RecoverSettings | None = None,
) -> np.ndarray:
    """Load a RAW file into linear RGB and optionally convert its colourspace.

    The RAW is demosaiced by ``rawpy`` into linear 16-bit ACES RGB with auto
    brightening disabled. ``'as_shot'`` white balance comes from the camera
    metadata; the other white-balance modes use LibRaw's daylight-balanced base
    output and colour-science chromatic adaptation in linear ACES RGB.

    Parameters
    ----------
    raw_path
        Path to the RAW image.
    white_balance
        White-balance mode. Supported string values are ``'as_shot'``,
        ``'daylight'``, ``'tungsten'``, and ``'custom'``. A
        ``(temperature, tint)`` tuple is also accepted.
    temperature
        Correlated colour temperature in kelvin for ``'custom'`` mode.
    tint
        Multiplicative adjustment applied to both green channels for
        temperature-derived white balance.
    lens_correction
        When ``True``, apply all lensfun lens corrections (vignetting,
        TCA, distortion, geometry and scale). Camera and lens are identified
        from the EXIF metadata.
    output_colorspace
        Output RGB colourspace name understood by ``colour.RGB_COLOURSPACES``.
    output_cctf_encoding
        Whether to apply the output colourspace transfer function when a
        colourspace conversion is requested.

    Returns
    -------
    numpy.ndarray
        RGB image as ``float32`` in the requested output colourspace.

    2026-08-30; see design/DECISIONS.md#d-raw_file_processor-101

    It is invisible in normal use because every caller decodes once and
    caches (`Controller._current_input_image`), so a session is internally
    consistent; it would surface only as "the photo looks slightly different
    after I reopened it".

    **Do not write a byte-identity test that re-decodes.** Pin the decoded
    array instead, or the test will flake. `OMP_NUM_THREADS=1` makes it exact
    at 5x the cost, and the VNG demosaic is deterministic with threads on,
    should this ever need revisiting; AHD (the default), DHT, AAHD, PPG and
    DCB are not. Full measurements in
    docs/reports/2026.08.29_random-edit-audit.md (B11).
    """

    # 2026-07-22; see design/DECISIONS.md#d-raw_file_processor-99
    if white_balance == 'as_shot':
        # as_shot is a DECODE-TIME property (LibRaw camera multipliers at
        # demosaic), so it cannot ride the daylight-base + live-WB split.
        params, postprocess_adaptation, tint_multiplier = _postprocess_params(
            white_balance, temperature, tint, recover)
        rgb = _decode_recovered(raw_path, params, recover)
        rgb = _remove_dust(rgb, recover)
        rgb = _lens_correct(rgb, raw_path, lens_correction, lens_info_out)
        rgb = _adjust_lens(rgb, recover)
        if postprocess_adaptation is not None:
            rgb = _apply_white_balance_adaptation(rgb, *postprocess_adaptation)
        rgb = _apply_tint_adjustment(rgb, tint_multiplier)
        return _convert_output_colorspace(
            rgb, output_colorspace, output_cctf_encoding)

    # All other modes share the LibRaw daylight-balanced base decode; white
    # balance is pure post-demosaic math (Phase 17A: the live-WB step runs
    # exactly this tail on the same base, so the split is byte-identical by
    # construction).
    rgb = load_raw_daylight_base(
        raw_path,
        lens_correction=lens_correction,
        lens_info_out=lens_info_out,
        recover=recover,
    )
    return apply_raw_white_balance(
        rgb,
        white_balance=white_balance,
        temperature=temperature,
        tint=tint,
        output_colorspace=output_colorspace,
        output_cctf_encoding=output_cctf_encoding,
    )


def load_raw_daylight_base(
    raw_path: str | PathLike[str],
    *,
    lens_correction: bool = False,
    lens_info_out: dict[str, str] | None = None,
    recover: RecoverSettings | None = None,
) -> np.ndarray:
    """Decode a RAW to the LibRaw daylight-balanced linear ACES2065-1 base.

    This is the shared decode for every white-balance mode except
    ``'as_shot'`` (which needs camera multipliers at demosaic). Lens
    correction is part of the base because it is geometry, not colour.
    White balance, tint and output conversion are applied separately by
    :func:`apply_raw_white_balance`, which lets the GUI keep ONE stored
    base and re-balance live without re-decoding.
    """

    params, _, _ = _postprocess_params('daylight', None, None, recover)
    rgb = _decode_recovered(raw_path, params, recover)
    rgb = _remove_dust(rgb, recover)
    rgb = _lens_correct(rgb, raw_path, lens_correction, lens_info_out)
    return _adjust_lens(rgb, recover)


def apply_raw_white_balance(
    rgb_aces_base: np.ndarray,
    *,
    white_balance='daylight',
    temperature: float | None = None,
    tint: float | None = None,
    output_colorspace: str = "ACES2065-1",
    output_cctf_encoding: bool = False,
) -> np.ndarray:
    """White-balance a daylight-base decode: the tail of the RAW loader.

    Applies the Von Kries chromatic adaptation and tint for ``white_balance``
    in linear ACES on ``rgb_aces_base`` (a :func:`load_raw_daylight_base`
    output), then converts to the requested output colourspace. Running this
    on a stored base is byte-identical to a full
    :func:`load_and_process_raw_file` with the same arguments.

    ``'as_shot'`` is rejected: it is a decode-time property and cannot be
    derived from the daylight base.
    """

    if white_balance == 'as_shot':
        raise ValueError(
            "as_shot white balance is decoded from camera metadata and "
            "cannot be applied to a daylight base; reprocess the RAW instead.")
    _, postprocess_adaptation, tint_multiplier = _postprocess_params(
        white_balance, temperature, tint)
    if postprocess_adaptation is not None:
        rgb_aces_base = _apply_white_balance_adaptation(
            rgb_aces_base, *postprocess_adaptation)
    rgb_aces_base = _apply_tint_adjustment(rgb_aces_base, tint_multiplier)
    return _convert_output_colorspace(
        rgb_aces_base, output_colorspace, output_cctf_encoding)


def solve_neutral_white_balance(
    rgb_daylight_base,
    *,
    minimum: float = 1700.0,
    maximum: float = 12000.0,
    iterations: int = 40,
) -> tuple[float, float]:
    """The (temperature, tint) that make ``rgb_daylight_base`` neutral.

    The inverse of the custom white balance, for the eyedropper: you point
    at something that should be grey and this says what to set.

    It solves in two steps because the forward transform separates cleanly.
    ``_apply_tint_adjustment`` is a PURE GREEN MULTIPLIER -- ``rgb * [1, t,
    1]`` -- so tint cannot touch the red:blue ratio at all, and the
    adaptation is the only thing that can. So:

      1. bisect the temperature until the adapted red equals the adapted
         blue. Monotone over the range, because a warmer assumed illuminant
         can only push the result bluer.
      2. take the tint straight from what is left: the green multiplier that
         puts green on the red/blue level. No search needed.

    Returns the pair clamped to the panel's own ranges (1700-12000 K, the
    floor the first 100 K step above the CCT conversion's 1667,
    tint 0-5). ``rgb_daylight_base`` is a linear ACES RGB triple from
    :func:`load_raw_daylight_base` -- the UNBALANCED decode, which is what
    the daylight-base path keeps around; handing this a balanced pixel asks
    it to neutralise something already neutral and it will say 6504 K.
    """
    rgb = np.asarray(rgb_daylight_base, dtype=np.float64).reshape(1, 1, 3)
    if not np.all(np.isfinite(rgb)) or np.max(rgb) <= 0.0:
        return float(_DAYLIGHT_REFERENCE_TEMPERATURE), 1.0

    reference_white = _whitepoint_xyz_from_temperature(
        _DAYLIGHT_REFERENCE_TEMPERATURE)

    def adapted(temperature: float) -> np.ndarray:
        scene_white = _whitepoint_xyz_from_temperature(temperature)
        if np.allclose(reference_white, scene_white):
            return rgb
        return np.asarray(
            _apply_white_balance_adaptation(rgb, scene_white, reference_white),
            dtype=np.float64)

    def red_minus_blue(temperature: float) -> float:
        out = adapted(temperature).reshape(3)
        return float(out[0] - out[2])

    low, high = float(minimum), float(maximum)
    f_low, f_high = red_minus_blue(low), red_minus_blue(high)
    if f_low == 0.0:
        temperature = low
    elif f_high == 0.0:
        temperature = high
    elif f_low * f_high > 0.0:
        # NO CROSSING IN RANGE. The sample is more coloured than any
        # illuminant in the panel can explain -- a saturated object rather
        # than a grey one. Answer with the end that gets closest instead of
        # pretending, so the pad opens somewhere sane and the user can see
        # what happened.
        temperature = low if abs(f_low) < abs(f_high) else high
    else:
        for _ in range(int(iterations)):
            middle = 0.5 * (low + high)
            if red_minus_blue(low) * red_minus_blue(middle) <= 0.0:
                high = middle
            else:
                low = middle
        temperature = 0.5 * (low + high)

    out = adapted(temperature).reshape(3)
    target = 0.5 * (float(out[0]) + float(out[2]))
    green = float(out[1])
    tint = 1.0 if green <= 0.0 else target / green
    return (float(min(max(temperature, minimum), maximum)),
            float(min(max(tint, 0.0), 5.0)))


def _decode_with_readable_error(raw_path, params: dict, prepare=None) -> np.ndarray:
    try:
        return _decode_raw(raw_path, params, prepare)
    except rawpy.LibRawError as exc:
        raise ValueError(_unreadable_raw_message(raw_path, exc)) from exc


def _decode_recovered(raw_path, params: dict, recover: RecoverSettings | None) -> np.ndarray:
    """The decode, with this frame's cached denoise blended in at the chosen amount."""
    backend = recover.denoise_backend if recover is not None else ''
    prepare = None
    if backend in ('clean', 'deep'):
        def prepare(raw):
            blend_mosaic(raw, raw_path, backend, recover.denoise_amount)
    rgb = _decode_with_readable_error(raw_path, params, prepare)
    if backend == 'sharp':
        rgb = blend_linear(rgb, raw_path, recover.highlights, bool(params.get('use_camera_wb')),
                           recover.denoise_amount)
    return rgb


def decode_recover_base(raw_path, *, as_shot: bool, highlights: str) -> np.ndarray:
    """The decode Sharp denoises: no lens work, no cached denoise."""
    params, _, _ = _postprocess_params('as_shot' if as_shot else 'daylight', None, None,
                                       RecoverSettings(highlights=highlights))
    return _decode_with_readable_error(raw_path, params)


def _lens_correct(
    rgb: np.ndarray,
    raw_path,
    lens_correction: bool,
    lens_info_out: dict[str, str] | None,
) -> np.ndarray:
    if not lens_correction:
        return rgb
    exif_metadata = _read_exif_metadata(raw_path)
    rgb, lens_info = _apply_lens_correction(rgb, exif_metadata)
    if lens_info_out is not None and lens_info:
        lens_info_out["summary"] = lens_info
    return rgb


def read_exif_metadata(raw_path: str | PathLike[str]) -> ExifData:
    """The frame's camera, lens and ISO, for the callers outside this module."""
    return _read_exif_metadata(raw_path)


def _remove_dust(rgb: np.ndarray, recover: RecoverSettings | None) -> np.ndarray:
    """Sensor dust sits in sensor coordinates, so it goes before any geometry."""
    plan = recover.defects if recover is not None else None
    spots = json.loads(plan.spots) if plan is not None else []
    if not spots:
        return rgb
    soft = SOFT_FRACTION * max(rgb.shape[:2])
    with DustSession(rgb) as session:
        prepared = session.prepare(spots, plan.method, soft)
        return session.correct(prepared, plan.method, plan.strength, soft=soft)


def _adjust_lens(rgb: np.ndarray, recover: RecoverSettings | None) -> np.ndarray:
    if recover is None:
        return rgb
    return apply_lens_adjust(rgb, recover.lens_barrel, recover.lens_vignette)


def _convert_output_colorspace(
    rgb: np.ndarray,
    output_colorspace: str,
    output_cctf_encoding: bool,
) -> np.ndarray:
    if output_colorspace == 'ACES2065-1':
        return rgb
    return colour.RGB_to_RGB(
        rgb,
        input_colourspace=_ACES_COLOURSPACE,
        output_colourspace=colour.RGB_COLOURSPACES[output_colorspace],
        apply_cctf_decoding=False,
        apply_cctf_encoding=output_cctf_encoding,
    )


__all__ = [
    'load_and_process_raw_file',
    'load_raw_daylight_base',
    'decode_recover_base',
    'read_exif_metadata',
    'apply_raw_white_balance',
]