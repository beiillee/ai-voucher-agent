# -*- coding: utf-8 -*-
"""集成测试：用真实 OCR 缓存 + 真实 Excel 跑通核心链路（不重复 OCR）。"""
import sys
sys.path.insert(0, "/workspace/ai_voucher_agent")

from tools.ocr import load_cache
from tools.voucher_extractor import extract_vouchers
from tools.excel_analyzer import analyze_excel
from tools.reconciliation import reconcile
from tools.anomaly_detector import detect_anomalies
from tools.report_generator import generate_report

PDF = "/root/uploads/1788011094935791699-2023-10-1211.pdf"
LED = "/root/uploads/1788011095541198983-副本202301-202412序时账.xlsx"

print(">>> 1) 加载真实 OCR 缓存")
pages = load_cache("/workspace/ai_voucher_agent/data/ocr_cache.json")
print(f"   OCR 页数: {len(pages)}, 平均置信度: {sum(p['ocr_confidence'] for p in pages)/len(pages):.1f}")

print(">>> 2) 提取 PDF 凭证")
pdf_result = {"pages": pages, "n_pages": len(pages)}
pdf_vs = extract_vouchers(pdf_result)
print(f"   提取凭证数: {len(pdf_vs)}")
for v in pdf_vs:
    print(f"   凭证{v['voucher_no']} 日期{v['date']} 公司{v['company']} "
          f"借方合计{v['debit_total']} 贷方各页和{v['credit_total_pages']} "
          f"页数{v['page_count']} 行数PDF{v['line_count_pdf']} "
          f"OCR置信{v['ocr_confidence_avg']} 提取置信{v['extraction_confidence']} 缺{v['unparsed_fields']}")
    print(f"     附件数={v['attachment']} 交易对手数={v['counterparty_count']} "
          f"样例={v['counterparties'][:3]} 发票/合同数={len(v['invoices'])+len(v['contracts'])}")

print(">>> 3) 读取序时账")
led_res = analyze_excel(LED)
print(f"   行{led_res['n_rows']} 凭证{len(led_res['vouchers'])}")

# 序时账 1211(10月) 交易对手抽样
led1211 = [x for x in led_res["vouchers"] if x.get("voucher_no") and "2023-10-1211" in str(x["voucher_no"])]
if led1211:
    print(f"   序时账(记)-2023-10-1211 交易对手: {led1211[0]['counterparties'][:3]}")

print(">>> 4) 匹配")
recon = reconcile(pdf_vs, led_res["vouchers"])
print("   summary:", recon["summary"])

print(">>> 5) 异常检测")
anom = detect_anomalies(pdf_vs, led_res["vouchers"], recon)
print(f"   异常(非信息): {anom['n_anomalies']}, 信息性: {anom['n_info']}")
for a in anom["anomalies"][:6]:
    print("   -", a["type"], a.get("voucher_no"), a.get("detail"))

print(">>> 6) 生成报告")
path = generate_report("/workspace/ai_voucher_agent/output/voucher_reconciliation_result.xlsx", recon, anom)
print(f"   报告已生成: {path}")
