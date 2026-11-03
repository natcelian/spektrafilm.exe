// Phase D2 (2026-09-18): scan-time geometry on the GPU -- the two resamplers
// scanning.scan() applies LAST, on the finished scan RGB (gpu/scan_geometry.py).
//
//   DESQUEEZE = skimage.transform.rescale(order=3) = resize ->
//     [anti-alias gaussian, mirror, when compressing] -> ndi.zoom(order=3,
//     grid_mode=True, mode='mirror') -> clip to the input's own range.
//     Separable along the ONE scaled axis: a tensor-product cubic B-spline
//     evaluated at the knots of the unscaled axis gives that axis's samples
//     back, so only the scaled axis is prefiltered and interpolated.
//     Passes: aa_blur (optional) -> prefilter -> zoom_cubic.
//   SKEW = skimage.transform.rotate(order=1, mode='constant', cval=SKEW_FILL)
//     = _warp_fast bilinear per channel, a neighbour outside the picture
//     reading SKEW_FILL, sampled at M @ (c, r, 1).
//
// Both written against an f64 numpy reference that matches skimage to
// ~1e-15 (desqueeze) and exactly (skew). Sample positions are computed
// EXACTLY -- as an integer ratio (desqueeze) or an affine with its integer
// and fractional parts carried apart (skew) --
// because an f32 coordinate at x ~ 6000 px is only good to ~5e-4 px.

// -------------------------------------------------------------- desqueeze ---
struct DParams {
    n_lines: u32,     // lines along which the resample runs
    n_in: u32,        // samples per line in
    n_out: u32,       // samples per line out
    radius: u32,      // anti-alias kernel radius (aa_blur only)
    in_line: u32,     // element stride between lines (in / blur / coef buffers)
    in_pos: u32,      // element stride along a line (in)
    out_line: u32,    // element stride between lines (out)
    out_pos: u32,     // element stride along a line (out)
    clip_lo: f32,
    clip_hi: f32,
    clip_from_buf: u32, // zoom_cubic: 1 = clip range from dweights[0..1]
                        // (minmax_final's output, fused chain), 0 = the two above
    _p1: u32,
};

@group(0) @binding(0) var<uniform>             dp: DParams;
@group(0) @binding(1) var<storage, read>       dsrc: array<f32>;
@group(0) @binding(2) var<storage, read_write> ddst: array<f32>;
@group(0) @binding(3) var<storage, read>       dweights: array<f32>;

// ndimage 'mirror': reflect about the end SAMPLES (d c b | a b c d | c b a).
fn mirror_idx(i: i32, n: i32) -> i32 {
    if (n == 1) {
        return 0;
    }
    let period = 2 * n - 2;
    var j = abs(i) % period;
    if (j > n - 1) {
        j = period - j;
    }
    return j;
}

fn in_index(line: u32, pos: u32, ch: u32) -> u32 {
    return line * dp.in_line + pos * dp.in_pos + ch;
}

// Anti-alias gaussian along the line (scipy gaussian_filter1d, truncate 4,
// mirror); weights computed host-side exactly as scipy's _gaussian_kernel1d.
@compute @workgroup_size(64, 1, 1)
fn aa_blur(@builtin(global_invocation_id) gid: vec3<u32>) {
    let pos = gid.x;
    let line = gid.y / 3u;
    let ch = gid.y % 3u;
    if (pos >= dp.n_in || line >= dp.n_lines) {
        return;
    }
    let r = i32(dp.radius);
    let n = i32(dp.n_in);
    var acc = 0.0;
    for (var k = -r; k <= r; k = k + 1) {
        let p = mirror_idx(i32(pos) + k, n);
        acc = acc + dweights[u32(k + r)] * dsrc[in_index(line, u32(p), ch)];
    }
    ddst[in_index(line, pos, ch)] = acc;
}

