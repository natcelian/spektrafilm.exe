"""The Reco view: Denoise, Defects, Lens correction and Highlights.

Rows are drawn over the editors of `widgets.recover` (and, for the lens
toggle, `widgets.load_raw`), so the state bridge, presets, rolls and versions
see nothing new. The groups follow the 20.3 page's Reco tab.
"""
from __future__ import annotations

from qtpy import QtCore, QtWidgets

from spektrafilm.utils.raw_dust import is_switched_off, spot_summary
from spektrafilm_gui.dyeworks_buttons import SegmentedEnum, WideButton
from spektrafilm_gui.dyeworks_editors import SIDE_COLUMNS
from spektrafilm_gui.dyeworks_labels import Hint, LabelledRow, ShrinkingLabel, _mono_font, _ui_font
from spektrafilm_gui.dyeworks_widgets import DyeworksSection
from spektrafilm_gui.edit_panel import EditRow, _reset_editors, _wire_reset
from spektrafilm_gui.panel_scroll import PanelScrollArea
from spektrafilm_gui.theme_tokens import TYPE_CAPTION

Qt = QtCore.Qt

_BACKENDS = (('clean', '纯净 (Ansel)'), ('deep', '深度 (Multi-scale)'), ('sharp', '锐利 (RawNIND)'))
_FINDS = (('roll', '全卷分析'), ('frame', '单张识别'), ('manual', '手动点击'))
_TOOLS = (('add', '增加污点'), ('ignore', '忽略区域'), ('only', '仅在此处修补'))
_TOOL_NOTE = ('增加污点：处于手动识别模式时单击污点即可添加。忽略区域：拖动绘制圆形选区，圈内污点将保持原样不处理。仅在此处：拖动绘制圆形选区，仅对圈内污点进行修补。')
_METHODS = (('fast', '快速'), ('precise', '精准'), ('heal', '智能融合'))
_HIGHLIGHTS = (('clip', '硬剪裁'), ('blend', '平滑混合'), ('rebuild', '高光重建'))

_DENOISE_NOTE = (
    'Clean (Ansel U-Net) 适合绝大多数照片。Deep (多尺度 Ansel) 针对重度提亮暗部和明显低频彩噪。Sharp (RawNIND) 在反马赛克后运行，保留最多的边缘与真实微观细节。三种模型均原生支持富士 X-Trans。')
_DENOISE_TIMING = ('每张照片后台仅运行推理一次并缓存结果。后续调整降噪强度无需重新计算。2400万像素耗时预估：Clean约15秒，Sharp约25秒，Deep约45秒。')
_FIND_NOTE = ('全卷分析：从整卷胶卷的多张照片中深度学习共性噪点，即使被复杂纹理遮挡的 CMOS 脏点也能精准捕捉。单张：仅识别当前底片。手动：在画面污点处单击即可直接添加。')
_METHOD_NOTES = {
    'fast': '基于衍射物理扩散曲线进行反卷积阴影剔除，绝不涂抹凭空绘制。',
    'precise': '利用高信噪比清晰区域的自适应物理剖面轮廓，适合高反差硬调场景。',
    'heal': '智能取样周围无瑕疵纹理进行自然融合克隆，适合不透光的不规则异物毛屑。',
}
_BARREL_NOTE = '负值矫正桶形畸变（画面向外膨胀）；正值矫正枕形畸变（画面向内凹陷）。'
_VIGNETTE_NOTE = ('负值提亮四角暗角失光；正值压暗四角生成暗角：双向物理校准与风格微调。')
_LENS_NOTE = ('根据照片 EXIF 中的相机机身与镜头型号自动匹配光学矫正数据库，同步校正畸变、边缘暗角和色差紫边，并可与上方微调参数叠加生效。')


