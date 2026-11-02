# Fix: Input mode exports, renders and edits the wrong photo

Target files (The Dyeworks 2.2.1), all in `src/spektrafilm_gui/`: `controller.py`, `controller_attempts.py`, `controller_compare.py`, `controller_crop.py`, `controller_loupe.py`, `controller_neutral_sample.py`, `controller_presets_canvas.py`, `controller_roll_file.py`. Each `.py` here is the full patched module; `input_mode_wrong_photo.diff` is the whole change in one file; `test_input_mode.py` the checks below.

## Symptom

1. **Add frames…** with two photos. The app goes into Input mode (sorting: side panels hidden, "Sorting on the input").
2. Click photo 1, then photo 2. Photo 2's thumbnail is on screen.
3. **Export image…** writes **photo 1's picture** under photo 2's file name, with photo 2's EXIF.

Preview does the same (photo 1 is rendered); after a second round of clicks it is right, because the first render quietly ended Input mode. Less visibly, browsing thumbnails also changes looks: click A, B, A and B now has A's look, and the roll exports it that way.

## Cause

The input-mode branch of `_roll_load_current` (controller_roll_file.py) sets `_current_input_path` and `_input_mode_navigated` and shows the embedded thumbnail. By design it does not decode the frame, but it also does not apply the frame's look. From then on:

- the input layers (and any render) still hold the **last decoded** frame, while the file name, EXIF and roll entry are the **selected** one: Export, Preview, Scan, test strip, loupe, compare, rotate, mirror and the white-balance eyedropper all act on the wrong picture;
- the panels still hold the last decoded frame's **look**, and every `store_state_for_current(collect_gui_state(...))` (frame switch, autosave, Save roll, Export roll, Analyze roll) stores it onto the selected frame; Copy settings, Apply look to all, versions and Keep read it as the selected frame's too;
- any panel change while sorting (a paste, a reset, an undo) auto-previews the stale pixels, and the finished render clears `_input_mode` without loading the frame or showing the side panels again.

## Fix

The idea: sorting skips the **decode**, not the frame. Everything that needs the frame's pixels loads it first.

1. **Sorting applies the selected frame's stored look** (no decode) in the same branch, with the same follow-ups as a normal switch (stock sync note, canvas background, missing-stock warning, entry look for versions). The panels always belong to the selected frame, so every look consumer above is correct without its own guard.
2. **The app knows which file is decoded.** `_set_or_add_input_stack` records `_loaded_input_path`; `_loaded_frame_is_current()` compares it with `_current_input_path`. This also covers a failed load, and browsing A → B → A needs no reload.
3. **Sorting renders nothing.** `_run_scheduled_auto_preview` and `request_view_refresh` skip while in Input mode, which is what the mode's docstring says it is for ("instant thumbnails, no decode, no render"). A skipped render is remembered and paid when sorting ends, so a paste while sorting is shown on Print.
4. **`_load_selected_frame()`** leaves Input mode (exactly what picking Print does) or, outside it, reloads; it returns False when the frame cannot be decoded, and the caller stops instead of using another frame's pixels. When the caller renders anyway it drops the auto preview the load queued (otherwise it replayed after a Scan and replaced it). It runs after each caller's own busy / cancel / dismiss checks.
5. **Leaving Input mode** stores the panels on the frame before reloading (keeps edits made while sorting), and reloads whenever the decoded frame is not the selected one.
6. **Pins never use another frame's render:** `_capture_kept` and `_capture_reference` skip (an old reference pin is dropped, the frame's next render pins it), and version thumbnails are skipped rather than wrong.

What each entry point does from Input mode now:

| Entry point | From Input mode |
|---|---|
| `_start_simulation` (Preview, Scan, retry) | loads the selected frame, leaves Input mode properly, renders once |
| `export_output` (Export image…, Ctrl+E) | loads the selected frame, then exports it |
| `show_fullscreen_output` (F11, Ctrl+P) | renders the selected frame and goes full screen when that render lands |
| `toggle_preview_view` (Ctrl+R) | renders the selected frame, like a first press with nothing rendered |
| `run_test_strip`, `_start_compare_negative_render` | load the selected frame first |
| `toggle_loupe` | opens the frame (it renders), then the loupe works on it |
| `_rotate_input_image`, `_mirror_input_image` | load first, so the turn applies to the right picture |
| `arm_neutral_sample` (white-balance eyedropper) | loads first, so it samples the right pixels |
| `save_output_layer` | refuses when the render is another frame's (not reachable from the 2.2.1 UI, guarded anyway) |

Side effects to know about:

- A thumbnail click while sorting now also applies the look: median 138 ms → 182 ms in a headless run (the decode it still skips is ~2 s).
- Those actions now take you out of Input mode, as Print does. Export of the frame that was already decoded stays in Input mode.
- `_input_mode_navigated` is still set by the existing code but no longer decides anything.

Not changed here: undo/redo is one stack for all frames, so Ctrl+Z after a frame switch brings back the previous frame's look in edit mode too. That has its own small PR (per-image undo).

## Verification

`python test_input_mode.py <src dir> <out dir> <raw A> <raw B>` drives the real `GuiController` headless (`QT_QPA_PLATFORM=offscreen`, a throwaway settings file); A and B must be different pictures. It compares the pixels a render or export would use with each file decoded on its own.

36 checks: **2.2.1 fails 27, the patched build passes all 36.** Among them:

- export / Preview after clicking A then B renders B's pixels with B's look (2.2.1: A's);
- browsing A → B → A → B keeps each frame's look; an autosave while sorting does not overwrite B;
- roll export while sorting gives each frame its own look;
- a paste while sorting lands on the selected frame, renders nothing until sorting ends, and survives Print;
- adding another batch while sorting, an undecodable frame (refused twice), Keep, flagging a reference, the eyedropper, F11 and Ctrl+R;
- regressions: in edit mode a frame switch still stores the edit on the frame left and shows the next frame's look; exporting the decoded frame from Input mode stays in Input mode.

Also tested by hand in the app with the steps in the Symptom section, plus Preview, look switching and F11.