// Cubic B-spline prefilter along one line (scipy ni_splines.c apply_filter,
// order 3, mirror boundary): gain, causal pass with the mirror init, then
// the anticausal pass. One invocation per (line, channel); in place on ddst
// after copying the line from dsrc.
@compute @workgroup_size(64, 1, 1)
fn prefilter(@builtin(global_invocation_id) gid: vec3<u32>) {
    let line = gid.x / 3u;
    let ch = gid.x % 3u;
    if (line >= dp.n_lines) {
        return;
    }
    let n = dp.n_in;
    for (var i = 0u; i < n; i = i + 1u) {
        ddst[in_index(line, i, ch)] = dsrc[in_index(line, i, ch)] * 6.0;   // (1-z)(1-1/z) = 6
    }
    if (n == 1u) {
        return;
    }
    let z = -0.26794919243112270;   // sqrt(3) - 2
    // _init_causal_mirror. Beyond ~48 terms z^i is below f32 resolution, so a
    // long line truncates the sum and drops the z^(n-1) wrap term (0 in f32).
    var z_n_1 = 0.0;
    var horizon = n - 1u;
    if (n - 1u <= 48u) {
        z_n_1 = 1.0;
        for (var i = 0u; i < n - 1u; i = i + 1u) {
            z_n_1 = z_n_1 * z;
        }
    } else {
        horizon = 48u;
    }
    var c0 = ddst[in_index(line, 0u, ch)] + z_n_1 * ddst[in_index(line, n - 1u, ch)];
    var z_i = z;
    for (var i = 1u; i < horizon; i = i + 1u) {
        c0 = c0 + z_i * (ddst[in_index(line, i, ch)]
                         + z_n_1 * ddst[in_index(line, n - 1u - i, ch)]);
        z_i = z_i * z;
    }
    ddst[in_index(line, 0u, ch)] = c0 / (1.0 - z_n_1 * z_n_1);
    for (var j = 1u; j < n; j = j + 1u) {
        let at = in_index(line, j, ch);
        ddst[at] = ddst[at] + z * ddst[in_index(line, j - 1u, ch)];
    }
    // _init_anticausal_mirror
    let last = in_index(line, n - 1u, ch);
    ddst[last] = (z * ddst[in_index(line, n - 2u, ch)] + ddst[last]) * z / (z * z - 1.0);
    var j = i32(n) - 2;
    loop {
        if (j < 0) {
            break;
        }
        let at = in_index(line, u32(j), ch);
        ddst[at] = z * (ddst[in_index(line, u32(j) + 1u, ch)] - ddst[at]);
        j = j - 1;
    }
}

// Cubic evaluation at the grid_mode sample position of each output sample:
// x = (o + 0.5) * n_in / n_out - 0.5, carried as the exact ratio
// ((2o+1)*n_in - n_out) / (2*n_out) so floor and fraction are exact.
@compute @workgroup_size(64, 1, 1)
fn zoom_cubic(@builtin(global_invocation_id) gid: vec3<u32>) {
    let o = gid.x;
    let line = gid.y / 3u;
    let ch = gid.y % 3u;
    if (o >= dp.n_out || line >= dp.n_lines) {
        return;
    }
    let num = i32(2u * o + 1u) * i32(dp.n_in) - i32(dp.n_out);
    let den = 2 * i32(dp.n_out);
    var i = num / den;                       // truncates toward zero...
    if (num < 0 && i * den != num) {
        i = i - 1;                           // ...floor for negatives
    }
    let t = f32(num - i * den) / f32(den);
    let t2 = t * t;
    let t3 = t2 * t;
    let w0 = (1.0 - t) * (1.0 - t) * (1.0 - t) / 6.0;
    let w1 = (4.0 - 6.0 * t2 + 3.0 * t3) / 6.0;
    let w2 = (1.0 + 3.0 * t + 3.0 * t2 - 3.0 * t3) / 6.0;
    let w3 = t3 / 6.0;
    let n = i32(dp.n_in);
    let v = w0 * dsrc[in_index(line, u32(mirror_idx(i - 1, n)), ch)]
          + w1 * dsrc[in_index(line, u32(mirror_idx(i, n)), ch)]
          + w2 * dsrc[in_index(line, u32(mirror_idx(i + 1, n)), ch)]
          + w3 * dsrc[in_index(line, u32(mirror_idx(i + 2, n)), ch)];
    var lo = dp.clip_lo;
    var hi = dp.clip_hi;
    if (dp.clip_from_buf == 1u) {
        lo = dweights[0];
        hi = dweights[1];
    }
    ddst[line * dp.out_line + o * dp.out_pos + ch] = clamp(v, lo, hi);
}

