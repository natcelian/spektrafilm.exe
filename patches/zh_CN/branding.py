"""What the product is called, in one place.

2026-08-25; see design/DECISIONS.md#d-branding-236

THE PRODUCT IS RENAMED; THE ENGINE IS NOT. `spektrafilm` is the name of
Andrea Volpato's spectral model and of the packages this fork is built on,
and it stays exactly where it is:

  * the Python packages (`spektrafilm`, `spektrafilm_gui`,
    `spektrafilm_lut_creator`) keep their names. Renaming them would put a
    conflict on every path of every future merge from upstream -- 331
    commits of shared history, an `upstream` remote kept deliberately intact,
    and a B&W release still to come -- in exchange for nothing a user sees.
  * `.sfroll`, `.sfpreset` and `.sfstock` keep their extensions, so every
    file already saved goes on opening.
  * the `Spektrafilm.Roll` ProgId keeps its name. It is an identifier, not a
    label; the label beside it is `SFROLL_DESCRIPTION` and that does change.
  * `QSettings('spektrafilm', 'spektrafilm')` keeps its keys, so nobody's
    canvas swatch, dialog directories or recent rolls are forgotten.
  * `SPEKTRAFILM_*` environment flags keep their names.

2026-08-25; see design/DECISIONS.md#d-branding-237

So: `APP_NAME` is what a person reads, and `spektrafilm` is what the code
imports. Anything that shows a name to a user reads from here.
"""

from __future__ import annotations
from spektrafilm_gui.app_settings import app_settings
import pathlib as _pathlib
from qtpy import QtCore

#: The product, as it is spoken and written. Includes the article: it is
#: "The Dyeworks", the way a place is named, not a bare noun.
APP_NAME = 'The Dyeworks'

#: The name without its article, for a sentence that already has one --
#: "a Dyeworks roll", "Not a Dyeworks roll file."
APP_NAME_BARE = 'Dyeworks'

#: The line under the wordmark. Unchanged: it says what the app DOES, and
#: that has not.
APP_TAGLINE = '全光谱物理胶片仿真'

#: The engine underneath, credited where a person can see it. The science is
#: Andrea Volpato's and the fork does not pretend otherwise.
ENGINE_NAME = 'spektrafilm'

# 2026-09-03; see design/DECISIONS.md#d-branding-232
WINDOW_TITLE = f'{APP_NAME} - 全光谱物理胶片模拟'

#: The app icon, as a package resource. `extra/icon.ico` is the SAME artwork
#: PyInstaller stamps on the executable; a copy lives in the package so the
#: window is not bare when running from source, where `extra/` is a sibling
#: of the repo rather than something the package can reach.
APP_ICON_ASSET = 'app_icon.ico'
# 2026-09-07; see design/DECISIONS.md#d-branding-233
APP_ICON_TRANSPARENT_ASSET = 'app_icon_transparent.ico'


# 2026-08-25; see design/DECISIONS.md#d-branding-234
APP_VERSION = '2.2.1'


def engine_version() -> str:
    """The spektrafilm package's own version, for the credit line."""
    try:
        from importlib.metadata import version

        return version(ENGINE_NAME)
    except Exception:
        return ''



# 2026-09-25; see design/DECISIONS.md#d-branding-235
APP_ICON_VARIANTS: dict[str, tuple[str, str, str | None]] = {
    'default': ('Default', APP_ICON_ASSET, APP_ICON_TRANSPARENT_ASSET),
    'narrower': ('Narrower', 'icons/narrower.ico', None),
    'narrow': ('Narrow', 'icons/narrow.ico', None),
    'standard': ('Standard', 'icons/standard.ico', None),
    'curves': ('Curves', 'icons/curves.ico', None),
    'logo': ('Logo', 'icons/logo.ico', None),
    's-01': ('S 01', 'icons/s-01.ico', None),
    's-02': ('S 02', 'icons/s-02.ico', None),
    'sa-01': ('SA 01', 'icons/sa-01.ico', None),
    'sa-02': ('SA 02', 'icons/sa-02.ico', None),
}
DEFAULT_ICON_KEY = 'default'
_ICON_SETTINGS_KEY = 'ui/app_icon'


def _settings():
    return app_settings()


def current_icon_key() -> str:
    """The chosen icon variant; the default when unset or unknown."""
    try:
        key = str(_settings().value(_ICON_SETTINGS_KEY, DEFAULT_ICON_KEY)
                  or DEFAULT_ICON_KEY)
    except Exception:
        return DEFAULT_ICON_KEY
    return key if key in APP_ICON_VARIANTS else DEFAULT_ICON_KEY


def set_icon_key(key: str) -> bool:
    """Record the choice. Reports whether it was a real key."""
    if key not in APP_ICON_VARIANTS:
        return False
    try:
        _settings().setValue(_ICON_SETTINGS_KEY, key)
    except Exception:
        return False
    return True


def app_icon_path(key: str | None = None):
    """The app icon of the chosen variant, or None if it is missing.

    Resolved beside this module the way every other GUI asset is
    (`theme_fonts._bundled_dir`, `left_panel._mark_pixmap`), so it works
    from source and inside the frozen build alike -- `collect_all
    ('spektrafilm_gui')` in the spec brings the whole assets tree. A variant
    whose file is missing falls back to the default rather than to nothing.
    """
    key = key or current_icon_key()
    return _asset(APP_ICON_VARIANTS[key][1]) or _asset(APP_ICON_ASSET)


def app_icon_transparent_path(key: str | None = None):
    """The brand lockup's mark: the icon with no squircle behind it.

    Falls back to the variant's opaque icon when it has no transparent cut,
    so a missing asset costs the squircle rather than the mark.
    """
    key = key or current_icon_key()
    transparent = APP_ICON_VARIANTS[key][2]
    return (_asset(transparent) if transparent else None) or app_icon_path(key)


def _asset(name: str):
    path = _pathlib.Path(__file__).resolve().parent / 'assets' / name
    return path if path.is_file() else None
