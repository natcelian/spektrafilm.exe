# -------------------------------------------------------------------------
# FORK CONTRACT -- upstream has this file too, and both sides edit it.
# OURS: the section list derived from fields(GuiState) (upstream's
#   hand-kept name tuple and SECTION_STATE_ACCESSORS are gone, d339cce),
#   preview_max_size held as a machine preference across loads (NAT
#   2026-07-22), auto_preview never applied from a loaded state (NAT
#   2026-07-18), scan_film round-tripped with the simulation section,
#   and the restore hook (restoring / is_restoring / on_restore) that lets
#   controller_crop tell a stored look going back from the user's own edit.
# UPSTREAM: the apply/collect API shape; its gui_only nesting no longer
#   exists in our GuiState. No upstream commits here since 28bf883.
# ON CONFLICT: KEEP OURS. Their accessor table cannot fit our flat state;
#   if they add a section, add the field to our GuiState (state.py) and
#   this file picks it up by itself -- port only a new special case.
# Convention and rationale: docs/reports/2026.08.20_fork_contracts.md
# -------------------------------------------------------------------------

from __future__ import annotations

import weakref
from contextlib import contextmanager
from dataclasses import fields
from typing import Protocol, cast

from spektrafilm_gui.state import (
    GuiState,
    PROJECT_DEFAULT_GUI_STATE,
)
from spektrafilm_gui.widgets import WidgetBundle


class SupportsSectionState(Protocol):
    def set_state(self, state: object) -> None:
        ...

    def get_state(self) -> object:
        ...


DEFAULT_GUI_STATE = PROJECT_DEFAULT_GUI_STATE
GUI_STATE_SECTION_NAMES = tuple(field_info.name for field_info in fields(GuiState))


def _get_stateful_widget(widgets: WidgetBundle, section_name: str) -> SupportsSectionState:
    return cast(SupportsSectionState, getattr(widgets, section_name))


#: Above 0 while a stored look is being put back into the widgets (undo,
#: presets, roll frames, versions, pasted looks), so a listener can tell
#: those value changes from the user's own (controller_crop opens the crop
#: session on an angle the USER changes).
_restoring = 0
#: Called as a restore begins, before any value moves (controller_crop
#: applies an open crop session there, so the look put back is not
#: overwritten by the session's frame later). Weak, so a controller that
#: goes away takes its listener with it.
_restore_listeners: list = []


def on_restore(bound_method) -> None:
    _restore_listeners.append(weakref.WeakMethod(bound_method))


@contextmanager
def restoring():
    global _restoring
    _restoring += 1
    try:
        if _restoring == 1:
            for ref in tuple(_restore_listeners):
                listener = ref()
                if listener is None:
                    _restore_listeners.remove(ref)
                else:
                    listener()
        yield
    finally:
        _restoring -= 1


def is_restoring() -> bool:
    return _restoring > 0


def apply_gui_state(state: GuiState, *, widgets: WidgetBundle) -> None:
    apply_gui_state_sections(state, widgets=widgets, section_names=GUI_STATE_SECTION_NAMES)


def apply_gui_state_sections(
    state: GuiState,
    *,
    widgets: WidgetBundle,
    section_names: tuple[str, ...],
) -> None:
    with restoring():
        _apply_sections(state, widgets=widgets, section_names=section_names)


def _apply_sections(state: GuiState, *, widgets: WidgetBundle,
                    section_names: tuple[str, ...]) -> None:
    for section_name in section_names:
        if section_name == 'display':
            # 2026-07-22; see design/DECISIONS.md#d-state_bridge-771
            current_preview_size = widgets.display.preview_max_size.value
            widgets.display.set_state(state.display)
            widgets.display.preview_max_size.value = current_preview_size
            continue
        _get_stateful_widget(widgets, section_name).set_state(getattr(state, section_name))
    if 'simulation' in section_names:
        # 2026-07-18; see design/DECISIONS.md#d-state_bridge-772
        widgets.simulation.set_scan_film_value(state.simulation.scan_film)
        widgets.simulation.reset_scan_for_print_value()


def collect_gui_state(
    *,
    widgets: WidgetBundle,
) -> GuiState:
    gui_state = GuiState(**{section_name: _get_stateful_widget(widgets, section_name).get_state() for section_name in GUI_STATE_SECTION_NAMES})
    gui_state.simulation.auto_preview = widgets.simulation.auto_preview_value()
    gui_state.simulation.scan_film = widgets.simulation.scan_film_value()
    return gui_state