# Fix: dragging the crop frame moves edges you are not holding, and a press near it turns the photo

Target files (The Dyeworks 2.2.1): `src/spektrafilm_gui/crop_frame.py`, `controller_crop.py`, `controller.py`, `gpu_canvas.py` and `widget_specs.py`. Each `.py` here is the full patched module, `crop_frame_drag.diff` the change, and `test_frame_drag.py` the checks below.

## Symptom

Arm the crop frame (the rail's crop button) and turn the photo about 10° by dragging outside the frame. Then:

1. Drag the top handle up toward the photo's edge. When it gets there, the bottom edge and both sides start to move: the frame slides and shrinks instead of stopping.
2. Press a pixel or two outside the frame's edge, anywhere but right on a handle, and drag. The photo turns instead of the frame resizing, and the cursor gave no warning.
3. Make the frame smaller, move it off-centre and turn the photo. The frame slides toward the middle.
4. Press Enter. The crop kept is not quite the frame that was on screen: crop_center / crop_size keep two decimals, so up to ~60 px at 24 MP.

## Cause

1. `_frame_dragged` runs `resize_rect` (or `move_image_under_frame`) on the frame, then `fit_rect_in_rotated_image` on the result (d-controller-257). That fit slides the rect back inside at its own size. So once the dragged edge passes the turned picture, the fit moves the edges the user is not holding. Every move fits the previous fit again. Unconstrained, `clamp_rect` inside `resize_rect` does the same at the canvas edge.
2. `hit_test` grabs only a 16 px square around each edge's midpoint and 24 px at a corner. Every other point on or just outside the edge line is `'outside'`, and a press outside turns the photo.
3. `_skew_dragged` and the straighten line fit the frame with `fit_rect_in_rotated_image`, which slides it toward the centre.
4. `crop_center` / `crop_size` have two decimals (0.01 of the long edge), and Done writes the frame rounded to them.

## Fix

**Only what is held moves, and it stops at the edge.**
- A drag is tracked from the press. `_frame_drag_from` holds the frame at the press and the total drag. It is cleared by the canvas's `on_frame_release`, which was a no-op lambda.
- `_held_frame` builds the frame from that start plus the total drag. `furthest_drag` bisects how far along the drag it still fits inside the turned picture. The frame is never re-fitted or slid, and it follows the pointer again once the pointer comes back.
- A corner on a locked ratio moves both axes together. Otherwise each axis is taken on its own, so a corner (or the whole frame) that meets one edge still slides along it.
- `rect_fits_rotated_image` tests each corner exactly. Comparing with a re-fit is off by the fit's own rounding right at the edge.

**A press near the frame resizes it, never turns.**
- `hit_test` takes a whole edge within 10 px of its line and a corner within 14 px. Anything up to 20 px outside the frame takes the nearest edge or corner, and the turn starts beyond that band (Fluent's minimum target is 24 px).
- The canvas shows the matching cursor: ↕ ↔ on edges, diagonals on corners, the move cursor inside, and the turn cursor beyond the band.
- A press in the band that falls past the picture's edge resizes too. 2.2.1 turned the photo for any press outside the picture.

**Turning keeps the frame where the user put it.** `fit_rect_about_centre` shrinks the frame about its own centre (aspect kept) to the largest size that fits. It slides the frame, as before, only when no size fits at that centre. It is used by the canvas turn and the straighten line.

**Done keeps the frame on screen.** `crop_center` / `crop_size` keep four decimals: 0.0001 of the long edge is ~0.6 px at 24 MP.

This is how Lightroom Classic, Apple Photos and Capture One behave: the handle stops at the boundary and the opposite edge never moves.

## Verification

`python test_frame_drag.py <src dir> <out dir> <raw> [--baseline]` drives the real `GuiController` headless (`QT_QPA_PLATFORM=offscreen`, a throwaway settings file). It turns the photo 10° with the canvas's own turn handlers, then drags through `_frame_dragged`. For the presses it sends real mouse events to the canvas overlay. `--baseline` runs the same against 2.2.1.

| Check (2 - Nikon D7100.NEF, turned 10°) | 2.2.1 | patched |
|---|---|---|
| Top handle dragged up past the photo's edge: left, right and bottom never move | fail (left 0.101 → 0.296, bottom 0.799 → 0.930) | pass |
| ...and the top stops inside the turned photo | pass | pass |
| A corner dragged outward: the opposite corner never moves | fail | pass |
| The frame moved past the edge keeps its size, inside the photo | pass | pass |
| A press 1 px outside an edge, a quarter along: resize cursor, it resizes, no turn | fail (turn cursor, the photo turns) | pass |
| ...9 px outside an edge's middle handle | fail (the photo turns) | pass |
| ...13 px outside a corner | fail (the photo turns) | pass |
| ...19 px outside an edge | fail (the photo turns) | pass |
| A press inside: the move cursor, no turn | fail (arrow cursor) | pass |
| A press 35 px outside: the turn cursor, and the photo turns | pass | pass |
| Turning an off-centre frame keeps its centre and shape, inside the photo | fail (centre 0.285, 0.256 → 0.329, 0.282) | pass |
| Done keeps the frame on screen (largest edge shift, 1024 px preview) | fail (5.0 px) | pass (0.0 px) |

14 checks: 2.2.1 fails 9, the patched build passes all 14. Also tested by hand in the app, together with `patches/crop_white_corners` and `patches/crop_straighten_session`.
