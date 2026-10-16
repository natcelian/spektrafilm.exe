"""The presets canvas part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes PresetsCanvasMixin as a base.
"""
from __future__ import annotations

import os

import numpy as np
from qtpy import QtCore

from spektrafilm_gui import controller_runtime as runtime, preset_store
from spektrafilm_gui import notices
from spektrafilm_gui.app_settings import app_settings
from spektrafilm_gui.shell_layout import dialog_parent
from spektrafilm_gui.shell_layout import set_status
from spektrafilm_gui.state_bridge import apply_gui_state, apply_gui_state_sections
from spektrafilm_gui.state_bridge import collect_gui_state
import shutil
from spektrafilm_gui.preset_store import (
    delete_preset as _delete_preset,
    join_qualified_name,
    list_preset_folders,
    load_preset_fields,
    load_preset_payload,
    load_preset_sections,
    preset_path,
    save_preset,
    split_qualified_name,
)
from spektrafilm_gui.persistence import gui_state_to_dict
from spektrafilm_gui.settings_fields import fields_of as fields_of_group, sections_touched
from spektrafilm_gui.roll_session import classify_path
from spektrafilm_gui.instant_preview import load_instant_preview
from spektrafilm_gui.paper_texture import composite_textures

QTimer = getattr(QtCore, 'QTimer')


def paper_of_look(look_state):
    """A look's own paper state if its finish is active with textures, else None.
    The roll export reads each frame's saved look, not the widgets."""
    paper = getattr(look_state, 'paper', None)
    if paper is None or not getattr(paper, 'active', False) or not getattr(paper, 'textures', ()):
        return None
    return paper


