# PDF 转 Markdown 质量报告

## 概览

- 源文件：`sample_pdf_to_markdown_note.pdf`
- 解析器：Poppler pdftotext -bbox-layout + Python heuristics
- 页数：3
- 文本/结构块：10
- 表格：2
- 疑似图片或扫描页：3

## 自动检查结果

- [WARNING] page 3：页面文字层过少，疑似图片/扫描页；OCR 状态：已尝试；需要人工复核 OCR 候选文本。

## 表格检查

### 表36-1 租赁负债到期分析（page 1）

- source locator：`page=1;bbox=50.0,125.3,466.7,287.8`
- 行数：4，列数：3
- 合计数自动校验通过。

### 表37-1 职工薪酬明细（page 2）

- source locator：`page=2;bbox=50.0,97.3,551.8,277.2`
- 行数：5，列数：4
- 合计数自动校验通过。

## 需要人工复核

- 第 3 页仅有页脚文字层，疑似图片页；如运行时启用 `--ocr`，需人工复核 OCR 候选文本和原 PDF 图像。
- 表格列边界由坐标间距启发式判断，遇到跨页表格、合并单元格、长备注时需要人工复核。
- 页眉页脚已在 Markdown 正文中排除，但仍保留在结构化 source locator 可追溯；应抽查是否误删正文。
- 金额单位、脚注语义、Excel GT 对齐规则不能完全自动判断，应由业务复核。

## 优化建议

- 接入 pdfplumber/PyMuPDF 后保留更完整的字体、字号、图片对象和表格线索。
- 对 `needs_ocr: true` 页面接入 Tesseract/PaddleOCR，并记录 OCR 置信度和人工确认状态。
- 为跨页表格建立 table_id 延续规则，合并标题、表头和页码来源。
- 将 bad case 写回 Skill/SOP：记录触发条件、错误样例、修复规则和回归样例。
