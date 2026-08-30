# AI 财务凭证智能核对 Agent（AI Financial Voucher Reconciliation Agent）

一个真正可运行的 MVP：把**会计凭证 PDF**（扫描件）与**企业序时账 Excel** 自动核对，
识别账证不符并输出可追溯的 Excel 核查报告。

> ⚠️ **定位声明**：本工具是「财务资料智能核对**辅助**工具」。**不**声称能
> “自动完成审计”“自动判断财务造假”“替代审计人员”。所有输出仅作辅助线索，
> 最终判断须由具备资质的财务/审计人员做出。

> 🔗 **在线演示**：https://a91a5ee45b373f309.app.workbuddy.link （免装环境，上传 PDF + Excel 即可体验）
>
> 📌 本项目为个人简历 / 作品集展示用 MVP：核心核对逻辑全部用 Python 确定性实现，LLM 仅为可选增强（未配置 API Key 也能完整运行）。

---

## 一、项目结构

```
ai_voucher_agent/
├── app.py                      # Streamlit 前端
├── main.py                     # 命令行入口（可直接运行 / 打包成 exe）
├── 启动网页版.bat               # Windows 一键启动网页（双击即用，需先装 Python）
├── 启动网页版.command           # macOS 一键启动网页（双击即用，需先装 Python）
├── agent/
│   ├── orchestrator.py         # Agent 编排器：按文件类型自主调度工具链
│   └── prompts.py              # LLM 提示词（可选）
├── tools/
│   ├── pdf_parser.py           # 判断是否文字层；扫描件转 OCR
│   ├── ocr.py                  # Tesseract 中文 OCR（渲染+识别+置信度）
│   ├── voucher_extractor.py    # 从 OCR 文本提取结构化凭证字段（跨页聚合）
│   ├── excel_analyzer.py       # 读取/自动映射序时账字段
│   ├── reconciliation.py       # 凭证 ↔ 序时账匹配（优先级+模糊）
│   ├── anomaly_detector.py     # 10 类异常检测
│   └── report_generator.py     # 生成 Excel 报告（3 Sheet）
├── data/                       # OCR 缓存 / 渲染图片（运行产物）
├── output/                     # 核查结果 Excel（运行产物）
├── requirements.txt
├── .env.example
├── .github/workflows/build-windows-exe.yml  # 推到 GitHub 后自动构建 Windows exe
└── README.md
```

## 二、技术栈

`Python` · `pandas` · `openpyxl` · `pdfplumber` · `PyMuPDF` · `pytesseract`(Tesseract chi_sim) ·
`Streamlit` · `python-dotenv` · （可选）`openai`

**设计原则**：能用规则解决的问题绝不交给 LLM。OCR、Excel 读取、字段映射、金额匹配、
差异计算、重复检测、异常统计全部由 Python 确定性完成；LLM 只负责在配置 API Key 后
对异常生成自然语言解释，不参与任何计算或决策。

## 三、安装

```bash
cd ai_voucher_agent
pip install -r requirements.txt        # 或 sudo pip3 install -r requirements.txt

# OCR 依赖系统 Tesseract + 中文包
sudo apt-get install -y tesseract-ocr tesseract-ocr-chi-sim
tesseract --list-langs | grep chi_sim  # 确认中文包已安装
```

## 四、运行

### 1) 命令行（CLI）端到端

```bash
cd ai_voucher_agent
python3.11 -m agent.orchestrator <凭证.pdf> <序时账.xlsx> [输出目录，默认 output]
# 示例（使用本项目真实样例文件）：
python3.11 -m agent.orchestrator \
  /root/uploads/1788011094935791699-2023-10-1211.pdf \
  /root/uploads/1788011095541198983-副本202301-202412序时账.xlsx
# 报告默认输出到 ./output/voucher_reconciliation_result.xlsx
```

### 2) Streamlit 网页

```bash
streamlit run app.py
# 浏览器打开后上传 ① 凭证PDF ② 序时账Excel，点击「开始智能核对」
```

### 3) 免敲命令：双击启动器（推荐给非技术用户）

下载 zip 解压后，**无需记命令**：

- **Windows**：双击 `启动网页版.bat` → 自动装依赖 → 自动开浏览器 `http://localhost:8501`
- **macOS**：双击 `启动网页版.command` → 同上

