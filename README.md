# PDF 转 Markdown 小系统

这是一个最小可运行的 PDF 转 Markdown 作业实现。系统读取 `sample_pdf_to_markdown_note.pdf`，输出 Markdown、结构化 blocks/tables 和质量报告。

仓库内也保留了一份附件副本：`assignment_pdf_markdown_skill_assets/sample_pdf_to_markdown_note.pdf`。

## 如何运行

环境要求：

- Python 3.9+
- Poppler 命令行工具，确保 `pdftotext` 在 PATH 中

运行：

```bash
python convert_pdf.py sample_pdf_to_markdown_note.pdf -o outputs
```

如本机已安装 Tesseract 和中文语言包，可以对疑似扫描页尝试 OCR：

```bash
python convert_pdf.py sample_pdf_to_markdown_note.pdf -o outputs --ocr
```

本仓库包含 `tessdata/chi_sim.traineddata`、`tessdata/eng.traineddata` 和 `tessdata/osd.traineddata`。如果当前终端还没有刷新 PATH，脚本会自动尝试使用 `C:\Program Files\Tesseract-OCR\tesseract.exe`。

输出文件：

- `outputs/document.md`：带页码标记的 Markdown
- `outputs/blocks.json`：页面、文本块、表格、bbox 和 source locator
- `outputs/qa_report.md`：解析质量、问题和人工复核建议

## 当前实现

- 使用 `pdftotext -bbox-layout` 抽取文字层、词坐标和页面信息。
- 按坐标合并物理行，识别页眉、页脚、附注标题、表格标题和简单表格。
- Markdown 保留 `<!-- page: n -->`，表格输出为 Markdown table。
- `blocks.json` 保留 page、type、bbox、source locator、表格行列，并为表格输出单元格级 `cells` 信息。
- 对文字层很少的页面标记 `needs_ocr: true`，写入 `scanned_page` block，并支持通过 `--ocr` 调用 Tesseract 输出 OCR 候选文本。
- 对含“合计”的数值表格做明细求和校验。

## 已知限制和风险

- OCR 是可选能力；当前已验证可以通过 `--ocr` 跑出第 3 页 OCR 候选文本，但关键数字和文字仍需人工复核。
- 表格识别依赖坐标间距启发式，复杂跨页表格、多层表头、合并单元格可能错位。
- 没有做生产级版面分析，标题和正文识别只覆盖本样例所需的最小规则。
- 最大风险是表格行列边界、脚注归属和扫描页内容缺失，因此这些都写入 QA 报告要求人工复核。

## 实际用时

实际用时：约 1.5 小时，包括环境检查、脚本实现、运行生成 outputs、人工抽查 Markdown/JSON/QA 报告、补充 Skill/SOP 和 README。

## AI / 智能体使用说明

使用了 Codex 辅助完成：

- 读取作业要求并拆解最小闭环。
- 检查本机可用工具和依赖。
- 编写 `convert_pdf.py`、`skills/pdf_to_markdown_review_skill.md` 和 README。
- 运行脚本并根据输出做复核和修正。

人工/复核动作：

- 用 `pdfinfo` 和 `pdftotext` 抽查 PDF 页数与文字层。
- 检查 Markdown 是否包含页码、标题、表格、第 3 页扫描提示和 OCR 候选文本。
- 检查 `blocks.json` 是否包含表格、单元格 bbox、source locator、扫描页 block 和 OCR 状态。
- 检查 `qa_report.md` 是否明确说明扫描页、表格和人工复核风险。
- 对表格合计数做自动校验，并在报告中呈现结果。

避免盲信 AI 输出的做法：

- 代码必须真实运行并生成文件，不只写方案。
- 所有重要内容保留 page 和 bbox，便于回到原 PDF 复核。
- 对 OCR、复杂表格、GT 不一致等不确定内容只标记风险，不自动补最终答案。
- 将常见错误和人工复核点沉淀到 Skill/SOP，后续用 bad case 持续更新。

## 如果多给半天

优先优化：

- 接入 PyMuPDF/pdfplumber，读取字体、图片对象、表格线条，提高版面和表格识别稳定性。
- 增强 OCR 流程：输出 OCR 置信度、截图路径、人工确认状态，并把 OCR 表格候选结构化为 rows/cells。
- 增加跨页表格合并、表格标题归属、脚注归属和回归测试样例。
- 增加更多 CLI 参数，例如 `--keep-header-footer`、`--qa-only`、`--tessdata-dir`。

## Git 协作建议

如果放进团队代码库：

- 从 `main` 拉出短分支，例如 `feature/pdf-md-minimal-converter`。
- 每次只改当前任务相关文件，先看 `git status`，避免覆盖他人改动。
- 提交前查看 `git diff`，确认没有智能体误改无关文件。
- commit 拆成可 review 的粒度，例如 converter、outputs、skill/readme。
- PR 中附运行命令、样例输出、已知限制和需要人工复核的风险。
- review 时重点看解析规则、source locator、QA 报告和 bad case 是否覆盖。
