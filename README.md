# Spektrafilm.exe

A portable, standalone Windows build of Andrea Volpato's **spektrafilm**, packaged as a double-clickable application with no Python installation required. This repository exists to make spektrafilm's spectral film-simulation GUI accessible to Windows users who do not want to set up a Python environment, `uv`, or build the application themselves.

This is a packaging project. All of the simulation science, the GUI, and the photographic model are Andrea Volpato's work. This repository contributes only the Windows freezing, the launcher, and the fixes required to make the application run correctly as a self-contained executable.

---

## Relation to Andrea Volpato's spektrafilm

spektrafilm is a research project by **Andrea Volpato**: an end-to-end, physically based spectral simulation of the analog photographic pipeline. It models how film-stock data, dye couplers, enlarger settings, grain, halation, and scanning shape a final image, grounded in published spectral measurements rather than a generic "film look."

- Upstream project: https://github.com/andreavolpato/spektrafilm
- Development discussion: https://discuss.pixls.us/t/spectral-film-simulations-from-scratch/48209

This build tracks upstream **v0.3.2**. It does not modify the simulation model. The only source-level changes are the minimum required to make the frozen executable locate its data and run on Windows (documented in the Log below). For the meaning of every control, the methodology, and the model itself, refer to Andrea Volpato's repository and the pixls.us thread. If you find this useful, please consider supporting Andrea's work directly.

---

## Download and Releases

Builds are published under the **Releases** tab of this repository.

- Download the latest `Spektrafilm-win64.zip` from Releases.
- Extract the entire folder anywhere you like (Desktop, Documents, a USB drive).
- Run `Spektrafilm.exe` from inside the extracted folder.

The application is distributed as a one-folder build: a folder containing `Spektrafilm.exe` alongside an `_internal` directory holding the bundled Python runtime and dependencies. **Keep the folder intact.** Do not move `Spektrafilm.exe` out of the folder on its own; it needs the `_internal` directory beside it. You may create a desktop shortcut to the exe.

### Releases Log

- **v.a.0.1** — First public Windows build. Based on upstream spektrafilm v0.3.2. PyInstaller one-folder build with splash screen and custom icon.

---

## Status

**Working Alpha — Windows x64 only.**

This is an early, experimental build. It runs the full spektrafilm pipeline and GUI on Windows without a Python environment. Expect rough edges. Full-resolution processing is slow by nature of the spectral model (see Import Settings). There is no installer; it is a portable folder. macOS and Linux are not provided here; users on those platforms should run upstream spektrafilm directly.

---

## Import Settings

These notes follow Andrea Volpato's guidance for the underlying application.

The simulation expects **linear, scene-referred** input, with or without a transfer function.

**RAW files** can be imported directly through the import-raw panel (RAW decoding is handled internally). The recommended manual workflow, per upstream, is to open RAW files in a tool such as darktable, deactivate the non-linear mappings (filmic or sigmoid), adjust exposure to preserve all information while avoiding clipping, then export a 32-bit float TIFF in linear ProPhoto RGB.

**Prepared linear images** can be loaded through the file loader. The loader imports 16-bit and 32-bit image files as new layers using OpenImageIO. PNG, TIFF, and EXR are known to work, and other formats may work too. For best results keep the image scene-referred and linear, ideally a 16-bit or 32-bit float TIFF/EXR in a wide-gamut color space such as linear Rec2020 or linear ProPhoto RGB.

**Performance note.** The simulation is slow for full-resolution images. Adjust most values using **PREVIEW**, which works on a scaled image. When you need a final image, use **SCAN**, which bypasses image scaling.

For the full list of controls and their behavior, refer to the upstream documentation and the in-GUI tooltips.

---

## Performance

This build keeps numba's JIT compilation active, so core processing speed matches the native Python application rather than being penalized by packaging. Reaching this took some trial and error: an earlier route (documented in the Log) ran 2–4x slower because numba was disabled in the frozen build. The current PyInstaller build resolves that.

The raw spectral computation runs on the same interpreter and the same numba-compiled machine code as the native application, so a full SCAN is not faster here, it is identical. What can feel more fluid is everything around that: launch, GUI responsiveness, switching settings, and triggering previews. Because all dependencies are bundled and laid out for fast loading, startup and general interaction carry less overhead than launching the native app and importing its large dependency tree from scattered files. In practice the app shell can feel noticeably snappier even though the math itself is unchanged.

That said, spektrafilm is computationally heavy by design. It is a full spectral simulation, not a LUT, so even at full speed PREVIEW and SCAN take real time. Processing time scales with image resolution and depends on your hardware. Use PREVIEW for adjustments and SCAN for finals, as noted above. This is the expected cost of a physically based model, not a packaging defect.

---

## Build Requirements and Installation

This section is for anyone who wants to reproduce the build, not for end users (end users only need the Releases download).

The build was produced on Windows x64 with Python 3.13, using `uv` to manage the upstream spektrafilm tool environment, and PyInstaller to freeze it. Required components:

