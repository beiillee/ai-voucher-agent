# -*- coding: utf-8 -*-
"""
PDF Parser Tool
===============
职责：判断 PDF 是否含文字层。
  - 有文字层：直接 pdfplumber 提取文本（精度高、快）
  - 无文字层（扫描件）：调用 OCR Tool 渲染+OCR
返回统一的 pages 结构：[{page_no, text, source(text|ocr), ocr_confidence, image_size}, ...]
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pdfplumber

from .ocr import ocr_pdf, save_cache, load_cache


CACHE_FILENAME = "pdf_parser_cache.json"


def _has_text_layer(pdf_path: str, sample_pages: int = 3, min_chars: int = 20) -> bool:
    """抽样前 sample_pages 页，若累计文字长度 >= min_chars 视为有文字层。"""
    total = 0
    with pdfplumber.open(pdf_path) as pdf:
        for i in range(min(sample_pages, pdf.pages.__len__())):
            txt = pdf.pages[i].extract_text() or ""
            total += len(txt)
    return total >= min_chars


def parse_pdf(
    pdf_path: str | Path,
    cache_dir: str | Path | None = None,
    force_ocr: bool = False,
    ocr_dpi: int = 300,
    ocr_psm: int = 4,
    ocr_fuse_psm: bool = True,
) -> dict[str, Any]:
    """
    解析 PDF。
    返回：{
      source, n_pages, has_text_layer, used_ocr, pages: [...], warnings: [...]
    }
    若提供 cache_dir 且存在 cache 则直接使用，节省重复 OCR。
    """
    pdf_path = str(pdf_path)
    cache_dir_p = Path(cache_dir) if cache_dir else None
    # 按 PDF 文件名区分缓存，避免不同文件互相命中陈旧缓存
    pdf_slug = Path(pdf_path).stem
    cache_file = (cache_dir_p / f"{pdf_slug}_{CACHE_FILENAME}") if cache_dir_p else None

    if cache_file and cache_file.exists():
        pages = load_cache(cache_file)
        return {
            "source": pdf_path,
            "n_pages": len(pages),
            "has_text_layer": not force_ocr,
            "used_ocr": pages[0].get("source", "ocr") == "ocr" if pages else False,
            "pages": pages,
            "warnings": ["loaded from cache"],
        }

    has_text = _has_text_layer(pdf_path) if not force_ocr else False
    warnings: list[str] = []

    if has_text:
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, p in enumerate(pdf.pages):
                txt = p.extract_text() or ""
                pages.append({
                    "page_no": i + 1,
                    "text": txt,
                    "source": "text",
                    "ocr_confidence": None,
                    "image_size": (int(p.width), int(p.height)),
                })
        used_ocr = False
    else:
        if not force_ocr:
            warnings.append("PDF 未检测到文字层，判定为扫描件，已自动调用 OCR（tesseract chi_sim）")
        raw = ocr_pdf(pdf_path, dpi=ocr_dpi, psm=ocr_psm, fuse_psm=ocr_fuse_psm,
                      save_images_dir=str(cache_dir_p / "pages") if cache_dir_p else None)
        for r in raw:
            r["source"] = "ocr"
        pages = raw
        used_ocr = True

    if cache_file:
        save_cache(pages, cache_file)

    return {
        "source": pdf_path,
        "n_pages": len(pages),
        "has_text_layer": has_text,
        "used_ocr": used_ocr,
        "pages": pages,
        "warnings": warnings,
    }
