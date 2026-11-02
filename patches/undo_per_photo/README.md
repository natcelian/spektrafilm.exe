# Fix: Undo after switching frames puts the previous frame's look on the new one

Target files (The Dyeworks 2.2.1): `src/spektrafilm_gui/controller_history.py` and `controller.py`. Each `.py` here is the full patched module, `undo_per_photo.diff` the change, and `test_undo_per_photo.py` the checks below.

## Symptom

1. Edit frame A (say Film exposure 0 → +1 → +2).
2. Switch to frame B.
3. Press Ctrl+Z.

B now shows **A's look** (+2). Undo did not undo anything on B; it stepped back to the snapshot before the switch, which belongs to A, and applied it to B. It happens in normal editing, with or without a roll.

## Cause

`HistoryMixin` keeps **one** debounced stack of whole-look snapshots for everything. Switching frames applies the incoming frame's stored look to the panels, and that change is recorded like any edit. So the switch itself becomes an undo step, and the step before it is the previous frame's look. The class header in `controller.py` already says what was intended: "loading files and roll membership are not part of the stack".

## Fix

One stack **per image**, keyed by the image's path (normalized like the roll's keys):

- `_use_history_of_current()` makes `_history` / `_history_index` the open image's own stack. It parks the previous image's stack (with its redo steps) and takes out the current one's, empty for an image not edited yet. It is called at the top of `_history_push_now`, `undo` and `redo`, so no load path has to remember to call it.
- `_set_or_add_input_stack` (every decode of an image) records the look the image opened with, so the image's first edit can be undone too.
- Stacks are kept for the 30 most recent images (`_HISTORY_IMAGES`), each still capped at `_HISTORY_LIMIT` = 100 snapshots.

Result: Ctrl+Z only ever undoes edits to the image in front of you, a frame switch is not an undo step, and coming back to an image brings its own undo and redo back. Single images outside a roll get their own stack the same way. The startup baseline belongs to "no image".

Two small consequences:

- An edit made less than 0.7 s before switching (inside the debounce) is recorded in the image's stack when you come back to it, rather than as a separate step.
- The extra push on decode is de-duplicated against the stack's top, so reloading the same image (a white-balance reprocess, for example) adds no step unless the look actually changed.

## Verification

`python test_undo_per_photo.py <src dir> <out dir> <raw A> <raw B>` drives the real `GuiController` headless (`QT_QPA_PLATFORM=offscreen`, a throwaway settings file), editing through the panels and pressing undo/redo as the shortcuts do.

| Check | 2.2.1 | patched |
|---|---|---|
| Ctrl+Z right after switching to B leaves B's look alone | fail (A's look) | pass |
| an edit on B undoes back to B's look | fail | pass |
| ...and redoes | pass | pass |
| back on A, Ctrl+Z undoes A's last edit | fail (B's look) | pass |
| ...and the one before | fail | pass |
| redo on A goes forward again | fail | pass |
| undoing all the way on A never reaches B's look | pass | pass |

10 checks: 2.2.1 fails 5, the patched build passes all 10. Also tested by hand in the app (edit one frame, switch, Ctrl+Z / Ctrl+Y on both frames).
