# 简体中文语言支持补丁 (Simplified Chinese Localization)

本目录包含适用于 **The Dyeworks (Spektrafilm)** 2.2.x 的全界面简体中文本地化补丁。

## 翻译特性与专业暗房标准
- **色彩科学与暗房化学标准**：对感光乳剂层（Emulsion）、DIR 抑制偶联剂（DIR Couplers）、留银冲洗（Bleach Bypass）、相纸预曝光（Preflash）、放大机滤色灯头（Enlarger Head）、相纸纸基与片基色调（Base）、光谱上采样重构算法（Spectral Upsampling）等进行了严密的地毯式术语校对。
- **全流程覆盖**：
  - 顶部导航工作台（原片 / 底片 / 印相 / 对比 / 扫描渲染 / 导出）
  - 侧边栏（胶卷 / 修复 / 设置）
  - 深度降噪与修补（Ansel U-Net / Multi-scale / RawNIND 神经模型）
  - 35+ 个胶片物理与暗房调色参数滑块与单位
  - 悬浮工具条与全局提示

## 使用方法 (How to apply)
构建或打包时，将 `patches/zh_CN/` 目录下的所有 `.py` 文件覆盖复制至 `spektrafilm_gui` 源码包目录下，或者在 `build.ps1` 编译前将本目录文件应用到 Python 环境中即可生成原生简体中文版的 Windows 独立可执行程序。
