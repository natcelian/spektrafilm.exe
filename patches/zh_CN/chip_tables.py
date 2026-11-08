"""Per-item chip art, GENERATED from the final design page.

Do not edit by hand: run `python tools/gen_chip_tables.py`. A test
re-parses the page and compares, so a drift fails loudly.

These are the stocks' graphic IDENTITY -- Ektar red/blue, Gold
yellow, the Vision stocks dark with a yellow flash. The point is
that they differ; a shelf where every Kodak looks alike is not the
design. They are not spectral data and nothing reads them for a
render.
"""

from __future__ import annotations

#: one gradient per film stock (the page's 150deg sweeps)
STOCK_CHIP: dict[str, str] = {
    'kodak_ektar_100': 'linear-gradient(150deg,#e8433a,#b02020 55%,#27418f)',
    'kodak_portra_160': 'linear-gradient(150deg,#f5ead8,#e6c39a 55%,#b98a68)',
    'kodak_portra_400': 'linear-gradient(150deg,#f2dec2,#dda677 52%,#8e5d46)',
    'kodak_portra_800': 'linear-gradient(150deg,#e8cba4,#c58a55 52%,#6e4433)',
    'kodak_portra_800_push1': 'linear-gradient(150deg,#e0bd92,#b57946 52%,#5e3a2b)',
    'kodak_portra_800_push2': 'linear-gradient(150deg,#d8b083,#a56a3b 52%,#503225)',
    'kodak_gold_200': 'linear-gradient(150deg,#f9d43e,#ee9f27 55%,#c53a20)',
    'kodak_ultramax_400': 'linear-gradient(150deg,#2f57b5,#22408c 55%,#d3352b)',
    'kodak_ultramax_800': 'linear-gradient(150deg,#24418c,#1a2f66 55%,#b02a22)',
    'kodak_vision2_50d': 'linear-gradient(150deg,#242424,#3a3a3a 58%,#f0c53c)',
    'kodak_vision2_100t': 'linear-gradient(150deg,#20242e,#343a4a 58%,#f0c53c)',
    'kodak_vision2_500t': 'linear-gradient(150deg,#1a1e2a,#2b3244 58%,#f0c53c)',
    'kodak_vision3_50d': 'linear-gradient(150deg,#262626,#404040 58%,#ffd23f)',
    'kodak_vision3_250d': 'linear-gradient(150deg,#242424,#3d3d3d 58%,#f5b93a)',
    'kodak_verita_200d': 'linear-gradient(150deg,#232a28,#37423e 58%,#4fc3a8)',
    'kodak_vision3_200t': 'linear-gradient(150deg,#21252f,#363d4e 58%,#ffd23f)',
    'kodak_vision3_500t': 'linear-gradient(150deg,#1b1f29,#2d3444 58%,#ffd23f)',
    'fujifilm_pro_400h': 'linear-gradient(150deg,#e9f4ec,#9ccBa8 55%,#2c7a4e)',
    'fujifilm_c200': 'linear-gradient(150deg,#37a04c,#237a38 58%,#d23a2e)',
    'fujifilm_xtra_400': 'linear-gradient(150deg,#2b8a41,#1d6330 58%,#254a9e)',
    'fujifilm_eterna_250d': 'linear-gradient(150deg,#2e3634,#48524e 58%,#8fb5a4)',
    'kodak_ektachrome_100': 'linear-gradient(150deg,#2f6db8,#204b85 58%,#eede4c)',
    'kodak_kodachrome_64': 'linear-gradient(150deg,#d8322a,#a32017 55%,#f2c400)',
    'fujifilm_velvia_100': 'linear-gradient(150deg,#7a2d8c,#54206a 55%,#2c8a4e)',
    'fujifilm_velvia_100f': 'linear-gradient(150deg,#9a4aa8,#6d2f83 55%,#3fa060)',
    'fujifilm_provia_100f': 'linear-gradient(150deg,#2f8ab8,#1f6088 55%,#2c4a9e)',
    'kodak_doublex': 'linear-gradient(150deg,#3a3a3a,#565656 58%,#c9c9c9)',
    'kodak_trix': 'linear-gradient(150deg,#f2c410,#d8a90c 55%,#1c1c1c)',
    '__custom__': 'linear-gradient(150deg,var(--bg-3),var(--bg-2))',
}

