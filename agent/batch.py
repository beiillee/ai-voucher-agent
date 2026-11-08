# -*- coding: utf-8 -*-
"""
Batch Runner
============
多文件批量核对：一次上传多个 PDF 凭证文件，共用一个序时账。

隔离原则（避免多文件互相污染）：
  1. 每个 PDF 单独解析，OCR 缓存按文件名分目录，不共用。
  2. 每个 PDF 单独与序时账匹配，pairs 上打 source_file 标记。
  3. 同一张序时账凭证被多个文件的 PDF 同时匹配时，在汇总层按「文件覆盖」统计，
     不做跨文件的相互覆盖，也不会因为别的文件匹配过就把本文件的结果抹掉。
  4. 单个文件失败不影响其他文件，失败原因逐文件记录。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.excel_analyzer import analyze_excel
from tools.pdf_parser import parse_pdf
from tools.voucher_extractor import extract_vouchers
from tools.reconciliation import reconcile
from tools.anomaly_detector import detect_anomalies


def run_batch(
    pdf_paths: list[str | Path],
    ledger_path: str | Path,
    output_dir: str | Path = "output",
) -> dict[str, Any]:
    """批量核对主入口。返回逐文件结果 + 汇总。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ledger_res = analyze_excel(ledger_path)
    ledger_vouchers = ledger_res["vouchers"]

    files: list[dict] = []
    all_pairs: list[dict] = []
    covered_ledger: set[int] = set()

    for p in pdf_paths:
        p = Path(p)
        fdir = output_dir / p.stem  # 每个文件独立的缓存目录
        fdir.mkdir(parents=True, exist_ok=True)
        try:
            pdf_res = parse_pdf(p, cache_dir=fdir / "cache")
            vouchers = extract_vouchers(pdf_res)
            for v in vouchers:
                v["source_file"] = p.name
            recon = reconcile(vouchers, ledger_vouchers)
            anom = detect_anomalies(vouchers, ledger_vouchers, recon)

            for pair in recon["pairs"]:
                pair["source_file"] = p.name
            all_pairs.extend(recon["pairs"])
            # 本文件覆盖到的序时账凭证下标 = 全集 - 本文件未匹配集合
            unmatched_idx = {u["ledger_index"] for u in recon["unmatched_ledger"]}
            covered_ledger.update(
                i for i in range(len(ledger_vouchers)) if i not in unmatched_idx)

            files.append({
                "file": p.name,
                "path": str(p),
                "ok": True,
                "n_pages": pdf_res["n_pages"],
                "used_ocr": pdf_res["used_ocr"],
                "has_text_layer": pdf_res["has_text_layer"],
                "n_pdf_vouchers": len(vouchers),
                "pdf_vouchers": vouchers,
                "reconciliation": recon,
                "anomalies": anom,
                "warnings": pdf_res.get("warnings") or [],
            })
        except Exception as e:  # 单文件失败不拖垮整批
            files.append({
                "file": p.name,
                "path": str(p),
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "n_pages": 0,
                "n_pdf_vouchers": 0,
                "pdf_vouchers": [],
                "reconciliation": {"pairs": [], "summary": {}, "unmatched_ledger": [],
                                   "duplicates": {"ledger": [], "pdf": []}},
                "anomalies": {"anomalies": [], "n_anomalies": 0, "n_info": 0},
                "warnings": [],
            })

    ok_files = [f for f in files if f["ok"]]
    ledger_only_n = len(ledger_vouchers) - len(covered_ledger)

    summary = {
        "n_files": len(files),
        "n_files_ok": len(ok_files),
        "n_files_failed": len(files) - len(ok_files),
        "total_pdf": sum(f["n_pdf_vouchers"] for f in ok_files),
        "total_ledger": len(ledger_vouchers),
        "matched": sum(f["reconciliation"]["summary"].get("matched", 0) for f in ok_files),
        "date_mismatch": sum(f["reconciliation"]["summary"].get("date_mismatch", 0) for f in ok_files),
        "amount_mismatch": sum(f["reconciliation"]["summary"].get("amount_mismatch", 0) for f in ok_files),
        "suspected": sum(f["reconciliation"]["summary"].get("suspected", 0) for f in ok_files),
        "pdf_only": sum(f["reconciliation"]["summary"].get("pdf_only", 0) for f in ok_files),
        "ocr_uncertain": sum(f["reconciliation"]["summary"].get("ocr_uncertain", 0) for f in ok_files),
        "ledger_only": ledger_only_n,
    }
    summary["match_rate_pct"] = (
        round((summary["matched"] + summary["date_mismatch"] + summary["amount_mismatch"])
              / summary["total_pdf"] * 100, 1) if summary["total_pdf"] else 0.0)

    return {
        "files": files,
        "pairs": all_pairs,
        "summary": summary,
        "ledger": {
            "source": ledger_res["source"],
            "sheet_name": ledger_res["sheet_name"],
            "n_rows": ledger_res["n_rows"],
            "n_cols": ledger_res["n_cols"],
            "n_vouchers": len(ledger_vouchers),
            "has_date_dim": ledger_res.get("has_date_dim", False),
            "column_mapping": ledger_res["column_mapping"],
            "warnings": ledger_res["warnings"],
        },
    }
