#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.8.1 — 启动脚本

双击运行或在命令行执行:
    python 启动爬虫.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from novel_crawler_v3_8_1 import NovelCrawler

if __name__ == '__main__':
    import traceback
    try:
        NovelCrawler().run()
    except KeyboardInterrupt:
        print("\n已退出")
    except Exception as e:
        print(f"\n[致命错误] {e}")
        traceback.print_exc()
        input("按回车键退出...")