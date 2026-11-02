"""The ratings part of GuiController: its methods, moved verbatim from controller.py.

GuiController takes RatingsMixin as a base.
"""
from __future__ import annotations

from spektrafilm_gui.shell_layout import set_status


class RatingsMixin:
    """GuiController's ratings methods."""

    def _current_rating(self) -> int:
        current = self._roll.current
        if current is not None:
            return self._roll.rating_for(current)
        # A single image's rating is ITS rating. It used to follow every image
        # opened after it, and into their EXIF on export.
        if self._single_rating_path != self._current_input_path:
            return 0
        return int(self._single_rating)

    def set_current_rating(self, rating: int) -> None:
        """Metadata panel stars: rate the CURRENT frame (0-5)."""
        rating = min(max(int(rating), 0), 5)
        current = self._roll.current
        if current is not None:
            self._roll.set_rating(current, rating)
            if self._rating_filter:
                self._roll_refresh_view()
        else:
            self._single_rating = rating
            self._single_rating_path = self._current_input_path
        self._sync_rating_panel()          # reflect keyboard shortcuts in the stars
        set_status(self._viewer,
                   f'Rating: {rating} of 5' if rating else 'Rating cleared')
