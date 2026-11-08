# Fix: a positive Barrel smears the photo's edges

Target files (The Dyeworks 2.2.1): `src/spektrafilm/utils/raw_recover.py` and `src/spektrafilm/utils/dust_geometry.py`. Each `.py` here is the full patched module, `barrel_corner_smear.diff` the change, and `test_barrel_smear.py` the checks below.

## Symptom

1. Open a raw.
2. Reco › Lens correction: set **Barrel** to a positive value (the pincushion direction), e.g. `50`.

Streaks of stretched edge pixels appear down both sides and in the corners; at `100` they run along every edge. Share of the frame that is smeared (3:2 frame):

| Barrel | smeared |
|---|---|
| −100 … 0 | 0% |
| +25 | 8.8% |
| +50 | 15.2% |
| +100 | 24.3% |

It is the same artefact as the Straighten corners (`patches/crop_white_corners`), here in the raw decode.

## Cause

`_lens_adjust` builds each pixel from the photo at radius × (1 + k·r²), r = 1 at the corner, k = `BARREL_AT_100` × Barrel / 100.

- A positive Barrel (k > 0) reads further out than the pixel itself. Near the edges that point is outside the photo.
- The kernel then clamps it to the photo (`sx = min(max(sx, 0.0), width - 1.0)`), so every such pixel reads the edge pixel, and the edge is repeated outward as streaks.
- A negative Barrel always reads inside the photo, so it never smears.

## Fix

A positive Barrel is fitted to the frame, the way the automatic Lens correction already is (lensfun's auto-scale):

- `raw_recover.barrel_fit(k)` = 1 / (1 + k) for k > 0, otherwise 1. The kernel reads at radius × (1 + k·r²) × fit.
- The corners (r = 1) now read the photo's corners, and every other pixel reads inside it. Nothing is read from outside, so nothing is smeared, and no fill colour is needed.
- It is the same curve, with the whole picture scaled to fit: the middle is enlarged by 1 + k relative to 2.2.1 (about 7% at +25, 15% at +50, 30% at +100), while the corners stay where they were. A negative Barrel is unchanged.
- `dust_geometry.barrel_backward` / `barrel_forward` use the same fit, so Reco's dust spots stay on the dust.

Why not fill the corners with white, as the Straighten fix does: Barrel runs on the scene-linear decode, before the film simulation. A "white" there is a scene highlight that the film and paper render as a blown, possibly tinted, highlight, not as paper white.

## A quick fix; a fuller one to follow

This removes the smear with a small, contained change, but it isn't the last word on how a positive Barrel should behave:

- Filling the frame means the middle of the photo grows as Barrel goes up (30% at +100), and it is resampled to do so.
- Lightroom's default for a manual distortion correction keeps the frame and the centre where they are, shows the empty edges as white, and offers Constrain Crop to crop them away. Doing that here means carrying the empty area past the film simulation, so it can be painted white after the scan; a white fill in the decode would render as a blown, haloed highlight and sway auto exposure.
- A crop instead of a resample would keep full sharpness, at the cost of the decoded image changing size with Barrel.

That fuller version is planned as a separate change. Until then this one stops the edges from smearing.

## Verification

`python test_barrel_smear.py <src dir> <out dir> <raw> [--baseline]` checks the remap with a "where did it read" image (each pixel holds its own x and y), checks the dust geometry, then drives the real `GuiController` headless (`QT_QPA_PLATFORM=offscreen`, a throwaway settings file) on `2 - Nikon D7100.NEF`, the CC0 raw the other patches use.

| Check | 2.2.1 | patched |
|---|---|---|
| Barrel +25: no pixel reads the clamped edge | fail (8.8% of the frame) | pass (0%) |
| Barrel +50: the same | fail (15.2%) | pass (0%) |
| Barrel +100: the same | fail (24.3%) | pass (0%) |
| Barrel −50: reads exactly where 2.2.1 does | pass | pass |
| Barrel +100: the dust geometry matches the remap | fail (40 of 117 points read outside the photo) | pass |
| The app, Barrel +100: the decode is the photo remapped by the fitted curve, edges included | fail (edges 38% off) | pass (0.0%) |

6 checks: 2.2.1 fails 5, the patched build passes all 6. Also checked in the running app at Barrel 0, +50 and +100: no streaks at any value.