class Fact(QtWidgets.QWidget):
    """A value in ink and its source in the mono face, on one line."""

    def __init__(self, text: str = '', source: str = '') -> None:
        super().__init__()
        self.setVisible(bool(text))
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._text = ShrinkingLabel(text)
        self._text.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        self._text.setFont(_ui_font(self._text, TYPE_CAPTION))
        self._source = ShrinkingLabel(source)
        self._source.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        self._source.setProperty('role', 'caption')
        self._source.setFont(_mono_font(TYPE_CAPTION))
        self._source.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._text, 1)
        row.addWidget(self._source, 0)

    def set_fact(self, text: str, source: str = '') -> None:
        self._text.setText(text)
        self._source.setText(source)
        self.setVisible(bool(text))


class DefectRow(QtWidgets.QFrame):
    """One spot: its cell label and size, how deep it is, and a x / + to switch it off or on."""

    clicked = QtCore.Signal(str)

    def __init__(self, label: str, text: str, meta: str, off: bool) -> None:
        super().__init__()
        self._label = label
        self.setCursor(Qt.PointingHandCursor)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(8)
        name = ShrinkingLabel(text)
        name.setFont(_ui_font(name, TYPE_CAPTION))
        name.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        depth = QtWidgets.QLabel(meta)
        depth.setProperty('role', 'caption')
        depth.setFont(_mono_font(TYPE_CAPTION))
        mark = QtWidgets.QLabel('+' if off else '×')
        mark.setProperty('role', 'caption')
        mark.setFixedWidth(14)
        mark.setAlignment(Qt.AlignCenter)
        row.addWidget(name, 1)
        row.addWidget(depth, 0)
        row.addWidget(mark, 0)
        if off:
            font = name.font()
            font.setStrikeOut(True)
            name.setFont(font)
            self.setProperty('off', True)
            for widget in (name, depth, mark):
                widget.setEnabled(False)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._label)
        super().mouseReleaseEvent(event)


class DefectList(PanelScrollArea):
    """Every spot of the frame, scrolling inside a frame of its own; clicking one toggles it."""

    toggled = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setMaximumHeight(176)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body = QtWidgets.QWidget()
        self._column = QtWidgets.QVBoxLayout(self._body)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(1)
        self._column.addStretch(1)
        self.setWidget(self._body)
        self.setVisible(False)

    def set_spots(self, rows: list[tuple[str, str, str, bool]]) -> None:
        while self._column.count() > 1:
            item = self._column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for position, (label, text, meta, off) in enumerate(rows):
            row = DefectRow(label, text, meta, off)
            row.clicked.connect(self.toggled)
            self._column.insertWidget(position, row)
        self.setVisible(bool(rows))