- A working install of upstream spektrafilm via `uv` (the `spektrafilm` and `spektrafilm_gui` packages and all their dependencies).
- A C/C++ toolchain is **not** required for this packaging route (PyInstaller bundles the existing CPython interpreter rather than compiling to C).
- PyInstaller, installed into the same environment as spektrafilm.

Install PyInstaller into the spektrafilm `uv` tool environment:

```powershell
uv pip install --python <path-to>\uv\tools\spektrafilm\Scripts\python.exe pyinstaller
```

Build command (run from the folder containing your `splash.png` and `icon.ico`):

```powershell
<path-to>\uv\tools\spektrafilm\Scripts\python.exe -m PyInstaller `
  --name Spektrafilm `
  --windowed `
  --noconfirm `
  --icon icon.ico `
  --splash splash.png `
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
  <path-to>\uv\tools\spektrafilm\Lib\site-packages\spektrafilm_gui\app.py
```

Notes on the flags:

- `--collect-all numba` and `--collect-all llvmlite` are essential. They bundle numba and its LLVM backend (including the binary `llvmlite.dll`) so that numba's JIT compilation works at runtime. Without these, the hot numerical loops fall back to pure Python and the application runs several times slower.
- `--copy-metadata` entries bundle each package's distribution metadata, which several dependencies (notably `imageio` and napari's plugin system) query at runtime via `importlib.metadata`. Missing metadata causes startup `PackageNotFoundError` failures.
- `--splash` provides the native loading image. The splash is dismissed from within the application code (see Log).

The output is `dist\Spektrafilm\`, a portable one-folder build. Zip that folder for distribution.

---

## Roadmap

- [x] Compilation to an `.exe` file
- [ ] Reworked UI/UX
- [ ] Side-by-side view: negative/positive or before/after comparison
- [ ] GPU-accelerated preview and scan
- [ ] Batch processing: apply a reference look across multiple frames, with propagation and normalization
- [ ] Dedicated export panel with print-ready TIFF and web JPEG targets
- [ ] Additional basic controls for final post processing
- [ ] Keeping the build in sync with upstream spektrafilm releases

Roadmap items are aspirational and not committed timelines.

---

## Log

A record of what was done to produce this build, including the dead ends, in case it helps anyone attempting the same.

**Initial attempts with Nuitka.** The first packaging route used Nuitka, compiling the Python sources to C. This ran into several obstacles in sequence:

- Nuitka's MinGW backend is not supported on Python 3.13, so MSVC was used.
- MSVC rejected the colour-science Munsell datasets with `error C2026: string too big`. Nuitka serializes large Python constants (the Munsell renotation tuples) into C string literals that exceed MSVC's literal size limit. As spektrafilm does not use the Munsell notation system, the two dataset modules (`colour/notation/datasets/munsell/all.py` and `real.py`) were replaced with minimal stubs exposing the same names as empty tuples, which let compilation proceed.
- The `--python-flag=no_site` and `no_warnings` flags stripped standard-library modules and were removed.
- `spektrafilm/utils/io.py` loads its CSV data (filters, ICC profiles, etc.) via `importlib.resources`, which resolves data directories as importable packages. This fails under a frozen app whose importer only knows compiled modules. A small shim was added to `io.py` so that `pkg_resources.files(...)` resolves data paths via `__file__` relative to the installed package instead, which works in both normal and frozen contexts.
- Even after the app launched, performance was 2–5x slower than running spektrafilm normally. The cause was numba: Nuitka's own documentation states that numba "is currently not working for standalone." With numba's JIT disabled in the frozen build, spektrafilm's hot loops ran in pure-Python fallback. This is not fixable via Nuitka flags, so the Nuitka route was abandoned for performance reasons.

**Switch to PyInstaller.** PyInstaller bundles the genuine CPython interpreter rather than compiling to C, so numba's JIT works normally and full processing speed is preserved. The `io.py` shim and the Munsell handling were kept (they are harmless under PyInstaller and the stubs are not needed for correctness, since PyInstaller never hits the MSVC string limit). The PyInstaller build required:

- `--collect-all` for the heavy data-driven dependencies (colour, napari, vispy, scipy, dask, rawpy, OpenImageIO, exiv2, imageio, magicgui, superqt, app_model, npe2) so their data files, binaries, and submodules are bundled.
- Explicit `--collect-all numba` and `--collect-all llvmlite` to preserve JIT performance.
- `--copy-metadata` for the packages that read their own distribution metadata at runtime, resolving a chain of startup `PackageNotFoundError` failures (imageio first, then several napari plugin-system dependencies).

**Splash and window behavior.** A loading splash image and a custom icon were added at build time. A small block was added to the application's `main()` to maximize the main window on launch and to dismiss the PyInstaller splash (`pyi_splash.close()`) once the main window is shown, wrapped so it is a no-op when running as ordinary Python.

**Result.** A portable, double-clickable Windows build that launches with a splash screen, opens maximized, imports and displays RAW files correctly, and runs at full numba-accelerated speed matching the native Python application.

---

## License

This project is licensed under **GPL-3.0**, the same license as upstream spektrafilm, of which it is a derivative work. All credit for the simulation, model, and GUI belongs to **Andrea Volpato**. See the upstream repository for the original work: https://github.com/andreavolpato/spektrafilm