// ------------------------------------------------------ min / max reduction ---
// The fused chain (scan_resident) never has the frame on the host, so the
// input range skimage clips the desqueeze to is reduced here: minmax_partial
// (one workgroup per slice of the flat buffer; n_lines = element count,
// n_in = partial count) then minmax_final (ONE workgroup) -> ddst[0..2] =
// (min, max, 1.0 if any element is non-finite). Finiteness is read from the
// exponent bits, which no compiler can fold away the way it may `x != x`.
var<workgroup> wg_lo: array<f32, 256>;
var<workgroup> wg_hi: array<f32, 256>;
var<workgroup> wg_bad: array<f32, 256>;

fn is_finite_bits(x: f32) -> bool {
    return (bitcast<u32>(x) & 0x7f800000u) != 0x7f800000u;
}

fn wg_reduce(lid: u32) {
    var s = 128u;
    loop {
        if (s == 0u) {
            break;
        }
        if (lid < s) {
            wg_lo[lid] = min(wg_lo[lid], wg_lo[lid + s]);
            wg_hi[lid] = max(wg_hi[lid], wg_hi[lid + s]);
            wg_bad[lid] = max(wg_bad[lid], wg_bad[lid + s]);
        }
        workgroupBarrier();
        s = s / 2u;
    }
}

@compute @workgroup_size(256, 1, 1)
fn minmax_partial(@builtin(local_invocation_id) lid3: vec3<u32>,
                  @builtin(workgroup_id) wid: vec3<u32>) {
    let lid = lid3.x;
    let stride = dp.n_in * 256u;
    var lo = 3.4028235e38;
    var hi = -3.4028235e38;
    var bad = 0.0;
    var i = wid.x * 256u + lid;
    loop {
        if (i >= dp.n_lines) {
            break;
        }
        let v = dsrc[i];
        if (is_finite_bits(v)) {
            lo = min(lo, v);
            hi = max(hi, v);
        } else {
            bad = 1.0;
        }
        i = i + stride;
    }
    wg_lo[lid] = lo;
    wg_hi[lid] = hi;
    wg_bad[lid] = bad;
    workgroupBarrier();
    wg_reduce(lid);
    if (lid == 0u) {
        ddst[wid.x * 3u] = wg_lo[0];
        ddst[wid.x * 3u + 1u] = wg_hi[0];
        ddst[wid.x * 3u + 2u] = wg_bad[0];
    }
}

@compute @workgroup_size(256, 1, 1)
fn minmax_final(@builtin(local_invocation_id) lid3: vec3<u32>) {
    let lid = lid3.x;
    var lo = 3.4028235e38;
    var hi = -3.4028235e38;
    var bad = 0.0;
    var i = lid;
    loop {
        if (i >= dp.n_in) {
            break;
        }
        lo = min(lo, dsrc[i * 3u]);
        hi = max(hi, dsrc[i * 3u + 1u]);
        bad = max(bad, dsrc[i * 3u + 2u]);
        i = i + 256u;
    }
    wg_lo[lid] = lo;
    wg_hi[lid] = hi;
    wg_bad[lid] = bad;
    workgroupBarrier();
    wg_reduce(lid);
    if (lid == 0u) {
        ddst[0] = wg_lo[0];
        ddst[1] = wg_hi[0];
        ddst[2] = wg_bad[0];
    }
}

