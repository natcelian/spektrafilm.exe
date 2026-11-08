# The Dyeworks

**The Dyeworks** is a GPL-3 fork of [spektrafilm](https://github.com/andreavolpato/spektrafilm) by Andrea Volpato. It runs his spectral film model, the physics that turns a camera RAW into a virtual negative, prints it under a virtual enlarger and scans the print, with a rebuilt interface and a GPU-resident pipeline on top.

The science is his and stays his. This repository is not the reference implementation; **upstream's Python runtime is**, and it is where the model is grown. `NOTICE.md` is the full provenance and licence record: who wrote which part, the fork point, and the terms each bundled piece carries.

![The Dyeworks home screen: an empty roll, ready for frames](img/readme/dyeworks_home.png)

![The Dyeworks with a frame scanned through Kodak Gold 200 and Supra Endura](img/readme/dyeworks_main.png)

## Speed

The same fixture (a 24 MP Fujifilm X-H1 frame), the same settings on both sides, timed through `simulate()` on an i7-14700KF with an RTX 4070 SUPER. Upstream is spektrafilm 0.3.4, which runs on the CPU only. Each grain mode is compared with the same mode upstream. Seconds, lower is better.

| 24 MP | upstream CPU | Dyeworks CPU | Dyeworks GPU | GPU vs upstream |
|---|---|---|---|---|
| fast grain statistics (what the app uses) | 46.8 | 44.6 | **1.4** | 33x |
| grain off | 34.9 | 32.0 | **1.4** | 25x |

The Dyeworks CPU path is the parity reference and lands within a few percent of upstream, as it should. The GPU path is pinned to it by tests, kernel by kernel. Exact statistics draw their random numbers through scipy on the CPU, so the GPU cannot help much there; the app always uses the fast statistics. Full table, method and raw numbers are in the release notes.

## What it does

A photograph passes through four stages, each of which you can steer.

**Film.** 31 film profiles as of v2.2.1, built from manufacturer datasheet measurements: the Kodak Portra, Ektar, Gold, Ultramax and Kodachrome families, the Vision2 and Vision3 motion-picture stocks, Fujifilm C200, Pro 400H, X-Tra, Velvia and Provia, plus black-and-white Double-X and Tri-X. Colour negative, colour reversal and B&W are all modelled. Push and pull, per-channel development chemistry, emulsion aging (fog, dye loss, speed change) and bleach bypass are parameters, not presets. Two exposure controls: Camera exposure is the real exposure and moves the brightness; Film exposure changes only how the film responds and holds the brightness, as the old auto compensation did.

**Print.** 6 papers as of v2.2.1: the Kodak Endura family, Ektacolor Edge and Fujifilm Crystal Archive Type II, exposed through a virtual colour enlarger with dichroic Y/M/C filtration, preflashing, and toe and shoulder shaping.

**Scan.** Spectral reduction to RGB, glare, lens blur and unsharp masking, then a display-referred finishing stage: exposure and white point, per-channel gains, contrast, saturation and vibrance, per-hue saturation, split toning, and a Lightroom-style point curve.

**Grain** is simulated rather than overlaid, in four tiers: a layered dye-cloud particle model (the default), a fast filtered-noise preview matched to its statistics, an RMS-granularity-calibrated tier, and a Monte Carlo Boolean grain field. Magnified, the dye clouds are really there:

![Magnified grain, showing the dye clouds](img/readme/gui_grain_magnified.png)

![The Reco tab: Denoise, Defects, Lens correction and Highlights](img/readme/dyeworks_reco.png)

Around the pipeline:

- **Reco.** A tab of repairs made to the RAW before the film sees it. **Denoise**: Ansel's Clean and Deep networks on the mosaic, RawNIND's Sharp after demosaic, run once per frame in the background and kept. **Defects**: sensor dust found in the frame, or with the help of the whole roll so dust hidden by texture is found too, switched off spot by spot and divided out of the image (Fast, Precise or Heal), plus a click to add one by hand, and drag-a-circle tools to ignore a zone or to correct only inside one; the rings follow the picture through the lens correction, Barrel, rotation and crop. **Lens correction**: lensfun, and Barrel and Vignette sliders. **Highlights**: Clip, Blend or Rebuild.
- **Library.** Film, paper and filter selectors open one library with favourites, recents, tags and your own USER stocks. Taking filters (a second filter, an ND slot) are part of the camera.
- **Custom stocks.** A `.sfstock` recipe edits a base stock's density curves through an H&D plot, without touching its spectral data.
- **Rolls.** A `.sfroll` file holds a set of frames and their per-frame edits; the filmstrip carries ratings and a light-table view.
- **Presets.** `.sfpreset` files, saved and applied from the library panel.
- **Versions.** Attempts are kept, never overwritten, and any past one reloads exactly.
- **Test strips.** EV bands rendered on the image, as a darkroom test strip.
- **Scopes.** Histogram, waveform, vectorscope, and the film's own H&D curve.
- **Export.** 8-bit JPEG, or true 16-bit TIFF and PNG taken from the full-precision float render, never an 8-bit buffer promoted. Latitude is chosen at write time. Linear DNG hands the scan over as a film scanner would.
- **LUT export.** The current look as a `.cube`, `.3dl`, Hald-CLUT PNG or Lumix realtime LUT, with an optional standalone OCIO 2 config.

Rendering runs on the GPU through `wgpu` (Vulkan on the test machine), in nine resident chains covering exposure, development, grain, halation, print and scan, with tiling for large frames and an automatic fall back to the CPU path, which stays the parity reference. Every GPU kernel is pinned to its CPU result by a test.

## The model

The simulation starts from published spectral data. Kodak Portra 400's datasheet (e4050, 2016) gives the log sensitivity of each layer, the characteristic curves, and the dye absorption spectra:

![Data extracted from the Kodak Portra 400 datasheet](img/readme/example_data_kodak_portra_400.png)

From linear RGB the pipeline reconstructs a spectrum, exposes it onto the negative's sensitivities, develops dye densities through the characteristic curves and the coupler model, projects the negative onto paper, and scans the print under a chosen illuminant:

![The colour photography process](img/readme/pipeline_color_digital_management.png)

Couplers are what make it look like film rather than a curve. Masking couplers give unexposed negative its orange base; DIR couplers, released where density forms, inhibit neighbouring layers, raising saturation and, when allowed to diffuse, local contrast. Andrea Volpato's [write-up](https://discuss.pixls.us/t/spectral-film-simulations-from-scratch/) is the best introduction to the framework.

## Install and run

**The application is a Windows build**, published on the [releases page](https://github.com/natcelian/spektrafilm.exe/releases). `The Dyeworks 2.2.1` ships as a one-folder PyInstaller bundle: run `TheDyeworks.exe` from it. One-folder rather than one-file is deliberate, because PySide6 is LGPL and a recipient must be able to relink it (`NOTICE.md` §5.1).

To build it from a checkout:

```bash
python -m PyInstaller --clean --noconfirm Spektrafilm.spec
python tools/finish_build.py     # lifts LICENSE/NOTICE/README/icons to the bundle root
python tools/frozen_smoke.py     # 29 checks; a build that fails this does not ship
```

### Developer setup

Python 3.13, on Windows. From a clone:

```bash
uv venv --python 3.13 .venv
uv pip install -e ".[dev]"
```

That gives an editable install of the checkout, so `import spektrafilm` resolves here and switching branch switches the code under test. `pip install -e ".[dev]"` works the same way in a virtualenv of your own. The `dev` extra adds `pytest` and PyOpenColorIO, which validates emitted OCIO configs.

Two commands are installed:

```bash
spektrafilm                      # the GUI
spektrafilm-lut list film        # the LUT exporter's registries
spektrafilm-lut build --film kodak_portra_400 --print kodak_portra_endura \
                      --input vlog --output srgb --topology 1lut --resolution 33
```

Tests:

```bash
python -m pytest tests -q                 # everything
python tools/affected_tests.py --run      # only what your change can reach
```

Regression baselines are committed `.npz` files under `tests/baselines/`, checked by `tests/test_regression_baselines.py` and regenerated deliberately with `python scripts/regenerate_test_baselines.py`, never automatically during a run.

## Using the runtime directly

The runtime is importable and has no GUI or LUT dependencies: linear RGB in, linear RGB out. `examples/run_simulation.py` is a working script, and the short form is:

```python
from spektrafilm import init_params, simulate

params = init_params(film_profile='kodak_portra_400',
                     print_profile='kodak_portra_endura')
result = simulate(image, params)     # image: linear RGB, (H, W, 3) float
```

`docs/PARAMS.md` lists every parameter the GUI can set and the engine field behind it, which is the map to drive it from code.

## Where the code is

Start with **[`CODEMAP.md`](CODEMAP.md)**: the pipeline as a node graph, the GPU layer and its resident chains, the application's modules, and where to look for a given thing. Four generated companions carry the detail, regenerated with `python tools/gen_docs.py --all`:

| File | What it answers |
|---|---|
| [`docs/INDEX.md`](docs/INDEX.md) | every file: lines, docstring, what it imports, which tests cover it |
| [`docs/PARAMS.md`](docs/PARAMS.md) | every GUI field, and the engine parameter it writes |
| [`docs/PROVENANCE.md`](docs/PROVENANCE.md) | what came from which reference, and how that was established |
| `docs/map/index.html` | the same graph, interactive and offline |

The three packages under `src/` are upstream's, and keep his names so future merges stay possible: `spektrafilm` (the runtime), `spektrafilm_gui` (the application) and `spektrafilm_lut_creator` (LUT and OCIO export). The runtime depends on neither of the others.

## Preparing input images

RAW import is built in: choose a white balance mode, pick the file, and the importer decodes through `rawpy` into the chosen input colour space. `reprocess raw` re-imports with new settings.

You can also load prepared linear images, 16- or 32-bit TIFF or EXR, scene-referred, in a wide-gamut space such as linear Rec2020 or linear ProPhoto RGB. [darktable](https://www.darktable.org/) is a good source: disable `filmic` or `sigmoid`, set exposure to preserve highlights, and export 32-bit float linear ProPhoto.

## Where this lives

- **Repository:** <https://github.com/natcelian/spektrafilm.exe>
- **Releases:** <https://github.com/natcelian/spektrafilm.exe/releases>, where the
  Windows build is published
- **Issues:** <https://github.com/natcelian/spektrafilm.exe/issues>

Two different places for two different questions, and the split matters.

**Bugs in this fork** go to the issue tracker above: the Dyeworks interface, the
GPU pipeline, the roll and preset files, the Windows build, anything this fork
added or changed.

**Questions about the film model** go upstream, where Andrea Volpato develops
it: [discuss.pixls.us](https://discuss.pixls.us/c/software/spektrafilm/43). A
stock's curves, coupler behaviour, the spectral method, why a profile renders as
it does: those are his subject, not this fork's, and asking here would only put
a fork between you and the person who knows the answer.

## Credit and support

The spectral film model, the profiles and the original program are **Andrea Volpato's** work:

- the upstream project: <https://github.com/andreavolpato/spektrafilm>
- the discussion where it is developed: [discuss.pixls.us](https://discuss.pixls.us/c/software/spektrafilm/)
- **support the upstream project**: [Buy Andrea a coffee](https://buymeacoffee.com/andreavolpato). That link funds spektrafilm, not this fork.

If you use the model in your own work, cite it: see `CITATION.cff`.

Other implementations of the same model, each worth knowing:

- [vkdt](https://jo.dreggn.org/vkdt/src/pipe/modules/filmsim/readme.html), a fast Vulkan implementation by [hanatos](https://github.com/hanatos).
- [the OFX plugin](https://spektrafilm.114c.de/) by Aedan Diez ([chaert-s](https://github.com/chaert-s)), for Resolve and other OFX hosts.
- [spektrafilm-rs](https://github.com/turbasvin/spektrafilm-rs), a Rust implementation by [turbasvin](https://github.com/turbasvin).
- [ART](https://artraweditor.github.io/SpectralFilmSimHowto), a LUT-based bridge by [agriggio](https://github.com/agriggio).

## Licence

Three bodies of work travel together, and each keeps its own terms:

- **Program code**, GNU GPL v3, upstream's and this fork's alike. Full text in [`LICENSE`](LICENSE).
- **Film profiles and LUTs**, CC BY-SA 4.0, by Andrea Volpato's deliberate choice, with additional LUT terms in [`SPEKTRAFILM_LICENSE.txt`](SPEKTRAFILM_LICENSE.txt).
- **Bundled third-party components**, their own licences, listed per component.

[`NOTICE.md`](NOTICE.md) is the authoritative record: the modification notice GPL-3 §5(a) requires, the copyright holders, the three tiers above, what this fork adapted from other projects, and the obligations that come with the bundled libraries and fonts. Read it before redistributing anything.

## Localization (i18n)

- **Simplified Chinese (简体中文)**: Available under [`patches/zh_CN/`](patches/zh_CN/). Provides comprehensive darkroom chemistry, spectral simulation, and photographic terminology translations across all UI stages, sections, and controls. See [`patches/zh_CN/README.md`](patches/zh_CN/README.md).

Sample images are from [signatureedits.com](https://www.signatureedits.com/).