#: one per print paper (165deg, a single 62% stop)
PAPER_CHIP: dict[str, str] = {
    'kodak_supra_endura': 'linear-gradient(165deg,#f7f4eb 62%,#e33a2c)',
    'kodak_endura_premier': 'linear-gradient(165deg,#f9f6ee 62%,#c9a23a)',
    'kodak_ultra_endura': 'linear-gradient(165deg,#f5f2e8 62%,#8c2a20)',
    'kodak_portra_endura': 'linear-gradient(165deg,#f6efe2 62%,#c08a5c)',
    'kodak_ektacolor_edge': 'linear-gradient(165deg,#f4f1e9 62%,#2a58a8)',
    'fujifilm_crystal_archive_typeii': 'linear-gradient(165deg,#f3f7f2 62%,#2c8a4e)',
    'kodak_2383': 'linear-gradient(165deg,#22262e 62%,#f0c53c)',
    'kodak_2393': 'linear-gradient(165deg,#262a32 62%,#e09a35)',
    'kodak_2302': 'linear-gradient(165deg,#e8e8e6 62%,#3a3a3a)',
}

#: the glass colour of each camera taking filter, oklch resolved to sRGB
FILTER_GLASS: dict[str, str] = {
    'hoya_x0': '#BBC855',
    'hoya_x1': '#62AE50',
    'hoya_y2': '#EDCC48',
    'hoya_ya3': '#E99B2A',
    'hoya_r1': '#D73626',
}

#: `METHOD_SUB` -- the caption under each upsampling method's name
METHOD_CAPTION: dict[str, str] = {
    'hanatos2025': '辐射照度重构 · 全色度轨迹 (推荐)',
    'jakob2019': '有界反射率 · Sigmoid S形曲线',
    'otsu2018': '有界反射率 · 聚类基底算法',
    'mallett2019': 'sRGB 基底 · 共享 3D LUT',
    'arctic2026beta04': '记忆色彩先验 · Beta 测试版',
    'mallett2019_legacy': '逐像素传统算法 · 仅限 sRGB',
}

#: `methodGlyph` -- the mini curve, on the page's 22x22 viewBox
METHOD_PATH: dict[str, str] = {
    'hanatos2025': 'M2 18 C6 16 7 5 11 5 C15 5 16 16 20 18',
    'jakob2019': 'M2 18 C9 18 13 4 20 4',
    'otsu2018': 'M2 17 L7 17 L7 10 L13 10 L13 6 L20 6',
    'mallett2019': 'M2 18 C5 8 8 8 11 15 M6 18 C10 5 13 5 16 14 M11 18 C15 8 18 8 20 12',
    'arctic2026beta04': 'M2 17 C5 12 7 12 9 15 C12 6 16 6 20 10',
    'mallett2019_legacy': 'M2 16 C8 14 14 8 20 6',
}

#: the curve colour per method, oklch resolved to sRGB
METHOD_STROKE: dict[str, str] = {
    'hanatos2025': 'var(--accent)',
    'jakob2019': '#7CCD8E',
    'otsu2018': '#D8BD51',
    'mallett2019': '#DE77AB',
    'arctic2026beta04': '#E89960',
    'mallett2019_legacy': 'var(--ink-3)',
}

#: `stroke-dasharray`, empty for the five solid curves
METHOD_DASH: dict[str, str] = {
    'hanatos2025': '',
    'jakob2019': '',
    'otsu2018': '',
    'mallett2019': '',
    'arctic2026beta04': '',
    'mallett2019_legacy': '3 2.4',
}


def chip_gradient(slug: str) -> str:
    """`chipBG(v)` -- the stock's or paper's own gradient, or empty.

    The page draws only the bundled stocks; an imported one falls through
    to `chip_imported`, drawn in the same idiom (QA 2026-09-29).
    """
    from spektrafilm_gui.chip_imported import imported_chip_gradient

    return (STOCK_CHIP.get(str(slug)) or PAPER_CHIP.get(str(slug))
            or imported_chip_gradient(slug))