// ------------------------------------------------------------------- skew ---
struct SParams {
    rows: u32,          // full image
    cols: u32,
    out_r0: u32,        // first output row of this band
    band_rows: u32,     // output rows in this band
    in_r0: u32,         // first input row uploaded for this band
    in_rows: u32,       // input rows uploaded
    _p0: u32,
    _p1: u32,
    // input column = M00*c + M01*r + M02; input row = M10*c + M11*r + M12.
    // Linear coefficients as (hi, lo) f32 pairs; offsets as (integer part,
    // fraction hi, fraction lo) -- all split host-side from skimage's f64.
    x_ab: vec4<f32>,    // (M00 hi, M00 lo, M01 hi, M01 lo)
    x_t: vec4<f32>,     // (M02 int, M02 frac hi, M02 frac lo, unused)
    y_ab: vec4<f32>,    // (M10 hi, M10 lo, M11 hi, M11 lo)
    y_t: vec4<f32>,     // (M12 int, M12 frac hi, M12 frac lo, unused)
};

@group(0) @binding(4) var<uniform>             sp: SParams;
@group(0) @binding(5) var<storage, read>       ssrc: array<f32>;
@group(0) @binding(6) var<storage, read_write> sdst: array<f32>;

// floor() and fraction in [0, 1) of  a*c + b*r + t, where c, r are exact
// integers, a/b are (hi, lo) and t is (integer, frac hi, frac lo). The
// integer parts are accumulated separately from the fractions, so no step
// needs an error-free SUM (two_sum's error terms did not survive compilation
// on the test GPU); only the product error is recovered, via the same
// fma(a, b, -a*b) precise_pow in color.wgsl relies on. Every integer here is
// < 2^24, so f32 holds it exactly; p - floor(p) is exact.
fn coord_split(a: vec2<f32>, b: vec2<f32>, t: vec3<f32>, c: f32, r: f32) -> vec2<f32> {
    let p0 = a.x * c;
    let e0 = fma(a.x, c, -p0);
    let f0 = floor(p0);
    let p1 = b.x * r;
    let e1 = fma(b.x, r, -p1);
    let f1 = floor(p1);
    var i = f0 + f1 + t.x;
    var f = ((p0 - f0) + e0 + a.y * c) + ((p1 - f1) + e1 + b.y * r) + t.y + t.z;
    let k = floor(f);
    i = i + k;
    f = f - k;
    return vec2<f32>(i, f);
}

// crop_resize.SKEW_FILL: white outside the picture, like the paper
const SKEW_FILL: f32 = 1.0;

fn spx(r: i32, c: i32, ch: u32) -> f32 {
    if (r < 0 || r >= i32(sp.rows) || c < 0 || c >= i32(sp.cols)) {
        return SKEW_FILL;                                       // constant mode
    }
    let rr = r - i32(sp.in_r0);                                 // band-relative
    return ssrc[(u32(rr) * sp.cols + u32(c)) * 3u + ch];
}

@compute @workgroup_size(8, 8, 1)
fn skew_bilinear(@builtin(global_invocation_id) gid: vec3<u32>) {
    let c = gid.x;
    let rl = gid.y;
    if (c >= sp.cols || rl >= sp.band_rows) {
        return;
    }
    let r = sp.out_r0 + rl;
    let xs = coord_split(sp.x_ab.xy, sp.x_ab.zw, sp.x_t.xyz, f32(c), f32(r));   // input column
    let ys = coord_split(sp.y_ab.xy, sp.y_ab.zw, sp.y_t.xyz, f32(c), f32(r));   // input row
    let c0 = i32(xs.x);
    let r0 = i32(ys.x);
    let dc = xs.y;
    let dr = ys.y;
    for (var ch = 0u; ch < 3u; ch = ch + 1u) {
        let top = (1.0 - dc) * spx(r0, c0, ch) + dc * spx(r0, c0 + 1, ch);
        let bot = (1.0 - dc) * spx(r0 + 1, c0, ch) + dc * spx(r0 + 1, c0 + 1, ch);
        sdst[(rl * sp.cols + c) * 3u + ch] = (1.0 - dr) * top + dr * bot;
    }
}
