"""What a stock or a paper IS, in one line, from its own profile.

The design page hardcodes `sub: 'C-41, colour negative'` on the film-stock
row and `'lustre · neutral base'` on the paper row, because its mockup only
ever had Portra 400 and Supra Endura selected. The app has 30 stocks and 9
papers, and most of them are neither: the Vision stocks are ECN-2 cine
negative, Velvia and Provia and Ektachrome are E-6 reversal, Double-X and
Tri-X are black and white, and three of the "papers" are print FILM.

So the caption is DERIVED, from the four fields every profile already
carries -- `channel_model`, `support`, `stage`, `type`, `use`. Nothing here
is keyed off a stock's name, because a name is not a fact and the next
stock to arrive would not be in the table.

Two exceptions ARE keyed by slug, and both are real chemistry rather than
gaps in the data: Kodachrome is K-14, not E-6 (its dyes are added during
development, which is why it needed its own process and its own lab), and
Kodachrome II is the same. Everything else falls out of the metadata.
"""

from __future__ import annotations

#: Processes a profile's metadata cannot distinguish, because the fields
#: describe the IMAGE (positive, colour, still) and not the chemistry.
#: Keyed by slug only where the chemistry is genuinely its own.
_PROCESS_BY_SLUG = {
    'kodak_kodachrome_64': 'K-14',
    'kodak_kodachrome_25': 'K-14',
    'kodak_kodachrome_ii': 'K-14',
}


def _info(slug: str):
    try:
        from spektrafilm.profiles.io import load_profile

        return load_profile(str(slug)).info
    except Exception:
        pass
    # A CUSTOM stock is a recipe over a base stock, so its process is the
    # base's -- a recipe changes the curves, not the chemistry (Phase 10:
    # editing log_sensitivity is out of scope, and a recipe cannot turn a
    # negative into a reversal).
    try:
        from spektrafilm.profiles.custom_stocks import (is_custom_slug,
                                                        load_custom_stock)
        from spektrafilm.profiles.io import load_profile

        if is_custom_slug(str(slug)):
            base = load_custom_stock(str(slug)).base_stock
            return load_profile(str(base)).info
    except Exception:
        pass
    return None


#: The taking filter's own line, verbatim from the FILM tab's chip — one
#: string for all of them, because what a filter IS does not vary: the
#: measured transmittance is what the engine folds in. Named here so the
#: row and the shelf card cannot drift apart.
FILTER_CAPTION = '拍摄滤色镜 · 光谱透射率'


def process_caption(slug: str) -> str:
    """'ECN-2 · 彩色负片', 'E-6 · 彩色反转片', 'RA-4 · 彩色相纸' …"""
    info = _info(slug)
    if info is None:
        return ''
    channel = str(getattr(info, 'channel_model', '') or '')
    support = str(getattr(info, 'support', '') or '')
    stage = str(getattr(info, 'stage', '') or '')
    kind = str(getattr(info, 'type', '') or '')
    use = str(getattr(info, 'use', '') or '')

    if channel == 'bw':
        if stage == 'printing':
            return '黑白 · 正片印相胶片'
        return '黑白 · 反转片' if kind == 'positive' else '黑白 · 负片'

    if support == 'paper':
        return 'RA-4 · 彩色相纸'
    if stage == 'printing':
        return 'ECP-2 · 电影印相胶片'

    if kind == 'positive':
        process = _PROCESS_BY_SLUG.get(str(slug), 'E-6')
        return f'{process} · 彩色反转片'
    return ('ECN-2 · 电影彩色负片' if use == 'cine'
            else 'C-41 · 彩色负片')
