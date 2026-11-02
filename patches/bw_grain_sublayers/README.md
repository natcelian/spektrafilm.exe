# Fix: grain shifts the tonality of B&W stocks (Tri-X, Double-X)

Target file: `src/spektrafilm/runtime/pipeline.py` (The Dyeworks 2.2.1).

| file | what it is |
|---|---|
| `pipeline.py` | the full patched module |
| `pipeline.py.diff` | the change against the 2.2.1 file |
| `verify_fix.py` | the checks below |
| `fix_installed_profiles.py` | the data-only alternative, usable on an installed build |
| `raw_comparison/` | the scripts behind the raw-image numbers |

## Symptom

With Kodak Tri-X (and, less strongly, Double-X), a Scan or export with grain on has a very different tonality from the preview. The preview strips grain (`settings.preview_mode`), so it never shows the shift, and the export does not look like what was graded. With grain off, Scan and preview match.

`verify_fix.py` on its test image (`img/readme/banner.jpg`), Tri-X scanned directly (the route the app uses for this reversal stock in the Print view), 1280 px:

| render | mean |
|---|---|
| preview (grain stripped) | 0.318 |
| grain on, 2.2.1 | 0.184 |
| grain on, with this patch | 0.316 |

The shift grows with the grain `amount`. Its direction depends on the route: printed onto 2302, Tri-X goes lighter instead (0.724 → 0.896).

## Cause

The grain splits each pixel's density over the sub-layer curves (`interp_density_cmy_layers_channel`), grains each sub-layer and sums them back, so the sub-layers must add up to the total curve. For every colour stock they do (within 0.0052). For the B&W stocks the shipped `density_curves_layers` arrays are stale: they do not sum to the shipped `density_curves`, and they do not match the stock's own `density_curves_model`, which reproduces the shipped total exactly.

| stock | max abs(sum(sub-layers) − total), shipped | sub-layers regenerated from the model |
|---|---|---|
| kodak_trix | 0.886 | 0.000 |
| kodak_doublex | 0.10 to 0.21 across development times | ≤ 0.0003 |
| kodak_2302, selected as a film | 0.08 to 0.73 across development times | ≤ 0.0003 |
| every colour stock | ≤ 0.0052 | not touched |

Turning off only the sub-layers (`sublayers_active=False`) with grain on removes the shift, so nothing else in `preview_mode` explains it.

Upstream does not show the bug: its `SimulationPipeline` calls `refresh_density_curves_from_model` unconditionally, which regenerates both arrays. This fork deliberately skips that to keep the shipped arrays byte-identical, and only regenerates under the chemistry morph, which is why push/pull renders of Tri-X were already right.

## Fix

`_reconcile_grain_sublayers` runs once in `SimulationPipeline.__init__`, after the development-time selection and before the chemistry and expiration bakes. It regenerates only `density_curves_layers`, from `density_curves_model`, and only when:

- the shipped sub-layers miss the total by more than `_SUBLAYER_SUM_TOLERANCE` (0.01 density);
- the model's sub-layers are within that tolerance and closer than the shipped ones.

In every other case it changes nothing. That includes no sub-layers, no multi-layer model, a model that cannot be evaluated, or a total the model does not describe. So the total curve and every colour stock pass through byte-identical, and so do custom `.sfstock` stocks, whose sub-layers come from their own fitted model.

Fog-free expiration of the B&W stocks is fixed as well. For Tri-X at 640 px:

| expiration setting | preview | grain-on render, 2.2.1 | grain-on render, patched |
|---|---|---|---|
| speed loss 1 EV | 0.740 | 0.895 | 0.739 |
| dye density 0.8 | 0.698 | 0.872 | 0.698 |

## Effect on the grain itself

The default `rms` grain sizes its particles from the sub-layers, so B&W grain changes too, not just the tone. `raw_comparison/` renders a CC0 raw from raw.pixls.us at full resolution (24 MP) with the same grain seed for all grain-on renders:

