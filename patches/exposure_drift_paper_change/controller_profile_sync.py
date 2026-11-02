# -------------------------------------------------------------------------
# FORK CONTRACT -- upstream has this file too, and both sides edit it.
# OURS: PROFILE_SYNC_FIELDS, per-FIELD rather than upstream's per-section
#   list, so a stock switch overwrites only stock-owned values; 'special' is
#   excluded on purpose (it compounded push/pull gamma, 2026-07-12), and
#   so is simulation.exposure_compensation_ev (it compounded Film exposure);
#   grain.rms_granularity added in 20C; Phase 15 update_baselines.
# UPSTREAM: the idea: sync widgets to the chosen profile's defaults.
# ON CONFLICT: KEEP OURS. Map any section upstream adds to
#   PROFILE_SYNC_SECTION_NAMES onto explicit field names here, and keep
#   test_every_selection_default_is_covered_by_the_profile_sync green.
# Convention and rationale: docs/reports/2026.08.20_fork_contracts.md
# -------------------------------------------------------------------------

from __future__ import annotations

from typing import Any, Mapping


PROFILE_SYNC_FIELDS: dict[str, tuple[str, ...]] = {
    'input_image': (
        'upscale_factor',
        'crop',
        'crop_center',
        'crop_size',
        'input_color_space',
        'apply_cctf_decoding',
        'spectral_upsampling_method',
        'apply_hanatos2025_adaptation_window',
        'apply_hanatos2025_adaptation_surface',
        'spectral_gaussian_blur',
        'filter_uv',
        'filter_ir',
        'color_filter',
    ),
    'grain': (
        'active',
        'sublayers_active',
        'particle_area_um2',
        'particle_scale',
        'particle_scale_sublayers',
        'density_min',
        'uniformity',
        'blur',
        'blur_dye_clouds_um',
        'micro_structure',
        # 20C: upstream's data/presets/grain.toml sets this PER STOCK, so it is
        # a selection default like the rest of this tuple. Without it here the
        # value is applied at selection and then dropped on the way into the GUI
        # state, which is authoritative from that point -- so it would silently
        # vanish from every export. Caught by
        # test_every_selection_default_is_covered_by_the_profile_sync.
        'rms_granularity',
    ),
    'preflashing': (
        'exposure',
        'y_filter_shift',
        'm_filter_shift',
        'c_filter_shift',
    ),
    'halation': (
        'active',
        'scatter_amount',
        'scatter_spatial_scale',
        'halation_amount',
        'halation_spatial_scale',
        'boost_ev',
        'protect_ev',
        'boost_range',
        'scatter_core_um',
        'scatter_tail_um',
        'scatter_tail_weight',
        'halation_strength',
        'halation_first_sigma_um',
        'halation_n_bounces',
        'halation_bounce_decay',
        'halation_renormalize',
    ),
    'couplers': (
        'active',
        'amount',
        'inhibition_samelayer',
        'inhibition_interlayer',
        'gamma_samelayer_rgb',
        'gamma_interlayer_r_to_gb',
        'gamma_interlayer_g_to_rb',
        'gamma_interlayer_b_to_rg',
        'diffusion_size_um',
    ),
    'glare': (
        'active',
        'percent',
        'roughness',
        'blur',
    ),
    # 'special' (the Tune gammas) is deliberately NOT synced: they are USER
    # knobs, and gui_state_from_params reads film_gamma_factor back from
    # params.film_render.density_curve_gamma AFTER push/pull multiplied its
    # stops into it — syncing that composed value re-fed it into the widget,
    # so every film-profile switch COMPOUNDED the gamma by 1.2**stops (NAT's
    # 'profile change stacks' report, 2026-07-12); print_gamma_factor was
    # hard-reset to 1.0 by the same path. A profile switch now leaves both
    # exactly as the user set them.
    #
    # 'exposure_compensation_ev' (Camera exposure) is left out for the same
    # reason: _apply_camera composes it with Film exposure into
    # params.camera.exposure_compensation_ev, and gui_state_from_params reads
    # that SUM back, so every switch added Film exposure to Camera exposure
    # again (-1, -2, -3 ... with Film exposure at -1; a paper change always,
    # a film change whenever Camera exposure was unedited). No stock sets it,
    # so it is a user knob, not a selection default.
    'simulation': (
        'film_stock',
        'film_format_mm',
        'camera_lens_blur_um',
        'auto_exposure',
        'auto_exposure_method',
        'print_paper',
        'print_illuminant',
        'print_exposure',
        'print_exposure_compensation',
        'print_y_filter_shift',
        'print_m_filter_shift',
        'print_c_filter_shift',
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
        'scan_lens_blur',
        'scan_white_correction',
        'scan_white_level',
        'scan_black_correction',
        'scan_black_level',
        'scan_unsharp_mask',
        'scan_film',
    ),
}


def apply_profile_sync_state(
    *,
    widgets: Any,
    synced_state: Any,
    profile_sync_fields: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    if profile_sync_fields is None:
        profile_sync_fields = PROFILE_SYNC_FIELDS
    for section_name, field_names in profile_sync_fields.items():
        section_widget = getattr(widgets, section_name)
        section_state = getattr(synced_state, section_name)
        for field_name in field_names:
            field_value = getattr(section_state, field_name)
            if section_name == 'simulation' and field_name == 'scan_film':
                section_widget.set_scan_film_value(field_value)
                continue
            getattr(section_widget, field_name).value = field_value
        # Phase 15: profile-synced fields RESET to the stock's own values —
        # refresh their baselines with every sync.
        update_baselines = getattr(section_widget, 'update_baselines', None)
        if callable(update_baselines):
            update_baselines(section_state, field_names)


def update_sync_baselines(
    *,
    widgets: Any,
    stock_state: Any,
    profile_sync_fields: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    """Point every synced row's reset target at the stock's own values, not the carried edit."""
    if profile_sync_fields is None:
        profile_sync_fields = PROFILE_SYNC_FIELDS
    for section_name, field_names in profile_sync_fields.items():
        section_widget = getattr(widgets, section_name, None)
        section_state = getattr(stock_state, section_name, None)
        update_baselines = getattr(section_widget, 'update_baselines', None)
        if section_state is not None and callable(update_baselines):
            update_baselines(section_state, field_names)
