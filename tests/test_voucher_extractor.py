# -*- coding: utf-8 -*-
"""凭证字段抽取测试：金额千分位+空格、跨页聚合、OCR 噪声分组、交易对手、附件缺失。"""
import unittest
from pathlib import Path

from tools.pdf_parser import parse_pdf
from tools.voucher_extractor import (
    extract_vouchers,
    _extract_page_fields,
    normalize_pdf_voucher_key,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = ROOT / "data" / "sample_voucher.pdf"


def _page(text, conf=90.0, no=None):
    return {"text": text, "ocr_confidence": conf, "page_no": no or 1}


class TestVoucherExtractor(unittest.TestCase):
    def test_sample_pdf_extraction(self):
        r = parse_pdf(SAMPLE_PDF)
        vs = extract_vouchers(r)
        by_no = {v["voucher_no"]: v for v in vs}
        self.assertIn("1211", by_no)
        self.assertIn("999", by_no)
        self.assertAlmostEqual(by_no["1211"]["debit_total"], 41_778_905.63, 2)
        self.assertAlmostEqual(by_no["999"]["debit_total"], 1_234_567.89, 2)

    def test_amount_with_comma_and_space(self):
        # OCR 常把 41,778,905.63 识别成 "41, 778, 905. 63"
        f = _extract_page_fields("合计 Total 41, 778, 905. 63")
        self.assertAlmostEqual(f["debit_total"], 41_778_905.63, 2)

    def test_ocr_noise_grouping_by_majority(self):
        # 某一页 OCR 漏识别凭证号(None)，应归入众数凭证号 1211
        pages = [
            _page("第 1211 号  2023 年 10 月 31 日  核算单位：甲有限公司"),
            _page("第 1211 号  2023 年 10 月 31 日  核算单位：甲有限公司"),
            _page("2023 年 10 月 31 日  核算单位：甲有限公司", no=3),  # 漏号
        ]
        vs = extract_vouchers({"pages": pages})
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0]["voucher_no"], "1211")

    def test_counterparty_and_missing_attachment(self):
        text = ("第 1211 号\n核算单位：乙科技股份有限公司\n"
                "采购暂估|上海示例物资有限公司\n采购暂估|北京示例设备有限公司")
        v = extract_vouchers({"pages": [_page(text)]})[0]
        cps = " ".join(v["counterparties"])
        self.assertIn("上海示例物资有限公司", cps)
        self.assertIn("北京示例设备有限公司", cps)
        self.assertIsNone(v["attachment"], "无附件栏时应标 None，不编造")

    def test_normalize_pdf_key(self):
        v = extract_vouchers({"pages": [_page("第 1211 号  2023 年 10 月 31 日")]})[0]
        self.assertEqual(normalize_pdf_voucher_key(v), ("记", "2023-10", "1211"))


if __name__ == "__main__":
    unittest.main()