- **Raw:** Nikon D7100, [`Nikon - D7100 - 14bit 14bit compressed (Lossless) (3:2).NEF`](https://raw.pixls.us/getfile.php/1855/nice/Nikon%20-%20D7100%20-%2014bit%2014bit%20compressed%20(Lossless)%20(3:2).NEF).
- **sha256:** `b241cb95b1008f25b8ed7d1443354e8cc7dee9c4d4e1edd0a5856d92063a48d0`
- **Look:** Tri-X scanned directly, with the scan finishing set in `render_raw.py`.

Whole-image mean: grain off 0.304, grain on 2.2.1 0.181, grain on patched 0.305. By the reference (grain-off) tone, patched is within ±0.011 everywhere:

| reference tone band | mean shift, 2.2.1 | mean shift, patched | grain RMS, 2.2.1 | grain RMS, patched |
|---|---|---|---|---|
| 0.1–0.2 | −0.079 | −0.007 | 0.019 | 0.022 |
| 0.2–0.3 | −0.139 | −0.006 | 0.016 | 0.020 |
| 0.5–0.6 | −0.224 | −0.001 | 0.007 | 0.007 |
| 0.7–0.8 | −0.159 | −0.000 | 0.021 | 0.004 |
| 0.8–0.9 | −0.113 | −0.000 | 0.009 | 0.004 |

Grain strength is unchanged in the midtones, a little higher in the shadows, and much lower in the highlights. Part of the highlight change is that 2.2.1 pushed the highlights down into tones where grain shows more. The grain-RMS figures for 2.2.1 are inflated somewhat by the tone shift varying within each band.

A rough size measure (median frequency of the grain noise spectrum in one 384 px crop) moves by 10 to 20 %: shadows 1.5 → 1.2 px, midtones 2.0 → 2.3 px, highlights 3.3 → 2.9 px. At 400 % the midtone grain looks much the same.

## Data-only alternative, and a workaround for installed builds

`fix_installed_profiles.py` makes the same correction in the data instead. It regenerates `density_curves_layers` in the three B&W profile JSONs from their own `density_curves_model` and leaves everything else in the files byte-identical. The app reads its profiles from disk, so users can run it on an installed 2.2.1 without a rebuild. It is standalone (Python 3 and numpy only) and its model evaluation matches `evaluate_density_curves_layers` to within 1e-15:

```
python fix_installed_profiles.py "<...>\TheDyeworks\_internal"            # dry run
python fix_installed_profiles.py "<...>\TheDyeworks\_internal" --apply    # writes, keeps .orig backups
python fix_installed_profiles.py "<...>\TheDyeworks\_internal" --restore  # undo
```

With only this applied to the unpatched 2.2.1 code, `verify_fix.py` passes everything the runtime patch passes. If you prefer to fix the data rather than the code, the same regenerated arrays can go straight into the repository's profiles.

## Not fixed here: fog

`model/develop.py` hands the grain `density_curves - nanmin(density_curves)`, but the sub-layers are not offset the same way. That is harmless for the shipped stocks, whose minimum is about 0. Anything that lifts the toe brings a smaller grain shift back, on colour stocks too (640 px, preview vs grain-on render):

- Expiration fog: Tri-X with fog 0.1 gives 0.653 vs 0.683.
- Expiration aging: Portra 400 aged 10 years gives 0.235 vs 0.262.
- Custom stocks with fog: `_apply_fog` lifts the total after fitting, but the sub-layers come from the fog-free model.

Offsetting the sub-layers the same way in `develop()`, or scaling them to the normalised total, would cover this. It touches every stock's grain path, so it is left to the maintainer. `verify_fix.py` prints these cases as known issues and does not fail on them.

## Verification

`python -I verify_fix.py <engine dir> <image> [--quick]`, with the engine dir holding the `spektrafilm` and `spektrafilm_gui` packages. Every check must pass; a check that cannot run raises and fails the script. The script refuses to run if the imported `spektrafilm` is not the one in the engine dir. It checks:

- every shipped colour stock's curves are byte-identical, the caller's params are not mutated, and all three B&W stocks are checked: their sub-layers sum to the total and their total curve is unchanged;
- grain-on render against the grain-free preview, at 1280 px and 6000 px (upscaled when the image is smaller), for:
  - Tri-X on 2302, printed;
  - Tri-X scanned directly, with the default `rms` grain and with the legacy `production` grain;
  - Tri-X on Portra Endura;
  - Double-X on 2302;
  - Portra 400 as a control;
- Tri-X with push/pull active, and with fog-free expiration (speed loss, dye density).

On the patched engine all checks pass; on the unpatched 2.2.1 engine the B&W checks fail. The DIR-coupler "inversion has folded" warnings the script prints come from the curves themselves and do not depend on this patch.

Not covered: the wgpu GPU path, which could not run in the test environment. It reads the same `density_curves_layers` from the film data (`gpu/develop_grain_resident.py`), so it receives the same correction, but a GPU-side check is welcome.