class RecoverView(QtWidgets.QWidget):
    """The four groups, one column."""

    def __init__(self, widgets) -> None:
        super().__init__()
        self._recover = widgets.recover
        self._load_raw = widgets.load_raw
        self._spots_total = 0
        self._spots_on = 0
        column = QtWidgets.QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.denoise = self._build_denoise()
        self.defects = self._build_defects()
        self.lens = self._build_lens()
        self.highlights = self._build_highlights()
        for section in (self.denoise, self.defects, self.lens, self.highlights):
            column.addWidget(section)
        column.addStretch(1)

        for editor in self._metered_editors():
            signal = getattr(editor, 'toggled', None) or getattr(editor, 'currentTextChanged', None) \
                or getattr(editor, 'valueChanged', None)
            if signal is not None:
                signal.connect(lambda *_: self._refresh_metas())
        self._refresh_metas()

    # ------------------------------------------------------------ helpers
    def _row(self, field: str) -> EditRow:
        return EditRow(f'recover.{field}', getattr(self._recover, field), None,
                       columns=SIDE_COLUMNS)

    def _seg_row(self, label: str, field: str, choices, *, reset: bool = True) -> LabelledRow:
        editor = getattr(self._recover, field)
        seg = SegmentedEnum(choices)
        seg.changed.connect(lambda slug: setattr(editor, 'value', str(slug)))
        editor.currentTextChanged.connect(seg.set_value)
        seg.set_value(editor.value)
        row = LabelledRow(label, seg, reset=_reset_editors([editor]) if reset else None)
        if reset:
            _wire_reset(row, [editor])
        row.seg = seg
        return row

    def _metered_editors(self):
        r = self._recover
        return (r.denoise_active, r.denoise_amount, r.denoise_backend, r.defects_active,
                r.defects_find, r.lens_barrel, r.lens_vignette, r.highlights_mode,
                self._load_raw.lens_correction)

    # ------------------------------------------------------------- groups
    def _build_denoise(self) -> DyeworksSection:
        section = DyeworksSection('RAW 深度降噪', meta='关闭', expanded=True, compact=True)
        section.add(self._row('denoise_active'))
        self.camera_fact = Fact()
        section.add(self.camera_fact)
        section.add(self._row('denoise_amount'))
        self.backend_row = self._seg_row('降噪模型', 'denoise_backend', _BACKENDS)
        section.add(self.backend_row)
        section.add(Hint(_DENOISE_NOTE))
        badge = QtWidgets.QLabel('物理保真 · 绝不凭空捏造细节')
        badge.setProperty('role', 'caption')
        badge.setFont(_mono_font(TYPE_CAPTION - 1))
        badge.setStyleSheet('border: 1px solid palette(mid); border-radius: 4px; padding: 1px 6px;')
        badge.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        section.add(badge)
        self.denoise_timing = Hint(_DENOISE_TIMING)
        section.add(self.denoise_timing)
        return section

    def _build_defects(self) -> DyeworksSection:
        section = DyeworksSection('坏点与污点修补', meta='关闭', expanded=True, compact=True)
        section.add(self._row('defects_active'))
        section.add(self._row('defects_show'))
        section.add(self._row('defects_strength'))
        self.find_row = self._seg_row('识别模式', 'defects_find', _FINDS, reset=False)
        section.add(self.find_row)
        section.add(Hint(_FIND_NOTE))

        self.defects_below = QtWidgets.QWidget()
        below = QtWidgets.QVBoxLayout(self.defects_below)
        below.setContentsMargins(0, 0, 0, 0)
        below.setSpacing(6)
        self.method_group = DyeworksSection('修复算法', variant='group', expanded=False, rule='none')
        self.method_row = self._seg_row('算法选择', 'defects_method', _METHODS)
        self.method_group.add(self.method_row)
        self.method_note = Hint(_METHOD_NOTES['fast'])
        self.method_group.add(self.method_note)
        below.addWidget(self.method_group)
        self.tool_row = self._seg_row('点击工具', 'defects_tool', _TOOLS, reset=False)
        below.addWidget(self.tool_row)
        below.addWidget(Hint(_TOOL_NOTE))
        self.zone_note = Hint('')
        self.clear_zones = WideButton('清除圈定选区')
        self.clear_zones.clicked.connect(lambda: setattr(self._recover.defects_zones, 'value', ()))
        below.addWidget(self.zone_note)
        below.addWidget(self.clear_zones)
        self._recover.defects_zones.valueChanged.connect(self._sync_zones)
        self._sync_zones()
        self.defect_status = Hint('')
        self.defect_status.setVisible(False)
        below.addWidget(self.defect_status)
        self.defect_list = DefectList()
        below.addWidget(self.defect_list)
        below.addWidget(self._row('defects_correct'))
        section.add(self.defects_below)

        active = self._recover.defects_active
        find = self._recover.defects_find
        method = self._recover.defects_method
        active.toggled.connect(self._on_defects_active)
        find.currentTextChanged.connect(lambda *_: self._sync_defects())
        method.currentTextChanged.connect(
            lambda slug: self.method_note.setText(_METHOD_NOTES.get(str(slug), '')))
        self._sync_defects()
        return section

    def _build_lens(self) -> DyeworksSection:
        section = DyeworksSection('镜头光学矫正', meta='', expanded=False, compact=True)
        section.add(self._row('lens_barrel'))
        section.add(Hint(_BARREL_NOTE))
        section.add(self._row('lens_vignette'))
        section.add(Hint(_VIGNETTE_NOTE))
        section.add(EditRow('load_raw.lens_correction', self._load_raw.lens_correction, None,
                            columns=SIDE_COLUMNS))
        self.lens_fact = Fact()
        section.add(self.lens_fact)
        section.add(Hint(_LENS_NOTE))
        return section

    def _build_highlights(self) -> DyeworksSection:
        section = DyeworksSection('过曝高光重建', meta='硬剪裁', expanded=False, compact=True)
        section.add(self._seg_row('重建模式', 'highlights_mode', _HIGHLIGHTS))
        return section

    # -------------------------------------------------------------- state
    def _on_defects_active(self, on: bool) -> None:
        if not on:
            self._recover.defects_find.value = 'none'
        self._sync_defects()

    def _sync_zones(self, *_args) -> None:
        zones = self._recover.defects_zones.value
        ignored = sum(1 for zone in zones if zone[0] == 'ignore')
        self.zone_note.setText(f'{ignored} ignore, {len(zones) - ignored} only here')
        self.zone_note.setVisible(bool(zones))
        self.clear_zones.setVisible(bool(zones))

    def _sync_defects(self) -> None:
        active = bool(self._recover.defects_active.isChecked())
        found = str(self._recover.defects_find.value) != 'none'
        for button in self.find_row.seg._buttons:
            button.setEnabled(active)
        self.defects_below.setVisible(active and found)

    def _refresh_metas(self) -> None:
        r = self._recover
        if r.denoise_active.isChecked():
            backend = str(r.denoise_backend.value).capitalize()
            self.denoise.set_meta(f'{backend} · {r.denoise_amount.value:.0f} %')
        else:
            self.denoise.set_meta('off')
        if not r.defects_active.isChecked():
            self.defects.set_meta('off')
        else:
            if str(r.defects_find.value) == 'none':
                self.defects.set_meta('ready')
            else:
                self.defects.set_meta(f'{self._spots_on} of {self._spots_total}' if self._spots_total
                                      else str(r.defects_find.value))
        parts = []
        for label, editor in (('barrel', r.lens_barrel), ('vignette', r.lens_vignette)):
            value = editor.value
            if value:
                parts.append(f'{label} {value:+.0f}')
        parts.append('auto' if self._load_raw.lens_correction.value else 'auto off')
        self.lens.set_meta(' · '.join(parts))
        self.highlights.set_meta(str(r.highlights_mode.value))

    # ------------------------------------------------------ facts (EXIF)
    def set_camera_fact(self, text: str, source: str = 'from EXIF') -> None:
        self.camera_fact.set_fact(text, source)

    def set_lens_fact(self, text: str, source: str = 'from EXIF') -> None:
        self.lens_fact.set_fact(text, source)

    def set_defect_spots(self, record: dict | None, excluded) -> None:
        """The frame's spots in the list, the ones switched off struck through; None clears it."""
        rows = []
        if record is not None:
            width = record['crop'][2]
            for spot, label in zip(record['spots'], record['labels']):
                text, meta = spot_summary(spot, label, width)
                rows.append((label, text, meta, is_switched_off(label, excluded)))
        self.defect_list.set_spots(rows)
        self._spots_total, self._spots_on = len(rows), sum(1 for row in rows if not row[3])
        self.set_defect_status('')
        self._refresh_metas()

    def set_defect_status(self, text: str) -> None:
        self.defect_status.setText(text)
        self.defect_status.setVisible(bool(text))

    def set_denoise_status(self, text: str) -> None:
        """What the denoise is doing; empty puts the standing note back."""
        self.denoise_timing.setText(text or _DENOISE_TIMING)
