# Fix: a turned photo shows stretched edge pixels in its corners

Target files (The Dyeworks 2.2.1): `src/spektrafilm/utils/crop_resize.py`, `src/spektrafilm/gpu/scan_geometry.py` and `src/spektrafilm/gpu/shaders/scan_geometry.wgsl`, plus two text-only edits in `src/spektrafilm_gui/crop_frame.py` (a comment) and `widget_specs.py` (the Straighten tooltip). Each file here is the full patched module, `crop_white_corners.diff` the change, and `test_white_corners.py` the checks below.

## Symptom

1. Open a photo, then Scan › Frame › Straighten: type `10` and press Enter.
2. The photo turns. The four corners that are not part of the picture fill with the picture's edge pixels dragged outward: streaks of stretched photo.

They are in the export too, unless a crop cuts them away. In 2.2.1 a typed angle (or the slider) never adds a crop; only the canvas turn and the straighten line fit one.

## Cause

`apply_skew` turns the scan with `skimage.transform.rotate(..., mode='edge')`, which gives every sample outside the picture the nearest edge pixel. The GPU skew shader matches it by clamping its indices to the edge (`spx` in `scan_geometry.wgsl`). The docstring says the corners "are meant to be cut away by the crop", but nothing makes sure a crop is on.

## Fix

The corners are filled with white, the way Lightroom shows them (the paper around a print):

- `crop_resize.SKEW_FILL = 1.0`, and `apply_skew` uses `mode='constant', cval=SKEW_FILL`.
- In the shader, `spx` returns `SKEW_FILL` for a neighbour outside the picture instead of clamping it. The CPU and GPU paths still agree.
- Inside the picture nothing changes: it is the same bilinear turn, and only samples that read outside the picture differ. So only the one-pixel border where the picture meets the corner blends with white instead of with itself. `ROTATED_FIT_INSET_PX` (2 px) already keeps a fitted crop clear of that border, so fitted crops come out exactly as before.
- Text only:
  - the Straighten tooltip said "Edges replicate; crop away the corners" and now says "The corners outside the photo are white; crop them away";
  - the `ROTATED_FIT_INSET_PX` comment now says the border blends with the fill.

## Verification

`python test_white_corners.py <src dir> <out dir> <raw> [--baseline]` drives the real `GuiController` headless (`QT_QPA_PLATFORM=offscreen`, a throwaway settings file). It types the angle into the Straighten field as a user does and renders the preview. It then looks at every preview pixel outside the turned picture, found exactly by turning an all-ones mask the way the engine turns the photo. `--baseline` against 2.2.1 reports the same count.

| Check | 2.2.1 | patched |
|---|---|---|
| Preview pixels outside the turned picture at 10° that are white (2 - Nikon D7100.NEF) | 0 of 55,628 (all smeared) | 55,628 of 55,628 |
| CPU `apply_skew`: the corners are `SKEW_FILL` | (edge pixels) | pass |
| GPU skew: the same, and it matches the CPU | (edge pixels) | pass (max diff 1.7e-7) |
| Inside the picture the turn is unchanged (against `mode='edge'`) | n/a | pass (exact) |

Also tested by hand in the app, together with the other two crop changes: `patches/crop_frame_drag` (independent of this one) and `patches/crop_straighten_session` (built on both).
