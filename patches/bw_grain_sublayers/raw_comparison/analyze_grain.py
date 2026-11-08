"""Compare grain-off / unpatched / patched renders of the same raw.

usage: python -I analyze_grain.py <dir with grain_off.npy, unpatched_on.npy, patched_on.npy>
Writes PNGs + metrics.txt into that dir.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import uniform_filter

D = sys.argv[1]
names = [('grain_off', 'grain off (reference)'), ('unpatched_on', 'grain on - BEFORE fix'),
         ('patched_on', 'grain on - AFTER fix')]
img = {k: np.load(os.path.join(D, k + '.npy')).mean(axis=2) for k, _ in names}   # B&W: one channel
ref = img['grain_off']
H, W = ref.shape
out = []


def to8(a):
    return Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))


def labelled_row(tiles, labels, pad=8):
    w = sum(t.width for t in tiles) + pad * (len(tiles) + 1)
    h = max(t.height for t in tiles) + pad * 2 + 22
    canvas = Image.new('L', (w, h), 40)
    draw = ImageDraw.Draw(canvas)
    x = pad
    for t, lab in zip(tiles, labels):
        canvas.paste(t, (x, pad + 22))
        draw.text((x + 2, pad + 4), lab, fill=230)
        x += t.width + pad
    return canvas


# 1. whole frame, downscaled
small = [to8(img[k]).resize((900, int(900 * H / W)), Image.LANCZOS) for k, _ in names]
labelled_row(small, [l for _, l in names]).save(os.path.join(D, 'cmp_full.png'))

# 2. pick the smoothest 384 px window in shadow / mid / highlight bands of the reference
S = 384
mean = uniform_filter(ref, S)
var = uniform_filter(ref ** 2, S) - mean ** 2
ys = np.arange(S // 2, H - S // 2, S // 4)
xs = np.arange(S // 2, W - S // 2, S // 4)
cand = [(var[y, x], mean[y, x], y, x) for y in ys for x in xs]
crops = {}
for band, lo, hi in (('shadows', 0.04, 0.30), ('midtones', 0.35, 0.65), ('highlights', 0.70, 0.95)):
    c = sorted(v for v in cand if lo <= v[1] <= hi)
    if c:
        _, m, y, x = c[0]
        crops[band] = (y - S // 2, x - S // 2, m)

for band, (y, x, m) in crops.items():
    tiles = [to8(img[k][y:y + S, x:x + S]) for k, _ in names]
    labelled_row(tiles, [f'{l}' for _, l in names]).save(os.path.join(D, f'cmp_{band}_100pct.png'))
    z = 96
    cy, cx = y + S // 2 - z // 2, x + S // 2 - z // 2
    tiles = [to8(img[k][cy:cy + z, cx:cx + z]).resize((z * 4, z * 4), Image.NEAREST) for k, _ in names]
    labelled_row(tiles, [f'{l}' for _, l in names]).save(os.path.join(D, f'cmp_{band}_400pct.png'))
    out.append(f'{band:10s} crop at (y={y}, x={x}) size {S}px, reference mean {m:.3f}')

# 3. tone shift and grain amplitude vs reference tone
bins = np.linspace(0, 1, 11)
idx = np.digitize(ref, bins) - 1
out.append('\nper reference-tone bin: mean shift (render - grain off) | grain RMS (std of render - grain off)')
out.append(f"{'tone':>11s} | {'pixels':>8s} | {'shift BEFORE':>12s} {'shift AFTER':>12s} | {'RMS BEFORE':>10s} {'RMS AFTER':>10s}")
for b in range(10):
    m = idx == b
    n = int(m.sum())
    if n < 5000:
        continue
    row = [f'{bins[b]:.1f}-{bins[b + 1]:.1f}', f'{n:8d}']
    shifts, rms = [], []
    for k in ('unpatched_on', 'patched_on'):
        dlt = img[k][m] - ref[m]
        shifts.append(f'{dlt.mean():+12.4f}')
        rms.append(f'{dlt.std():10.4f}')
    out.append(f'{row[0]:>11s} | {row[1]} | {shifts[0]} {shifts[1]} | {rms[0]} {rms[1]}')

# 4. grain size: radially averaged noise power spectrum in the midtone crop
def nps_halfpower(a):
    a = a - uniform_filter(a, 15)                     # remove image content, keep grain
    a = a[16:-16, 16:-16]
    a = a - a.mean()
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    p = np.abs(np.fft.fftshift(np.fft.fft2(a * win))) ** 2
    cy, cx = np.array(p.shape) // 2
    yy, xx = np.indices(p.shape)
    r = np.hypot(yy - cy, xx - cx).astype(int)
    radial = np.bincount(r.ravel(), p.ravel()) / np.bincount(r.ravel())
    radial = radial[1:min(cy, cx)]
    cum = np.cumsum(radial) / radial.sum()
    f50 = (np.searchsorted(cum, 0.5) + 1) / (2 * min(cy, cx))   # cycles/pixel
    return f50

out.append('\ngrain size (median frequency of the grain noise spectrum; lower = coarser grain)')
for band, (y, x, _) in crops.items():
    vals = []
    for k in ('unpatched_on', 'patched_on'):
        f = nps_halfpower((img[k] - ref)[y:y + S, x:x + S].astype(np.float64))
        vals.append(f'{f:.3f} cyc/px (~{1 / f / 2:.1f} px)')
    out.append(f'{band:10s} BEFORE {vals[0]:26s} AFTER {vals[1]}')

out.append('\nwhole-image mean: ' + ', '.join(f'{l}: {img[k].mean():.4f}' for k, l in names))
text = '\n'.join(out)
open(os.path.join(D, 'metrics.txt'), 'w').write(text)
print(text)
