#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.8.1 — 入口点

使用方式:
    python -m novel_crawler_v3_8_1
"""

import sys
import traceback

from .crawler import NovelCrawler


def main():
    """主入口函数"""
    try:
        NovelCrawler().run()
    except KeyboardInterrupt:
        print("\n已退出")
    except Exception as e:
        print(f"\n[致命错误] {e}")
        traceback.print_exc()
        input("按回车键退出...")


if __name__ == '__main__':
    main()
