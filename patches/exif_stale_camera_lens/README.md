# Fix: exports carry an earlier photo's camera, lens and star rating

Target files (The Dyeworks 2.2.1): `src/spektrafilm_gui/widget_sections.py`, `controller.py`, `controller_ratings.py`. Each `.py` here is the full patched module, each `.py.diff` the change, and `test_exif_carryover.py` the checks below.

## Symptom

Reported on the pixls.us thread: "The exported file is from a Fuji X100. However, earlier in the editing session, I had been editing a file from a Sony camera, and the extract shows that some of the EXIF data has been carried through into this file." The user never edited any metadata.

Reproduced with two CC0 raws: open a Sony A7 III ARW, then a Nikon D7100 NEF, and export the Nikon. The export says `Model = SONY ILCE-7M3` and `LensModel = FE 16-35mm F2.8 GM`. A roll export of both is worse: every frame gets the same camera and lens. A star rating given to a single image (no roll) also stays for every image opened after it and is written as `Exif.Image.Rating` into their exports.

## Cause

1. `MetadataPanel` (widget_sections.py) is built, but it is never placed in the window, so nobody sees its Camera Model / Lens Model boxes.
2. On every load, `_update_banner_and_metadata` calls `set_camera_lens`, which fills a box **only when it is empty**. The first file fills them; every later file leaves them alone.
3. `custom_tags()` hands the boxes to every export (`_custom_metadata_tags` for a single image, `export_roll` with Sync metadata on), and `write_image_metadata` writes them over the exported file's own tags.
4. `read_camera_lens_model` puts the make in front of the model ("SONY ILCE-7M3"), so even the correct file's export rewrote its Model tag.
5. `_single_rating` (controller_ratings.py) is one number for "the image outside a roll" and is never reset when another image is opened.

## Fix

- `set_camera_lens` now replaces a box that still holds the previous file's prefill (tracked per key in `_prefilled`), and empties it when the new file records none. A value someone typed is still kept, as the method's docstring always promised.
- `custom_tags()` leaves out a Camera/Lens value that equals the loaded file's own: the export copies the source file's tags anyway. So a prefill is only ever shown, never written. This also stops the make+model rewrite and stops one frame's camera being stamped onto a whole roll.
- The controller now also clears the prefill for a file whose metadata cannot be read, instead of skipping the call.
- The single-image rating remembers which image it was given to (`_single_rating_path`); another image starts at 0 stars, and the stars on screen are refreshed on every load. Re-opening the rated image in the same session brings its stars back.

Why keep the "typed value" path at all, when the panel is hidden? Because it is the panel's documented behaviour, and it keeps the door open if the panel is ever shown. Removing Camera/Lens from `custom_tags()` entirely would also fix the bug; happy to switch to that if you prefer it.

Nothing else changes: Artist / Copyright / Description in the same panel behave as before, and roll frames' ratings were already per frame.

## Verification

`python test_exif_carryover.py <src dir> <out dir> <raw A> <raw B>` drives the real `GuiController` headless (`QT_QPA_PLATFORM=offscreen`, a throwaway settings file). Raws A and B must come from different cameras. It exports files and reads their EXIF back.

| Check | 2.2.1 | patched |
|---|---|---|
| after loading A then B, the panel holds B's camera/lens | fail (A's) | pass |
| the export of B carries B's own Model and LensModel | fail (`SONY ILCE-7M3` on the Nikon) | pass |
| a typed lens is not overwritten by the next file | pass | pass |
| cleared again, the lens follows the file | fail | pass |
| a file without EXIF empties Camera/Lens | fail | pass |
| a single image's rating is written into its export | pass | pass |
| the next single image does not inherit that rating | fail (4 stars) | pass |
| re-opening the rated image brings its rating back | pass | pass |
| roll export: every frame keeps its own Make / Model / LensModel | fail (both frames) | pass |

12 checks in total: 2.2.1 fails 7, the patched build passes all 12. The fix was also tested by hand in the app (single and roll exports, checking the written files in Windows' file properties).