class PresetsCanvasMixin:
    """GuiController's presets canvas methods."""

    def import_preset_file(self, path: str) -> None:
        """Adopt a .sfpreset from anywhere into the user preset store.

        A COPY, validated first: the file keeps its own metadata and payload,
        so an imported preset behaves exactly like one saved here. A file
        that does not parse is refused with a message rather than landing
        half-written in the store.
        """
        import json


        try:
            with open(str(path), 'r', encoding='utf-8') as handle:
                document = json.load(handle)
            if not isinstance(document, dict) or 'state' not in document:
                raise ValueError('no preset state in that file')
            name = str(document.get('name') or os.path.basename(str(path)))
            target = preset_store.preset_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(path), str(target))
        except Exception as error:                      # noqa: BLE001
            set_status(self._viewer, f'Could not import that preset: {error}')
            return
        set_status(self._viewer, f'Imported preset: {name}')
        section = getattr(self._widgets, 'gui_config', None)
        refresh = getattr(section, 'refresh_presets', None)
        if callable(refresh):
            refresh(name)

    def apply_preset(self, name: str) -> None:
        """Dropdown selection: apply the named preset like Load from file."""
        from spektrafilm_gui.persistence import gui_state_from_dict

        try:
            state = gui_state_from_dict(load_preset_payload(name))
            sections = load_preset_sections(name)
            chosen_fields = load_preset_fields(name)
        except (OSError, ValueError) as exc:
            notices.warn(dialog_parent(self._viewer), 'Preset',
                                f'Could not load the preset.\n\n{exc}')
            return
        if chosen_fields:
            # A preset saved down to individual settings writes ONLY those
            # over what is on screen; everything else in the section stays.
            from spektrafilm_gui.settings_fields import sections_touched
            from spektrafilm_gui.roll_session import masked_state
            apply_gui_state_sections(
                masked_state(collect_gui_state(widgets=self._widgets),
                             state, chosen_fields),
                widgets=self._widgets,
                section_names=tuple(sections_touched(chosen_fields)))
        elif sections:
            # A partial preset from before per-field selection: whole sections,
            # which is exactly what it meant when it was saved.
            apply_gui_state_sections(
                state, widgets=self._widgets, section_names=tuple(sections))
        else:
            apply_gui_state(state, widgets=self._widgets)
        self._note_film_stock_synced()
        self._sync_canvas_background()
        self._warn_if_missing_custom_stock()
        set_status(self._viewer, f'Preset applied: {name}')

    def save_current_as_preset(self) -> None:
        from spektrafilm_gui.preset_dialogs import ask_preset_name_and_folder
        from spektrafilm_gui.section_picker import pick_settings
        from spektrafilm_gui.settings_groups import group_labels

        section = getattr(self._widgets, 'gui_config', None)
        suggested = section.current_preset() if section is not None else ''
        suggested_folder, suggested_leaf = split_qualified_name(suggested)
        # 2026-07-25; see design/DECISIONS.md#d-controller-286
        tags: list = []
        answer = ask_preset_name_and_folder(
            dialog_parent(self._viewer),
            name=suggested_leaf,
            folder=suggested_folder,
            folders=list_preset_folders(),
            tags_out=tags)
        if answer is None:
            return
        name, folder = answer
        name = join_qualified_name(folder, name)
        # Which settings does this preset carry? Default = everything (a full
        # preset); the user can narrow it to 'just the grain' etc. (NAT).
        from spektrafilm_gui.state import PROJECT_DEFAULT_GUI_STATE
        saved_state = collect_gui_state(widgets=self._widgets)
        chosen = pick_settings(
            dialog_parent(self._viewer),
            title='Save preset', meta='new preset', verb='Save',
            preselected=group_labels(),
            state=saved_state, default_state=PROJECT_DEFAULT_GUI_STATE)
        if chosen is None:
            return  # cancelled
        if not chosen:
            set_status(self._viewer, 'Nothing selected: preset not saved')
            return
        # A preset carrying EVERY field is a plain full preset (sections=None);
        # anything narrower records the sections it touches, exactly as before.
        every = tuple(p for label in group_labels()
                      for p in fields_of_group(label))
        sections_arg = (None if set(chosen) == set(every)
                        else sections_touched(chosen))
        try:
            if preset_path(name).is_file():
                # Overwriting loses the stored preset, so the safe answer
                # leads and Enter cannot reach the other one.
                if not notices.confirm(
                        dialog_parent(self._viewer), 'Save preset',
                        f"A preset named '{name}' already exists.",
                        sub='Overwriting replaces what it holds.',
                        confirm_label='Overwrite', cancel_label='Keep it',
                        destructive=True):
                    return
            save_preset(name, gui_state_to_dict(saved_state),
                        sections=sections_arg,
                        fields=(None if sections_arg is None
                                else tuple(chosen)),
                        tags=tags)
        except (OSError, ValueError) as exc:
            notices.warn(dialog_parent(self._viewer), 'Save preset',
                                f'Could not save the preset.\n\n{exc}')
            return
        if section is not None:
            section.refresh_presets(select=name)
        set_status(self._viewer, f'Preset saved: {name}')

    def update_preset(self, name: str) -> None:
        """NAT 2026-07-22: overwrite the SELECTED preset with the current
        look — no dialogs, the selection is the intent."""

        try:
            save_preset(name, gui_state_to_dict(collect_gui_state(widgets=self._widgets)))
        except (OSError, ValueError) as exc:
            notices.warn(dialog_parent(self._viewer), 'Update preset',
                                f'Could not update the preset.\n\n{exc}')
            return
        section = getattr(self._widgets, 'gui_config', None)
        if section is not None:
            section.refresh_presets(select=name)
        set_status(self._viewer, f'Preset updated: {name}')

    def delete_preset(self, name: str) -> None:
        if not notices.confirm(
                dialog_parent(self._viewer), 'Delete preset',
                f"Delete the preset '{name}'?",
                confirm_label='Delete preset', cancel_label='Keep it',
                destructive=True):
            return
        _delete_preset(name)
        section = getattr(self._widgets, 'gui_config', None)
        if section is not None:
            section.refresh_presets(select='')
        set_status(self._viewer, f'Preset deleted: {name}')

    def set_gpu_canvas(self, widget) -> None:
        self._gpu_canvas = widget
        if widget is not None:
            try:  # restore the persisted composition-guide overlay (Ctrl+O / Shift+O)
                settings = app_settings()
                mode = str(settings.value('canvas_guides', '') or 'off')
                widget.set_guide_mode(mode)
                if hasattr(widget, 'set_guide_orient'):
                    widget.set_guide_orient(int(settings.value('canvas_guide_orient', 0) or 0))
            except Exception:
                pass
        self._announce_guessed_gpu_memory()

    def _announce_guessed_gpu_memory(self) -> None:
        """Say it ONCE, and only while the budget is still a guess.

        2026-08-30; see design/DECISIONS.md#d-controller-313

        2026-08-30; see design/DECISIONS.md#d-controller-314

        * `memory_is_guessed` -- is there still something to say? A value
          already set by hand makes the answer no, on every launch, with no
          flag involved.
        * the stored flag -- have we said it already? A user who never sets
          a value has made their choice too, and repeating the line at every
          launch is nagging, not informing.

        The Config -> GPU section keeps the same sentence permanently beside
        the control that fixes it, so nothing is lost by going quiet here --
        it moves from an announcement to something you can go and look up.

        Deferred to the event loop because this runs during window
        construction, before there is a status bar to write to.
        """
        try:
            from spektrafilm.gpu import vendor

            if not vendor.profile().memory_is_guessed:
                return                       # detected, or answered by hand
            # Through `gpu_settings()`, the one seam the GPU preferences
            # share, so the tests can point all three keys at a temporary
            # file instead of writing the developer's real registry.
            from spektrafilm_gui.left_panel_config import gpu_settings

            settings = gpu_settings()
            if settings.value(self.GPU_MEMORY_NOTICE_KEY, False) in (
                    True, 'true', 'True', 1, '1'):
                return                       # already said once
            settings.setValue(self.GPU_MEMORY_NOTICE_KEY, True)
        except Exception:
            return

        def _say() -> None:
            try:
                set_status(
                    self._viewer,
                    'GPU memory could not be detected - assuming a conservative '
                    'amount. Set your card\'s memory in Config > GPU to use all '
                    'of it.',
                    timeout_ms=0)
            except Exception:
                pass

        try:
            QTimer.singleShot(0, _say)
        except Exception:
            _say()

    def _mirror_to_gpu_canvas(self, image, *, is_output: bool = False) -> None:
        """Display funnel to the wgpu canvas. The white padding frame is baked
        in HERE at the pixel level (the napari-era world-geometry border layer
        no longer reaches the canvas since 6C); the canvas gets the padding
        fraction as a content inset so crop mapping and the composition guides
        keep working on the photo, not the border.

        For the OUTPUT (is_output), the paper-texture finish is composited here
        too — before padding when padding-burn is off (texture on the image),
        after when it is on (texture bleeds into the frame). NAT 2026-07-26."""
        if self._gpu_canvas is None:
            return
        if is_output and self._negative_left_mode is not None:
            # Compare on the Negative view: the left of the seam is the
            # negative, so a new print re-renders THAT instead of replacing it
            QtCore.QTimer.singleShot(0, self._refresh_negative_left)
            return
        try:
            # A device frame (gpu.display.DisplayFrame) goes to the canvas as
            # it is -- buffer to texture, no host pixels. Only the paper
            # finish and the burnt-in padding below work on host pixels, and
            # touching `data` there reads the frame back (once, lazily).
            if getattr(image, 'on_device', False) and hasattr(image, 'stride'):
                data = image
            else:
                data = np.asarray(image)[..., :3]
            self._last_canvas_image = data
            self._last_canvas_is_output = is_output
            paper = self._active_paper_state() if is_output else None
            if paper is not None and not paper.padding_burn:
                data = self._paper_composite(data, paper)
            padding_fraction = self._display_padding_fraction()
            padding = runtime.padding_pixels_for_image(data, padding_fraction)
            if padding > 0:
                # 2026-07-11; see design/DECISIONS.md#d-controller-288
                fill = self._blank_film_rgb() if self._scan_film_active() else None
                data = runtime.apply_border_padding(data, padding, fill=fill)
            if paper is not None and paper.padding_burn:
                data = self._paper_composite(data, paper)
            self._gpu_canvas.set_image(data)
            # Phase 18G: a mount is cut to the print. The board follows the
            # image's shape, so a portrait frame does not sit in a landscape
            # board with paper down both sides.
            stage = getattr(self, '_stage_area', None)
            if stage is not None:
                try:
                    stage.follow(self._gpu_canvas)
                except RuntimeError:
                    pass
            set_inset = getattr(self._gpu_canvas, 'set_content_inset', None)
            if callable(set_inset):
                if padding > 0:
                    set_inset(padding / data.shape[1], padding / data.shape[0])
                else:
                    set_inset(0.0, 0.0)
        except Exception:
            pass  # display mirror must never break the render path

    def peek_input(self, held: bool) -> None:
        """Hold the backslash key: show the input while it is down, the render again
        on release. The same canvas swap as Ctrl+R, so it never re-runs the
        pipeline; with nothing rendered yet there is nothing to compare, so a
        press does nothing (Ctrl+R would start a preview, a held key must not)."""
        if held:
            rendered = (self._output_display_cached is not None
                        or self._output_layer() is not None)
            if self._showing_input or not rendered:
                return
            self._peeking = True
            self.toggle_preview_view()
        elif self._peeking:
            self._peeking = False
            if self._showing_input:
                self.toggle_preview_view()

    def toggle_preview_view(self) -> None:
        """Ctrl+R: rolling BEFORE/AFTER. Flip the canvas between the cached
        input DISPLAY and the last render, without re-running the pipeline
        (this is the accurate full-quality input, distinct from the instant
        thumbnail of the sorting mode). First press with nothing rendered kicks
        a preview so there is an 'after' to compare against (NAT 2026-07-18)."""
        if self._output_display_cached is None and self._output_layer() is None:
            self._run_preview(report_status=True)
            self._showing_input = False
            return
        if self._showing_input:
            target = self._output_display_cached
            if target is None:
                layer = self._output_layer()
                target = np.asarray(layer.data)[..., :3] if layer is not None else None
            if target is not None:
                self._mirror_to_gpu_canvas(target, is_output=True)
            self._showing_input = False
            set_status(self._viewer, 'After: showing render')
        else:
            source = self._input_display_cached
            if source is None:
                getter = getattr(self._layers, 'preview_input_layer', None)
                layer = getter() if callable(getter) else None
                source = np.asarray(layer.data)[..., :3] if layer is not None else None
            if source is not None:
                self._mirror_to_gpu_canvas(source)
            self._showing_input = True
            set_status(self._viewer, 'Before: showing input')

    # -- Input / sorting mode (segmented top-bar button) ---------------------
    def _choose_mode_for_open(self, *, frames: int, from_roll: bool) -> None:
        """Which mode a freshly opened set of files should land in.

        2026-09-05; see design/DECISIONS.md#d-controller-315

          - SEVERAL new frames  -> input mode. That is a take to sort, and
            sorting is what input mode is for -- instant thumbnails, no
            decode, no render, both panels out of the way.
          - ONE frame           -> edit. Opening a single picture is opening
            it to work on; there is nothing to sort.
          - a roll, or launched from one -> edit. The sorting already
            happened when the roll was made.

        Written as one method taking the two facts that decide it, because
        the alternative is the same condition spelled slightly differently
        at each of the three call sites, which is how they drift.
        """
        self.set_input_mode(bool(frames > 1 and not from_roll))

    def set_input_mode(self, on: bool) -> None:
        """Sorting mode (NAT 2026-07-25): show the camera's embedded thumbnail
        (instant, no decode/render) and keep showing it across roll navigation,
        so a roll can be culled without the ~5s per-frame input load. Leaving
        the mode loads + renders the current frame properly so it can be
        edited."""
        on = bool(on)
        was_on = self._input_mode
        self._input_mode = on
        refresh_rings = getattr(self, '_refresh_dust_marks', None)    # the rings belong to the developed picture, not the thumbnail
        if refresh_rings is not None:
            refresh_rings()
        if on:
            self._input_mode_navigated = False
            self._show_instant_input()
            # 2026-08-04, 2026-09-05; see design/DECISIONS.md#d-controller-289
            set_status(self._viewer,
                       '当前处于原片整理模式。点击“底片”或“印相”继续调色。',
                       timeout_ms=0)
        else:
            # Clears the pinned sorting hint above, which has no timeout.
            set_status(self._viewer, '调色编辑模式')
            # If the user navigated frames while sorting, the loaded image is
            # stale — reload + render the current frame so editing is correct.
            if was_on and self._input_mode_navigated:
                if self._roll.current is not None:
                    self._roll_load_current(restore_state=True)
                elif self._current_input_path:
                    self._reload_current_input()
            else:
                # Didn't navigate: just restore the render/input we already had.
                target = self._output_display_cached
                restore_output = target is not None
                if target is None and self._input_display_cached is not None:
                    target = self._input_display_cached
                if target is not None:
                    self._mirror_to_gpu_canvas(target, is_output=restore_output)
                # AND ASK FOR THE RENDER (NAT 2026-09-05: "when switching to
                # edit mode, we shouldn't need to click preview").
                #
                # Restoring the cached display puts a picture back on the
                # canvas, which is why this looked like it had worked -- but
                # if there was no render yet, the cache holds the INPUT and
                # edit mode opened on an unrendered frame waiting for a
                # click. The auto-preview toggle it goes through is the one
                # already on Config > Display preferences, so a user who has
                # turned it off still gets what they asked for.
                if not restore_output:
                    self._request_auto_preview_if_enabled()
        # 2026-07-26; see design/DECISIONS.md#d-controller-290
        if not on:
            self._set_right_panel_locked_state(False)
        self._set_side_panels_visible(not on)
        if on:
            self._set_right_panel_locked_state(True)
        self._apply_input_mode_chrome(on)
        self._sync_input_mode_button()

    def _set_side_panels_visible(self, visible: bool) -> None:
        hook = getattr(self, '_set_side_panels', None)
        if callable(hook):
            hook(visible)

    def _set_right_panel_locked_state(self, locked: bool) -> None:
        """The edit panel is EDIT MODE'S (NAT 2026-09-07).

        Input mode already hid it, but hiding is not holding: its top-bar
        toggle still worked, so one click put all 199 parameters back over
        the camera's embedded thumbnail -- which is not the render they would
        change. Locked shut here, and its button disabled so the bar says so.

        The LEFT panel stays free: sorting is exactly when the library is
        worth having open.
        """
        hook = getattr(self, '_set_right_panel_locked', None)
        if callable(hook):
            hook(locked)

    def _apply_input_mode_chrome(self, on: bool) -> None:
        """The other two things input mode does to the room, both from the
        page's own handler (NAT 2026-08-03).

        THE VIEW SEGMENT GREYS OUT (`#view-seg.is-disabled`). Input mode
        shows the untouched source; there is no print to compare it against,
        so Print / Split / Negative have nothing to mean.

        THE MOUNT GOES BARE (`mount.style.padding = 0`, no background, no
        shadow). Just the image, on the room colour.
        """
        top_bar = getattr(self, '_top_bar', None)
        set_enabled = getattr(top_bar, 'set_compare_enabled', None)
        if callable(set_enabled):
            set_enabled(not on)
        stage = getattr(self, '_stage_area', None)
        set_bare = getattr(stage, 'set_bare', None)
        if callable(set_bare):
            set_bare(on)

    def _reload_current_input(self) -> None:
        """Re-run the real load for the current single (non-roll) image."""
        path = self._current_input_path
        if not path:
            return
        if classify_path(path) == 'raw':
            self.load_raw_image(path)
        else:
            self.load_input_image(path)

    def _show_instant_input(self):
        """Push the current frame's instant thumbnail to the canvas; returns
        the image shown (or None)."""
        path = self._current_input_path
        if not path:
            if self._input_display_cached is not None:
                self._mirror_to_gpu_canvas(self._input_display_cached)
            return None
        image = load_instant_preview(path)
        if image is not None:
            self._mirror_to_gpu_canvas(image)
            return image
        if self._input_display_cached is not None:
            self._mirror_to_gpu_canvas(self._input_display_cached)
        return None

    def _sync_input_mode_button(self) -> None:
        """Keep the segmented top-bar input/edit toggle in sync."""
        button = getattr(self, '_input_mode_button', None)
        if button is None:
            return
        try:
            button.set_input_active(self._input_mode)
        except Exception:
            pass

    def _active_paper_state(self):
        """The paper state IF the finish is active with at least one loadable
        texture, else None (so default/empty output stays bit-identical)."""
        section = getattr(self._widgets, 'paper', None)
        if section is None:
            return None
        try:
            state = section.get_state()
        except Exception:
            return None
        if not getattr(state, 'active', False) or not getattr(state, 'textures', ()):  # noqa: E501
            return None
        return state

    def _load_paper_texture(self, path: str):
        if self._paper_texture_cache is None:
            self._paper_texture_cache = {}
        if path in self._paper_texture_cache:
            return self._paper_texture_cache[path]
        image = None
        try:
            from PIL import Image as PILImage
            prev = getattr(PILImage, 'MAX_IMAGE_PIXELS', 0)
            if prev is not None:
                PILImage.MAX_IMAGE_PIXELS = None
            with PILImage.open(path) as handle:
                image = np.asarray(handle.convert('RGB'))
        except Exception:
            image = None
        self._paper_texture_cache[path] = image
        return image

    def _paper_texture_list(self, paper):
        textures = []
        for entry in paper.textures:
            arr = self._load_paper_texture(str(entry[0]))
            if arr is None:
                continue
            mode = str(entry[1]) if len(entry) > 1 else 'Multiply'
            opacity = float(entry[2]) if len(entry) > 2 else 1.0
            textures.append((arr, mode, opacity))
        return textures

    def _paper_composite(self, data_uint8, paper) -> np.ndarray:
        """Composite the active textures onto a uint8 RGB display image."""
        from spektrafilm_gui.paper_texture import composite_textures
        textures = self._paper_texture_list(paper)
        if not textures:
            return data_uint8
        base = np.asarray(data_uint8).astype(np.float32) / 255.0
        out = composite_textures(base, textures)
        # 2026-09-05; see design/DECISIONS.md#d-controller-292
        return np.clip(out, 0.0, 1.0).__mul__(255.0).round().astype(np.uint8)

    def apply_paper_finish_to_export(self, buffer: np.ndarray, *, paper=None,
                                     padding: bool = True) -> np.ndarray:
        """Export-side paper finish: optional padding-burn border (reuses the
        Display white-padding width) then the texture composite, in the buffer's
        own dtype/space (matches the preview for sRGB/normal encodings; a
        defensible 'texture is part of the scan' for wide-gamut ones). Returns
        the buffer unchanged when the finish is inactive (bit-identical).

        `paper` defaults to what the widgets show; a roll export passes the frame's own
        saved paper state and `padding=False`."""
        if paper is None:
            paper = self._active_paper_state()
        if paper is None:
            return buffer
        arr = np.asarray(buffer)
        if padding and paper.padding_burn:
            pad = runtime.padding_pixels_for_image(
                arr, float(self._widgets.display.white_padding.value))
            if pad > 0:
                arr = runtime.apply_border_padding(arr, pad, fill=None)  # paper-white
        textures = self._paper_texture_list(paper)
        if not textures:
            return arr
        dtype = arr.dtype
        if np.issubdtype(dtype, np.integer):
            max_value = float(np.iinfo(dtype).max)
            base = arr.astype(np.float32) / max_value
            out = composite_textures(base, textures)
            return (np.clip(out, 0.0, 1.0) * max_value).round().astype(dtype)
        base = arr.astype(np.float32)
        return composite_textures(base, textures).astype(dtype)

    def refresh_paper_finish(self) -> None:
        """Re-composite the paper finish onto the last render, no re-simulation
        (paper is a display finish). Textures may have changed, so drop the
        loaded-texture cache first."""
        self._paper_texture_cache = None
        self._sync_mount_padding()
        if self._showing_input or self._input_mode:
            return          # not looking at the render right now
        target = self._output_display_cached
        if target is None:
            layer = self._output_layer()
            target = np.asarray(layer.data)[..., :3] if layer is not None else None
        if target is not None:
            self._mirror_to_gpu_canvas(target, is_output=True)

    def _sync_mount_padding(self) -> None:
        """Config > Display > White padding is the paper mount's border
        (QA 2026-10-07: the slider did nothing since the mount became the one
        frame). With Paper > Padding burn on, the border is IN the picture
        (`_display_padding_fraction`), so the mount adds none."""
        stage = getattr(self, '_stage_area', None)
        setter = getattr(stage, 'set_padding_fraction', None)
        if not callable(setter):
            return
        paper = self._active_paper_state()
        burnt = paper is not None and getattr(paper, 'padding_burn', False)
        try:
            setter(0.0 if burnt else float(self._widgets.display.white_padding.value))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def refresh_canvas_padding(self) -> None:
        """Re-push the last displayed image(s) (padding setting changed live),
        and the mount's border with them.
        The compare quad follows with its own border (white for the input,
        film-base for the negative)."""
        self._sync_mount_padding()
        if self._last_canvas_image is not None:
            self._mirror_to_gpu_canvas(self._last_canvas_image,
                                       is_output=self._last_canvas_is_output)
        if self._last_compare is not None and self._gpu_canvas is not None:
            try:
                image, mode = self._last_compare
                self._gpu_canvas.set_compare(self._padded_compare(image, mode))
            except Exception:
                pass
