# build.ps1
# Builds Spektrafilm.exe as a portable one-folder Windows application using PyInstaller.
#
# Prerequisites:
#   - Upstream spektrafilm installed via uv (the `spektrafilm` and `spektrafilm_gui`
#     packages and all dependencies must be importable by the Python below).
#   - PyInstaller installed into that same environment:
#       uv pip install --python <PYTHON> pyinstaller
#   - The modified `app.py` and `io.py` from the patches/ folder applied over the
#     upstream install (see patches/munsell_stub_note.md).
#   - assets/splash.png and assets/icon.ico present.
#
# Usage:
#   1. Edit the two paths below ($Python and $AppEntry) to match your machine.
#   2. Run from the repository root:  .\build\build.ps1
#
# Output:
#   dist\Spektrafilm\   (contains Spektrafilm.exe and _internal\)
#   Zip that folder for distribution as a Release asset.

# ---- EDIT THESE TWO PATHS ----
$Python   = "C:\Users\<USER>\AppData\Roaming\uv\tools\spektrafilm\Scripts\python.exe"
$AppEntry = "C:\Users\<USER>\AppData\Roaming\uv\tools\spektrafilm\Lib\site-packages\spektrafilm_gui\app.py"
# ------------------------------

& $Python -m PyInstaller `
  --name Spektrafilm `
  --windowed `
  --noconfirm `
  --icon assets\icon.ico `
  --splash assets\splash.png `
  --collect-all spektrafilm `
  --collect-all spektrafilm_gui `
  --collect-all colour `
  --collect-all napari `
  --collect-all napari_builtins `
  --collect-all vispy `
  --collect-all numba `
  --collect-all llvmlite `
  --collect-all scipy `
  --collect-all dask `
  --collect-all rawpy `
  --collect-all OpenImageIO `
  --collect-all exiv2 `
  --collect-all imageio `
  --collect-all magicgui `
  --collect-all superqt `
  --collect-all app_model `
  --collect-all npe2 `
  --copy-metadata imageio `
  --copy-metadata numpy `
  --copy-metadata napari `
  --copy-metadata dask `
  --copy-metadata vispy `
  --copy-metadata scipy `
  --copy-metadata numba `
  --copy-metadata magicgui `
  --copy-metadata superqt `
  --copy-metadata npe2 `
  --copy-metadata app_model `
  --copy-metadata psygnal `
  --collect-submodules napari `
  $AppEntry

Write-Host ""
Write-Host "Build complete. Output is in dist\Spektrafilm\"
Write-Host "Zip that folder as Spektrafilm-win64.zip and attach it to a GitHub Release."
