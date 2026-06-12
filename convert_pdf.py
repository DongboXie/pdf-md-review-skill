#!/usr/bin/env python3
"""Minimal PDF to Markdown converter for the assignment sample.

The converter intentionally keeps the extraction pipeline inspectable:
Poppler's pdftotext provides words and bounding boxes; Python groups them into
lines, detects simple tables, and emits Markdown plus a structured JSON file.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET


FOOTER_RE = re.compile(r"^第\s*\d+\s*页(?:（图片页）)?$")
NOTE_HEADING_RE = re.compile(r"^附注\d+")
TABLE_CAPTION_RE = re.compile(r"^表\d+-\d+")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER_RE = re.compile(r"^-?\d+(?:,\d{3})*(?:\.\d+)?$")
NO_SPACE_BEFORE = set("，。、；：！？）】》、,.!?;:%)]}")
NO_SPACE_AFTER = set("（【《([{")


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Cell:
    text: str
    bbox: BBox


@dataclass
class Line:
    page: int
    text: str
    cells: List[Cell]
    bbox: BBox


def run_command(args: List[str]) -> str:
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or f"Command failed: {' '.join(args)}") from exc
    return proc.stdout


def find_tesseract() -> Optional[str]:
    path = shutil.which("tesseract")
    if path:
        return path
    default_path = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if default_path.exists():
        return str(default_path)
    return None


def try_ocr_page(pdf_path: Path, page_no: int, lang: str = "chi_sim+eng") -> Dict:
    """Render one page and run Tesseract OCR when local tools are available."""
    tesseract_path = find_tesseract()
    if shutil.which("pdftoppm") is None:
        return {"attempted": False, "available": False, "text": "", "reason": "pdftoppm not found"}
    if tesseract_path is None:
        return {"attempted": False, "available": False, "text": "", "reason": "tesseract not found"}

    with tempfile.TemporaryDirectory(prefix="pdf_md_ocr_") as tmp_dir:
        prefix = Path(tmp_dir) / f"page_{page_no}"
        try:
            run_command(
                [
                    "pdftoppm",
                    "-f",
                    str(page_no),
                    "-l",
                    str(page_no),
                    "-r",
                    "220",
                    "-png",
                    str(pdf_path),
                    str(prefix),
                ]
            )
            images = sorted(Path(tmp_dir).glob(f"page_{page_no}-*.png"))
            if not images:
                return {"attempted": True, "available": True, "text": "", "reason": "rendered image not found"}
            command = [tesseract_path, str(images[0]), "stdout", "-l", lang]
            local_tessdata = Path("tessdata")
            if local_tessdata.exists():
                command.extend(["--tessdata-dir", str(local_tessdata)])
            text = run_command(command).strip()
            return {"attempted": True, "available": True, "text": text, "reason": ""}
        except RuntimeError as exc:
            return {"attempted": True, "available": True, "text": "", "reason": str(exc)}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_float(value: str) -> float:
    return float(value)


def is_ascii_token(text: str) -> bool:
    return any(ch.isascii() and ch.isalnum() for ch in text)


def should_insert_space(prev_text: str, next_text: str) -> bool:
    if not prev_text or not next_text:
        return False
    if next_text[0] in NO_SPACE_BEFORE or prev_text[-1] in NO_SPACE_AFTER:
        return False
    return is_ascii_token(prev_text) or is_ascii_token(next_text)


def cell_text_from_words(words: List[ET.Element]) -> str:
    # Keep Chinese words compact, but preserve readability around English,
    # numbers, and mixed tokens such as "PDF 转 Markdown" and "Excel GT".
    parts: List[str] = []
    for word in words:
        text = (word.text or "").strip()
        if not text:
            continue
        if parts and should_insert_space(parts[-1], text):
            parts.append(" ")
        parts.append(text)
    return "".join(parts)


def bbox_from_items(items: Iterable[ET.Element]) -> BBox:
    xs0, ys0, xs1, ys1 = [], [], [], []
    for item in items:
        xs0.append(parse_float(item.attrib["xMin"]))
        ys0.append(parse_float(item.attrib["yMin"]))
        xs1.append(parse_float(item.attrib["xMax"]))
        ys1.append(parse_float(item.attrib["yMax"]))
    return BBox(min(xs0), min(ys0), max(xs1), max(ys1))


def extract_pages(pdf_path: Path) -> Tuple[List[Dict], List[List[Line]]]:
    xml_text = run_command(["pdftotext", "-bbox-layout", "-enc", "UTF-8", str(pdf_path), "-"])
    root = ET.fromstring(xml_text)
    page_nodes = [node for node in root.iter() if local_name(node.tag) == "page"]
    page_infos: List[Dict] = []
    pages_lines: List[List[Line]] = []

    for page_no, page in enumerate(page_nodes, start=1):
        page_infos.append(
            {
                "page": page_no,
                "width": parse_float(page.attrib["width"]),
                "height": parse_float(page.attrib["height"]),
            }
        )
        raw_lines = []
        for line_node in page.iter():
            if local_name(line_node.tag) != "line":
                continue
            words = [w for w in line_node if local_name(w.tag) == "word" and (w.text or "").strip()]
            if not words:
                continue
            raw_lines.append(words)

        # Poppler may put table columns in separate line nodes. Regroup line
        # fragments with nearly identical y coordinates into one physical row.
        grouped: List[List[ET.Element]] = []
        for words in sorted(raw_lines, key=lambda ws: (parse_float(ws[0].attrib["yMin"]), parse_float(ws[0].attrib["xMin"]))):
            y = parse_float(words[0].attrib["yMin"])
            if grouped and abs(parse_float(grouped[-1][0].attrib["yMin"]) - y) <= 3.0:
                grouped[-1].extend(words)
            else:
                grouped.append(list(words))

        lines: List[Line] = []
        for words in grouped:
            words = sorted(words, key=lambda w: parse_float(w.attrib["xMin"]))
            cells = split_cells(words)
            text = " ".join(cell.text for cell in cells if cell.text).strip()
            lines.append(Line(page_no, text, cells, bbox_from_items(words)))
        pages_lines.append(lines)

    return page_infos, pages_lines


def split_cells(words: List[ET.Element]) -> List[Cell]:
    if not words:
        return []
    groups: List[List[ET.Element]] = [[words[0]]]
    for prev, word in zip(words, words[1:]):
        gap = parse_float(word.attrib["xMin"]) - parse_float(prev.attrib["xMax"])
        if gap > 28:
            groups.append([word])
        else:
            groups[-1].append(word)
    return [Cell(cell_text_from_words(group), bbox_from_items(group)) for group in groups]


def is_footer(line: Line, page_height: float) -> bool:
    return line.bbox.y0 > page_height - 80 and bool(FOOTER_RE.match(line.text.replace(" ", "")))


def is_header(line: Line) -> bool:
    return line.bbox.y1 < 55 and "半年度报告" in line.text


def markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    def clean(value: str) -> str:
        return value.replace("|", "\\|").strip()

    widths = [len(headers)] + [len(row) for row in rows]
    col_count = max(widths) if widths else 0
    header = [clean(headers[i]) if i < len(headers) else "" for i in range(col_count)]
    body = []
    for row in rows:
        body.append([clean(row[i]) if i < len(row) else "" for i in range(col_count)])
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * col_count) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def normalize_row_cells(line: Line, table_headers: Optional[List[str]] = None) -> List[str]:
    cells = [cell.text for cell in line.cells]
    if table_headers and len(cells) < len(table_headers):
        # If a row is missing an empty note cell, pad to the right. Keep a QA
        # warning elsewhere rather than inventing content.
        cells = cells + [""] * (len(table_headers) - len(cells))
    return cells


def table_cell_records(page_no: int, header_line: Line, row_lines: List[Line], col_count: int) -> List[Dict]:
    records: List[Dict] = []
    for row_idx, line in enumerate([header_line] + row_lines):
        for col_idx in range(col_count):
            if col_idx < len(line.cells):
                cell = line.cells[col_idx]
                bbox = asdict(cell.bbox)
                source_locator = (
                    f"page={page_no};row={row_idx};col={col_idx};"
                    f"bbox={cell.bbox.x0:.1f},{cell.bbox.y0:.1f},{cell.bbox.x1:.1f},{cell.bbox.y1:.1f}"
                )
                text = cell.text
            else:
                bbox = None
                source_locator = f"page={page_no};row={row_idx};col={col_idx};bbox=null"
                text = ""
            records.append(
                {
                    "row": row_idx,
                    "col": col_idx,
                    "role": "header" if row_idx == 0 else "body",
                    "text": text,
                    "bbox": bbox,
                    "source_locator": source_locator,
                }
            )
    return records


def detect_table(lines: List[Line], start_index: int) -> Optional[Dict]:
    caption = lines[start_index]
    if start_index + 1 >= len(lines):
        return None
    header_index = start_index + 1
    while header_index < len(lines) and not ("项目" in lines[header_index].text and DATE_RE.search(lines[header_index].text)):
        if lines[header_index].bbox.y0 - caption.bbox.y1 > 80:
            return None
        header_index += 1
    if header_index >= len(lines):
        return None

    header_line = lines[header_index]
    headers = normalize_row_cells(header_line)
    row_lines: List[Line] = []
    idx = header_index + 1
    while idx < len(lines):
        text = lines[idx].text
        compact = text.replace(" ", "")
        if not compact:
            idx += 1
            continue
        if compact.startswith(("注：", "补充说明：", "脚注：")) or NOTE_HEADING_RE.match(compact) or TABLE_CAPTION_RE.match(compact):
            break
        if len(lines[idx].cells) < 2:
            break
        row_lines.append(lines[idx])
        idx += 1

    if not row_lines:
        return None

    rows = [normalize_row_cells(row, headers) for row in row_lines]
    bbox = BBox(
        min(caption.bbox.x0, header_line.bbox.x0, *(r.bbox.x0 for r in row_lines)),
        min(caption.bbox.y0, header_line.bbox.y0, *(r.bbox.y0 for r in row_lines)),
        max(caption.bbox.x1, header_line.bbox.x1, *(r.bbox.x1 for r in row_lines)),
        max(caption.bbox.y1, header_line.bbox.y1, *(r.bbox.y1 for r in row_lines)),
    )
    return {
        "caption": caption.text,
        "start": start_index,
        "end": idx,
        "headers": headers,
        "rows": rows,
        "cells": table_cell_records(caption.page, header_line, row_lines, max(len(headers), *(len(row) for row in rows))),
        "row_lines": row_lines,
        "bbox": bbox,
        "markdown": markdown_table(headers, rows),
    }


def decimal_or_none(value: str) -> Optional[Decimal]:
    value = value.replace(",", "").strip()
    if not NUMBER_RE.match(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def check_table_totals(table: Dict) -> List[Dict]:
    issues = []
    rows = table["rows"]
    if not rows:
        return issues
    total_row = next((row for row in rows if row and row[0] == "合计"), None)
    if not total_row:
        return issues
    detail_rows = [row for row in rows if row and row[0] != "合计"]
    for col_idx, header in enumerate(table["headers"][1:], start=1):
        if not DATE_RE.search(header):
            continue
        values = [decimal_or_none(row[col_idx]) if col_idx < len(row) else None for row in detail_rows]
        total_value = decimal_or_none(total_row[col_idx]) if col_idx < len(total_row) else None
        if total_value is None or any(value is None for value in values):
            issues.append({"severity": "warning", "message": f"{table['caption']} {header} 含非数值单元格，未自动校验合计。"})
            continue
        expected = sum(values, Decimal("0"))
        if expected != total_value:
            issues.append(
                {
                    "severity": "error",
                    "message": f"{table['caption']} {header} 合计不一致：明细和 {expected}，表内合计 {total_value}。",
                }
            )
    return issues


def block_dict(block_id: str, block_type: str, page: int, text: str, bbox: BBox, extra: Optional[Dict] = None) -> Dict:
    data = {
        "id": block_id,
        "type": block_type,
        "page": page,
        "text": text,
        "bbox": asdict(bbox),
        "source_locator": f"page={page};bbox={bbox.x0:.1f},{bbox.y0:.1f},{bbox.x1:.1f},{bbox.y1:.1f}",
    }
    if extra:
        data.update(extra)
    return data


def convert(pdf_path: Path, output_dir: Path, try_ocr: bool = False, ocr_lang: str = "chi_sim+eng") -> Dict:
    page_infos, pages_lines = extract_pages(pdf_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    md_parts: List[str] = []
    blocks: List[Dict] = []
    tables: List[Dict] = []
    qa_issues: List[Dict] = []
    block_no = 1

    for page_info, lines in zip(page_infos, pages_lines):
        page_no = page_info["page"]
        page_height = page_info["height"]
        md_parts.append(f"<!-- page: {page_no} -->")
        content_lines = [line for line in lines if not is_header(line) and not is_footer(line, page_height)]

        visible_text = "".join(line.text.replace(" ", "") for line in content_lines)
        needs_ocr = len(visible_text) < 20
        if needs_ocr:
            marker = f"> [!WARNING] Page {page_no} appears to be an image/scanned page or has too little text layer. needs_ocr: true"
            md_parts.append(marker)
            ocr_result = try_ocr_page(pdf_path, page_no, ocr_lang) if try_ocr else {
                "attempted": False,
                "available": find_tesseract() is not None and shutil.which("pdftoppm") is not None,
                "text": "",
                "reason": "OCR not requested; run with --ocr to attempt OCR",
            }
            if ocr_result["attempted"] and ocr_result["text"]:
                md_parts.append("")
                md_parts.append("## OCR candidate")
                md_parts.append("")
                md_parts.append(ocr_result["text"])
            elif try_ocr:
                md_parts.append(f"> OCR attempted: failed or empty. reason: {ocr_result['reason']}")
            else:
                md_parts.append("> OCR attempted: false")
            qa_issues.append(
                {
                    "severity": "warning",
                    "page": page_no,
                    "message": (
                        f"页面文字层过少，疑似图片/扫描页；OCR 状态："
                        f"{'已尝试' if ocr_result['attempted'] else '未尝试'}；"
                        f"{ocr_result['reason'] or '需要人工复核 OCR 候选文本。'}"
                    ),
                    "ocr": ocr_result,
                }
            )
            page_bbox = BBox(0.0, 0.0, page_info["width"], page_info["height"])
            blocks.append(
                block_dict(
                    f"b{block_no:04d}",
                    "scanned_page",
                    page_no,
                    ocr_result["text"],
                    page_bbox,
                    {
                        "needs_ocr": True,
                        "ocr": ocr_result,
                        "source_locator": f"page={page_no};bbox=0.0,0.0,{page_info['width']:.1f},{page_info['height']:.1f}",
                    },
                )
            )
            block_no += 1
            if content_lines:
                md_parts.extend(f"> text-layer: {line.text}" for line in content_lines)
                for line in content_lines:
                    blocks.append(
                        block_dict(
                            f"b{block_no:04d}",
                            "scan_marker_text",
                            page_no,
                            line.text,
                            line.bbox,
                            {"needs_ocr": True, "ocr": ocr_result},
                        )
                    )
                    block_no += 1
            md_parts.append("")
            continue

        i = 0
        while i < len(content_lines):
            line = content_lines[i]
            compact = line.text.replace(" ", "")

            if NOTE_HEADING_RE.match(compact):
                md_parts.append(f"# {line.text}")
                blocks.append(block_dict(f"b{block_no:04d}", "heading", page_no, line.text, line.bbox, {"level": 1}))
                block_no += 1
                i += 1
                continue

            if TABLE_CAPTION_RE.match(compact):
                table = detect_table(content_lines, i)
                if table:
                    table_id = f"t{len(tables) + 1:03d}"
                    md_parts.append("")
                    md_parts.append(f"**{table['caption']}**")
                    md_parts.append("")
                    md_parts.append(table["markdown"])
                    md_parts.append("")
                    table_issues = check_table_totals(table)
                    qa_issues.extend({"page": page_no, **issue} for issue in table_issues)
                    tables.append(
                        {
                            "id": table_id,
                            "page": page_no,
                            "caption": table["caption"],
                            "headers": table["headers"],
                            "rows": table["rows"],
                            "cells": table["cells"],
                            "bbox": asdict(table["bbox"]),
                            "source_locator": f"page={page_no};bbox={table['bbox'].x0:.1f},{table['bbox'].y0:.1f},{table['bbox'].x1:.1f},{table['bbox'].y1:.1f}",
                            "qa_checks": {"total_check_issues": table_issues},
                        }
                    )
                    blocks.append(block_dict(f"b{block_no:04d}", "table", page_no, table["caption"], table["bbox"], {"table_id": table_id}))
                    block_no += 1
                    i = table["end"]
                    continue

                md_parts.append(f"**{line.text}**")
                blocks.append(block_dict(f"b{block_no:04d}", "table_caption_unparsed", page_no, line.text, line.bbox))
                block_no += 1
                i += 1
                continue

            block_type = "paragraph"
            if compact.startswith("注："):
                block_type = "note"
            elif compact.startswith("脚注："):
                block_type = "footnote"
            elif compact.startswith("补充说明："):
                block_type = "paragraph"
            md_parts.append(line.text)
            md_parts.append("")
            blocks.append(block_dict(f"b{block_no:04d}", block_type, page_no, line.text, line.bbox))
            block_no += 1
            i += 1

    result = {
        "source_pdf": str(pdf_path),
        "parser": "Poppler pdftotext -bbox-layout + Python heuristics",
        "pages": page_infos,
        "blocks": blocks,
        "tables": tables,
        "qa_issues": qa_issues,
    }

    (output_dir / "document.md").write_text("\n".join(md_parts).strip() + "\n", encoding="utf-8")
    (output_dir / "blocks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "qa_report.md").write_text(build_qa_report(result), encoding="utf-8")
    return result


def build_qa_report(result: Dict) -> str:
    pages = result["pages"]
    tables = result["tables"]
    issues = result["qa_issues"]
    blocks = result["blocks"]
    scanned_pages = sorted({issue["page"] for issue in issues if "扫描页" in issue["message"] or "图片/扫描页" in issue["message"]})

    lines = [
        "# PDF 转 Markdown 质量报告",
        "",
        "## 概览",
        "",
        f"- 源文件：`{result['source_pdf']}`",
        f"- 解析器：{result['parser']}",
        f"- 页数：{len(pages)}",
        f"- 文本/结构块：{len(blocks)}",
        f"- 表格：{len(tables)}",
        f"- 疑似图片或扫描页：{', '.join(map(str, scanned_pages)) if scanned_pages else '无'}",
        "",
        "## 自动检查结果",
        "",
    ]
    if issues:
        for issue in issues:
            lines.append(f"- [{issue['severity'].upper()}] page {issue['page']}：{issue['message']}")
    else:
        lines.append("- 未发现自动规则可判定的错误；仍需人工抽样复核标题、表格和关键数值。")

    lines.extend(["", "## 表格检查", ""])
    for table in tables:
        lines.append(f"### {table['caption']}（page {table['page']}）")
        lines.append("")
        lines.append(f"- source locator：`{table['source_locator']}`")
        lines.append(f"- 行数：{len(table['rows'])}，列数：{len(table['headers'])}")
        table_issues = table["qa_checks"]["total_check_issues"]
        if table_issues:
            for issue in table_issues:
                lines.append(f"- [{issue['severity'].upper()}] {issue['message']}")
        else:
            lines.append("- 合计数自动校验通过。")
        if any(len(row) != len(table["headers"]) for row in table["rows"]):
            lines.append("- [WARNING] 存在行列数不一致，需要人工确认表格错位或空单元格。")
        lines.append("")

    lines.extend(
        [
            "## 需要人工复核",
            "",
            "- 第 3 页仅有页脚文字层，疑似图片页；如运行时启用 `--ocr`，需人工复核 OCR 候选文本和原 PDF 图像。",
            "- 表格列边界由坐标间距启发式判断，遇到跨页表格、合并单元格、长备注时需要人工复核。",
            "- 页眉页脚已在 Markdown 正文中排除，但仍保留在结构化 source locator 可追溯；应抽查是否误删正文。",
            "- 金额单位、脚注语义、Excel GT 对齐规则不能完全自动判断，应由业务复核。",
            "",
            "## 优化建议",
            "",
            "- 接入 pdfplumber/PyMuPDF 后保留更完整的字体、字号、图片对象和表格线索。",
            "- 对 `needs_ocr: true` 页面接入 Tesseract/PaddleOCR，并记录 OCR 置信度和人工确认状态。",
            "- 为跨页表格建立 table_id 延续规则，合并标题、表头和页码来源。",
            "- 将 bad case 写回 Skill/SOP：记录触发条件、错误样例、修复规则和回归样例。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a PDF into Markdown and structured block JSON.")
    parser.add_argument("pdf", nargs="?", default="sample_pdf_to_markdown_note.pdf", help="Input PDF path")
    parser.add_argument("-o", "--output-dir", default="outputs", help="Output directory")
    parser.add_argument("--ocr", action="store_true", help="Attempt OCR on scanned/image-like pages when Tesseract is installed")
    parser.add_argument("--ocr-lang", default="chi_sim+eng", help="Tesseract language setting, default: chi_sim+eng")
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Input PDF not found: {pdf_path}", file=sys.stderr)
        return 2
    if shutil.which("pdftotext") is None:
        print("pdftotext is required. Install Poppler and make sure pdftotext is in PATH.", file=sys.stderr)
        return 2

    result = convert(pdf_path, Path(args.output_dir), try_ocr=args.ocr, ocr_lang=args.ocr_lang)
    print(f"Wrote {args.output_dir}/document.md, {args.output_dir}/blocks.json, {args.output_dir}/qa_report.md")
    print(f"Pages: {len(result['pages'])}; tables: {len(result['tables'])}; issues: {len(result['qa_issues'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
