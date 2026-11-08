# Fix: Lens correction does nothing on a Leica Q, Q2 or Q3

Target file: `src/spektrafilm/utils/raw_file_processor.py` (The Dyeworks 2.2.1). `raw_file_processor.py` here is the full patched module, `raw_file_processor.py.diff` the change, and `test_fixed_lens.py` the checks below.

## Symptom

Reported on the pixls.us thread ([post #63](https://discuss.pixls.us/t/the-dyeworks-gpu-accelerated-windows-port-of-spektrafilm/59447/63)): "Lens correction didn't seem to work with my Leica Q3."

1. Open a raw from a Leica Q, Q2 or Q3.
2. Reco › Lens correction: switch **Lens correction** on.

The photo doesn't change, and the status line says "Loaded raw, lens correction not applied". These cameras' lens has strong barrel distortion that is meant to be corrected in software, so the uncorrected picture is visibly bowed.

## Cause

lensfun has the camera and its lens, but the lens lookup throws the lens away.

- A fixed-lens camera has ONE lens in lensfun, named after the camera: `LEICA Q (Typ 116) & compatibles` for the Q, and `LEICA Q2 & compatibles` for the Q2 and the Q3.
- The camera writes its lens as `28.0 mm f/1.7` (Q, Q2) or `SUMMILUX 1:1.7/28 ASPH.` (Q3).
- `_find_lens_candidates` first searches by that name, which finds nothing. Its fallback takes every lens for the camera, but keeps only lenses whose name shares at least one word with the EXIF lens name (`_lens_model_score > (0, 0, 0)`).
- No word is shared, so the camera's one lens is dropped, and `_apply_lens_correction` returns the picture uncorrected.

## Fix

In `_find_lens_candidates`, when the name filter leaves nothing and the camera has exactly one lens in lensfun, that lens is used, provided the photo's focal length is in its range (`_lens_matches_focal`, already in the file).

- Every name match works exactly as before; the new rule only runs where 2.2.1 returned no lens at all.
- A camera with interchangeable lenses lists every lens of its mount, so the rule cannot reach it: the Nikon D7100 lists 353, the Sony A7 III 120. Across the whole lensfun database the app uses (948 cameras), 332 cameras have exactly one lens, and all of them are fixed-lens cameras. The only one on an interchangeable-lens mount is the Rolleiflex 2.8E, a twin-lens camera whose taking lens is fixed too.
- `dust_geometry.lens_map` uses the same lookup, so Reco's dust spots line up with the corrected picture on these cameras too.

## Verification

`python test_fixed_lens.py <src dir> <out dir> --fixed <raw> ... --other <raw> ... [--baseline]` checks the engine's lookup and correction for each raw, then drives the real `GuiController` headless (`QT_QPA_PLATFORM=offscreen`, a throwaway settings file) with Lens correction switched on.

Raws: the Leica Q (Typ 116) and Q2 are CC0 samples from raw.pixls.us. The Q3 was checked with a review site's sample file, which can't be redistributed. The Nikon D7100 and Sony A7 III are the CC0 raws the other patches use.

| Check | 2.2.1 | patched |
|---|---|---|
| Leica Q: the lookup finds `LEICA Q (Typ 116) & compatibles` | fail (no lens) | pass |
| Leica Q2: the lookup finds `LEICA Q2 & compatibles` | fail (no lens) | pass |
| Leica Q3: the lookup finds `LEICA Q2 & compatibles` | fail (no lens) | pass |
| The distortion correction runs (how far a 600 px frame's corner pixel moves in) | 0 px | 22–24 px |
| The app, Lens correction on, Q3 loaded: the status line | "lens correction not applied" | "applied lens correction: LEICA Q2 & compatibles @ 28.0mm f/8.0" |
| Nikon D7100, Sony A7 III: the same lens as in 2.2.1 (the rule can't reach them) | pass | pass |

9 checks: 2.2.1 fails 7, the patched build passes all 9. Also checked in the running app: switching Lens correction on corrects the Q3 photo visibly, and switching frames to the Q2, the Q and the Sony names each one's own lens.