> 前提：本机已安装 **Python 3.11+**（Windows 安装时勾选 “Add python.exe to PATH”）。
> 若不想装 Python，见下方「打包成 exe / 部署网页」方案。

### 4) 打包成 exe（彻底免环境，给别人分发）

本项目源码在 **Linux** 上开发，PyInstaller 只能打包出“当前系统”的可执行文件，
因此**不能直接从这里生成 Windows 的 `.exe`**。正确做法是用仓库里已配好的
`.github/workflows/build-windows-exe.yml`：**把代码推到 GitHub 后，GitHub 会在
Windows 机器上自动构建 `.exe`**，在 Actions 运行结果的 Artifacts 里下载即可。

```bash
# 本地也能打包（需在自己电脑上，且与目标系统一致）：
pip install pyinstaller
pyinstaller --onefile --name voucher-agent main.py
# 产物在 dist/voucher-agent(.exe)
```

双击 `voucher-agent.exe` → 按提示输入 PDF / Excel 路径 → 自动生成结果 Excel。

### 5) 免安装网页（给别人在线试用）

把 GitHub 仓库部署到 [Streamlit Cloud](https://streamlit.io/cloud)（连 GitHub 一键部署），
他人即获得一个**永久公网网址**，打开就能传文件核对，连 Python 都不用装。


## 五、真实样例文件说明（已用于验证）

- **凭证 PDF**：`2023-10-1211.pdf` 是**扫描件**（无文字层），27 页 =
  **同一张记账凭证（第 1211 号，2023-10-31，江苏宏亿精工股份有限公司）的拆页明细**，
  含 159 个「采购暂估」供应商贷方分录，借方合计 **41,778,905.63**。
- **序时账 Excel**：1 个 sheet「凭证」，约 19.2 万行，聚合为 **36,242 个凭证**，
  其中 `（记）-2023-10-1211` 的借/贷合计均为 41,778,905.63、159 行。

> 因为是「部分凭证扫描 ↔ 全年账」的真实场景，结果中绝大多数序时账凭证
> 标记为 `LEDGER_ONLY`（信息性，表示 PDF 未覆盖），这是**符合业务实际**的，
> 而非错误。核心校验点在于：PDF 提取的 1211 号凭证总额与序时账 1211 号
> **借/贷合计是否一致**。

## 六、真实运行结果（本项目实测）

使用上述两个真实文件端到端运行（`python3.11 -m agent.orchestrator`），结果：

| 指标 | 数值 | 说明 |
|---|---|---|
| PDF 凭证数 | 1 | 即第 1211 号（27 页拆页汇总） |
| 序时账凭证数 | 36,242 | Excel 约 19.2 万行聚合而来 |
| 提取借方合计 | 41,778,905.63 | 与凭证头「借方合计」一致 |
| 成功匹配 | 1 / 1 | PDF 1211 ↔ 序时账 `(记)-2023-10-1211` |
| 金额不一致 | 0 | 借方差异 0.00 / 贷方差异 0.00 |
| 匹配率 | 100% | 匹配方式 `P1_完整key`（凭证号+期间） |
| 实质异常 | 0 | — |
| 信息性提示 | 2 | `LEDGER_ONLY`（PDF 仅覆盖 1/36242）+ OCR 内部借贷小计噪声（已用借贷平衡修正） |

**核心结论**：PDF 扫描件经 OCR+跨页聚合后，第 1211 号凭证借方合计 **41,778,905.63**
与序时账 `(记)-2023-10-1211` 的借/贷合计**完全一致（差异 0.00）**，判定为 `MATCHED`。
OCR 对各页「贷方小计」的识别存在噪声，Agent 通过「借贷必平衡」先验自动以借方校核，
避免误报，并在报告中保留原因与置信度，确保可追溯。

## 七、匹配与异常逻辑

**匹配优先级**：P1 凭证号+期间 → P1b 期间+编号 → P2 仅编号 → P4 模糊（容 OCR 错：凭证号数字被
轻微误识如 `121l↔1211` 时仍能匹配）；输出 `match_status / match_method / match_confidence`，并解释原因。

**补充字段抽取**（用户原始规格要求）：在日期/凭证号/核算单位/借贷合计之外，额外抽取
**交易对手**（采购暂估行里的供应商公司名，跨页去重）、**附件数**、**发票/合同关键字段**。
OCR 噪声下 best-effort：本样例凭证的附件数、发票/合同号在扫描件中未出现，如实标 `NULL`；
交易对手则从 PDF 与序时账两侧分别抽取并在报告中并排展示（序时账侧来自「摘要/会计科目」文本，
更干净）。**绝不编造**任何未识别字段。

**10 类异常**：LEDGER_ONLY、PDF_ONLY、VOUCHER_NO_MISMATCH、DATE_MISMATCH、
AMOUNT_MISMATCH、DEBIT_CREDIT_IMBALANCE、DUPLICATE_VOUCHER、OCR_UNCERTAIN、
KEY_FIELD_MISSING、NON_UNIQUE_MATCH。

## 八、输出报告 `voucher_reconciliation_result.xlsx`

- **核对结果**：每个 PDF 凭证的逐笔核对明细（含 PDF/序时账借贷、差异、状态、置信度、原因）
- **异常清单**：所有异常（红/蓝区分严重度，信息性单独标注）
- **统计汇总**：总数、匹配率、异常分布

每份结果都保留原始数据、提取结果、匹配依据、异常原因、confidence，确保**可溯源**。

## 九、LLM（可选）

复制 `.env.example` 为 `.env`，填入 `OPENAI_API_KEY` 后，勾选「启用 LLM 异常解释」
即可对异常生成自然语言说明。未配置时所有基础核对功能**照常运行**。

## 十、合规与边界

- 仅作辅助核对；不替代审计/财务人员判断。
- 不修改原始 Excel/PDF 文件。
- OCR 无法可靠识别的字段标记为缺失并保留原文，**绝不编造**。

## 十一、脱敏样例数据与单元测试

为便于**开源 / 脱敏演示 / CI 自动化**，项目内置一份虚构样例数据，
不依赖任何 confidential 真实文件、无需 Tesseract、无需 API Key 即可全链路运行。

## 十二、部署到 Streamlit Cloud（免安装公网访问）

把本仓库推到 GitHub 后，即可一键部署为**永久公网网页**，他人打开链接即用、无需安装任何环境。

1. 将代码推到 GitHub（公开仓库）：
   ```bash
   git remote add origin https://github.com/<你的用户名>/ai-voucher-agent.git
   git push -u origin master        # 或 git push -u origin main
   ```
2. 打开 [streamlit.io/cloud](https://streamlit.io/cloud) → 用 GitHub 登录 → **New app**。
3. 选择本仓库，Main file path 填 `app.py` → **Deploy**。
4. 约 1–2 分钟后获得 `https://<app>.streamlit.app` 永久链接。

> 说明：云端默认环境未预装 Tesseract，**纯扫描件 OCR** 可能不可用；
> 文字层 PDF 与账证核对逻辑均正常。如需云端也支持扫描件 OCR，
> 在仓库根目录加 `packages.txt` 写入 `tesseract-ocr` 与 `tesseract-ocr-chi-sim` 即可。

### 1) 生成脱敏样例（可选，已附带）

```bash
python3.11 make_sample_data.py
# 产出：
#   data/sample_ledger.xlsx    —— 脱敏序时账（虚构主体 示例精工/示例贸易…）
#   data/sample_voucher.pdf    —— 含中文的「文字层」样例凭证（非扫描件，
#                                  pdfplumber 可直接抽取，无需 OCR）
#                                  含 第1211号(与序时账完全匹配) 与 第999号(序时账无→PDF_ONLY)
```

### 2) 运行单元测试

```bash
python3.11 run_tests.py
# 34 个用例全部通过（覆盖：Excel 聚合、字段抽取、匹配优先级
# P1/P1b/P2/P4、10 类异常检测、报告生成、端到端集成）
```

> 设计要点：脱敏样例的「文字层 PDF」走 `parse_pdf` 的**文字层分支**，
> 因此测试完全离线可跑；真实扫描件则走 OCR 分支（见第五、六节）。
> 抽取逻辑对两类信号（合计行 / 全局大额众数）做**合并取众数**，
> 以同时稳健应对清晰 PDF 与扫描 OCR 噪声。
