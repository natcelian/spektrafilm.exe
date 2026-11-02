# Fix: Camera exposure drifts on every paper (and film) change

Target file: `src/spektrafilm_gui/controller_profile_sync.py` (The Dyeworks 2.2.1). `controller_profile_sync.py` here is the full patched module, `controller_profile_sync.py.diff` the change, and `test_exposure_drift.py` the checks below.

## Symptom

Reported on the pixls.us thread: "Camera exposure gets lower with each new print stock selected. Keeps going down indefinitely", and "it only happens for me when I change paper, not film". It only shows when **Film exposure** is not 0, which is why it is hard to reproduce.

With Film exposure at −1 and Camera exposure at 0, each paper pick lowers Camera exposure by one more stop (−1, −2, −3 … down to −8). With a positive Film exposure it climbs instead. Film changes compound too, whenever Camera exposure has not been edited since the last sync.

## Cause

It is the same pattern as the 2026-07-12 gamma compounding ("profile change stacks"):

1. `params_mapper._apply_camera` composes `params.camera.exposure_compensation_ev = Camera exposure + Film exposure` (2026-10-14).
2. On a stock pick, `apply_profile_defaults` turns the digested params back into GUI state with `gui_state_from_params`, which reads that **sum** into `simulation.exposure_compensation_ev`.
3. `exposure_compensation_ev` is in `PROFILE_SYNC_FIELDS['simulation']`, so the sum is written into the Camera exposure widget.
4. On a paper change, `print_side_only` restores only couplers, halation and grain, so nothing puts the user's value back. On a film change, `_preserve_user_edits` keeps it only if it differs from the baseline, which holds the composed value too.

## Fix

Remove `'exposure_compensation_ev'` from `PROFILE_SYNC_FIELDS['simulation']`, as was done for the `special` gammas, with a comment in the same place and a note in the FORK CONTRACT header. No stock sets Camera exposure as a selection default, so nothing is lost: it is a user knob. Every other read-back of the params goes through the same field list (`_seed_sync_baseline`, `build_default_gui_state`) or copies only coupler fields (the Raw overlay), so this is the only path.

Two side effects, both corrections of the same bug:

- **Reset.** `baseline_look_state()` (top-bar Reset, the filmstrip's Reset settings) rebuilds the look from `_last_synced_values`, which held the composed sum. With Camera +0.7 / Film −1, one paper pick then Reset gave Camera −0.3. Now Camera exposure is not in the baseline, so Reset gives 0, its project default.
- **The film-change status.** `_preserve_user_edits` no longer counts Camera exposure as a stock-owned edit. A film change after editing only Camera exposure no longer says "kept 1 setting you had edited", and stock specifics apply as on any change without carried edits.

Values already drifted in saved roll frames, presets or state files from 2.2.1 are not repaired; they load as saved.

## Verification

`python test_exposure_drift.py <src dir>` drives the real `GuiController` headless (`QT_QPA_PLATFORM=offscreen`). It checks:

- Camera exposure is unchanged through 8 paper and film picks for four Camera/Film exposure pairs;
- Film exposure is untouched;
- none of the 36 film/paper pairs sets Camera exposure in `digest_after_selection`.

| Camera / Film exposure | 2.2.1, Camera exposure after 8 picks | patched |
|---|---|---|
| 0 / −1 | −1, −2, … −8 | 0 throughout |
| +0.7 / −1 | −0.3, −1.3, … −7.3 | +0.7 throughout |
| −0.3 / +1.5 | +1.2, +2.7, … +8 | −0.3 throughout |
| +0.7 / 0 | +0.7 throughout | +0.7 throughout |
| Reset after a paper pick (+0.7 / −1, an image open) | Camera −0.3 | Camera 0 |

With `--dump`, the full GUI state is saved after each of the 32 picks for both builds, and `--compare original.json patched.json` diffs them. The only difference is Camera exposure, so every stock-owned value still syncs exactly as before.
