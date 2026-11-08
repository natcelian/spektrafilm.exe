"""Data-only workaround for an installed The Dyeworks 2.2.1: regenerate the stale
B&W grain sub-layers in the profile JSONs from each stock's own density model.

The app reads its film profiles from disk, so this fixes the grain tonality
shift without rebuilding the app. It changes only `data/density_curves_layers`
in kodak_trix, kodak_doublex and kodak_2302, and only if the model's sub-layers
reproduce the shipped total curve. Standalone: needs Python 3 and numpy only
(the bundle's own libraries cannot be imported outside the frozen app). The
model evaluation mirrors `spektrafilm.model.density_curves.
evaluate_density_curves_layers` for the 'norm_cdfs' model these stocks use.

usage:
  python fix_installed_profiles.py "<...>\\TheDyeworks\\_internal"            dry run
  python fix_installed_profiles.py "<...>\\TheDyeworks\\_internal" --apply    write (keeps .orig backups)
  python fix_installed_profiles.py "<...>\\TheDyeworks\\_internal" --restore  put the backups back
"""
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np

STOCKS = ('kodak_trix', 'kodak_doublex', 'kodak_2302')
TOLERANCE = 0.01

_norm_cdf = np.vectorize(lambda z: 0.5 * math.erfc(-z / math.sqrt(2.0)))


def model_layers(model, log_exposure, profile_type):
    """(n_le, n_layers, n_channels) sub-layer curves of a 'norm_cdfs' model."""
    if model.get('model_type') != 'norm_cdfs':
        raise ValueError(f"unsupported model_type {model.get('model_type')!r}")
    x = np.asarray(log_exposure, dtype=float)
    centers = np.asarray(model['centers'], dtype=float)
    amplitudes = np.asarray(model['amplitudes'], dtype=float)
    sigmas = np.asarray(model['sigmas'], dtype=float)
    n_ch, n_layers = centers.shape
    sign = -1.0 if profile_type == 'positive' else 1.0
    out = np.zeros((x.size, n_layers, n_ch))
    for ch in range(n_ch):
        for i in range(n_layers):
            out[:, i, ch] = amplitudes[ch, i] * _norm_cdf(sign * (x - centers[ch, i]) / sigmas[ch, i])
    return out


def main(internal, mode):
    profiles = Path(internal) / 'spektrafilm' / 'data' / 'profiles'
    if not profiles.is_dir():
        sys.exit(f'not a Dyeworks _internal folder: {profiles} does not exist')
    failed = False
    for stock in STOCKS:
        path = profiles / f'{stock}.json'
        backup = path.with_name(path.name + '.orig')
        payload = json.loads(path.read_text(encoding='utf-8'))
        data = payload['data']
        total = np.asarray(data['density_curves'], dtype=float)
        shipped = np.asarray(data['density_curves_layers'], dtype=float)
        shipped_error = float(np.nanmax(np.abs(np.nansum(shipped, axis=1) - total)))
        if mode == '--restore':
            if not backup.exists():
                print(f'{stock}: no backup, left as is')
            elif shipped_error > TOLERANCE:
                # Not a file this script wrote (an app update shipped a new
                # one): the backup is older than it, so restoring would
                # downgrade the profile.
                print(f'{stock}: not patched by this script, backup left unused')
            else:
                shutil.copy2(backup, path)
                print(f'{stock}: restored from {backup.name}')
            continue

        try:
            regenerated = model_layers(data['density_curves_model'], data['log_exposure'],
                                       payload['info']['type'])
        except (KeyError, ValueError) as exc:
            print(f'{stock}: cannot evaluate the model ({exc}), NOT changed')
            failed = True
            continue
        model_error = float(np.nanmax(np.abs(np.nansum(regenerated, axis=1) - total)))
        print(f'{stock}: sum(sub-layers) vs total, shipped {shipped_error:.4f}, regenerated {model_error:.4f}')

        if regenerated.shape != shipped.shape or not model_error <= TOLERANCE:
            print(f'{stock}: model does not reproduce the total, NOT changed')
            failed = True
            continue
        if shipped_error <= TOLERANCE:
            print(f'{stock}: already consistent, nothing to do')
            continue
        if mode != '--apply':
            continue

        data['density_curves_layers'] = np.round(regenerated, 6).tolist()
        # Only a file still carrying the stale sub-layers gets here, so it IS
        # the original: back it up every time, replacing a backup left from an
        # earlier run against an older release.
        shutil.copy2(path, backup)
        # newline: keep the shipped LF line endings (Windows would write CRLF)
        path.write_text(json.dumps(payload, indent=4), encoding='utf-8', newline='\n')
        print(f'{stock}: written (backup {backup.name})')

    if mode not in ('--apply', '--restore'):
        print('\ndry run: nothing written. Re-run with --apply to write.')
    return 1 if failed else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else '--dry-run'))
