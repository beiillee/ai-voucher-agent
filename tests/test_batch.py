# -*- coding: utf-8 -*-
"""
批量处理与文件隔离测试
======================
验收标准第 3 条要求「多文件凭证可以正确隔离和识别」，这里逐项验证：

1. 两个 PDF 文件各自出结果，不互相污染
2. 每个文件有独立的缓存目录（同名文件不会串用 OCR 缓存）
3. 单个文件失败不影响其他文件，失败原因逐文件记录
4. 汇总层不重复统计账内未覆盖凭证
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from agent.batch import run_batch

ROOT = Path(__file__).resolve().parents[1]
DEMO_PDF = ROOT / "data" / "demo_recon" / "vouchers.pdf"
DEMO_XLSX = ROOT / "data" / "demo_recon" / "ledger.xlsx"
SAMPLE_PDF = ROOT / "data" / "sample_voucher.pdf"


class TestBatchIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp())
        cls.res = run_batch([DEMO_PDF, SAMPLE_PDF], DEMO_XLSX, output_dir=cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_each_file_has_own_result(self):
        files = {f["file"]: f for f in self.res["files"]}
        self.assertEqual(len(files), 2)
        self.assertTrue(all(f["ok"] for f in files.values()))
        # 两个文件各自解析出的凭证数应分别对应各自内容
        self.assertEqual(files["vouchers.pdf"]["n_pdf_vouchers"], 7)
        self.assertEqual(files["sample_voucher.pdf"]["n_pdf_vouchers"], 2)

    def test_pairs_carry_source_file(self):
        """逐笔结果必须标明来自哪个文件，否则多文件下无法追溯。"""
        for p in self.res["pairs"]:
            self.assertIn("source_file", p)
            self.assertTrue(p["source_file"])
        self.assertEqual(len(self.res["pairs"]),
                         sum(len(f["reconciliation"]["pairs"]) for f in self.res["files"]))

    def test_per_file_cache_dir(self):
        """每个文件一个缓存子目录，避免同名文件串用 OCR 缓存。"""
        for name in ("vouchers", "sample_voucher"):
            self.assertTrue((self.out / name).is_dir(), f"缺少独立目录 {name}")

    def test_ledger_only_counted_once(self):
        """账内未覆盖凭证只在汇总层算一次，不能按文件累加。"""
        s = self.res["summary"]
        self.assertEqual(s["ledger_only"],
                         len(self.res["reconciliation"]["unmatched_ledger"]))
        self.assertLessEqual(s["ledger_only"], s["total_ledger"])

    def test_report_generated_with_seven_sheets(self):
        import openpyxl
        wb = openpyxl.load_workbook(self.res["report_path"])
        self.assertEqual(len(wb.sheetnames), 7)
        self.assertIn("未匹配序时账", wb.sheetnames)

    def test_progress_callback_ordered(self):
        seen = []
        with tempfile.TemporaryDirectory() as td:
            run_batch([DEMO_PDF, SAMPLE_PDF], DEMO_XLSX, output_dir=td,
                      on_progress=lambda d, t, n: seen.append((d, t, n)))
        self.assertEqual([x[0] for x in seen], [1, 2])
        self.assertTrue(all(x[1] == 2 for x in seen))


class TestBatchFailureTolerance(unittest.TestCase):
    def test_bad_file_does_not_break_batch(self):
        bad = Path(tempfile.mkdtemp()) / "broken.pdf"
        bad.write_bytes(b"not a real pdf")
        out = Path(tempfile.mkdtemp())
        try:
            res = run_batch([bad, DEMO_PDF], DEMO_XLSX, output_dir=out)
            by_name = {f["file"]: f for f in res["files"]}
            self.assertFalse(by_name["broken.pdf"]["ok"])
            self.assertIn("error", by_name["broken.pdf"])
            self.assertTrue(by_name["vouchers.pdf"]["ok"], "坏文件不应拖垮其他文件")
            self.assertEqual(res["summary"]["n_files_failed"], 1)
            self.assertEqual(res["summary"]["n_files_ok"], 1)
            # 正常文件的结果仍然完整
            self.assertEqual(len(by_name["vouchers.pdf"]["reconciliation"]["pairs"]), 7)
        finally:
            shutil.rmtree(out, ignore_errors=True)
            shutil.rmtree(bad.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
