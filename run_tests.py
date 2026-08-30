# -*- coding: utf-8 -*-
"""
测试运行器（项目根目录执行）：
    python3.11 run_tests.py
全部用例离线运行，不依赖 Tesseract / OpenAI API Key / 真实 confidential 文件。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
