#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.8.1 — 控制台工具
"""

import sys
import os


# ═══════════════════════════════════════════════════════════════════
# 控制台工具
# ═══════════════════════════════════════════════════════════════════
def init_console():
    """初始化控制台（Windows 下设置 UTF-8 编码）"""
    if sys.platform == 'win32':
        os.system('chcp 65001 >nul 2>&1')
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            pass


# 颜色代码
C = {
    "g": "\033[92m",  # 绿色
    "y": "\033[93m",  # 黄色
    "r": "\033[91m",  # 红色
    "c": "\033[96m",  # 青色
    "m": "\033[95m",  # 紫色
    "b": "\033[1m",   # 粗体
    "d": "\033[2m",   # 暗色
    "R": "\033[0m",   # 重置
}


def p(text, color="", end="\n"):
    """带颜色的打印函数"""
    prefix = C.get(color, "")
    suffix = C["R"] if prefix else ""
    print(f"{prefix}{text}{suffix}", end=end, flush=True)


def banner():
    """显示程序横幅"""
    p("=" * 56, "c")
    p("  [*] 万能小说爬虫 v3.8.1", "b")
    p("  自动识别目录 | 智能提取正文 | 增量更新 | EPUB导出", "d")
    p("  [!] 本程序仅供学习交流使用，请勿用于非法用途", "y")
    p("=" * 56, "c")
    print()


def select_format():
    """选择导出格式，返回 'txt' 或 'epub'"""
    options = ['txt', 'epub']
    labels = ['TXT (纯文本)', 'EPUB (电子书)']

    if sys.platform == 'win32':
        import msvcrt

        idx = 0

        def _render():
            for i, label in enumerate(labels):
                if i == idx:
                    p(f"    > {label}", "g")
                else:
                    p(f"      {label}", "d")

        _render()

        while True:
            ch = msvcrt.getwch()
            if ch in ('\r', '\n'):
                return options[idx]
            if ch == '\x00' or ch == '\xe0':
                arrow = msvcrt.getwch()
                sys.stdout.write('\033[2A\033[J')
                sys.stdout.flush()
                if arrow == 'H':
                    idx = (idx - 1) % len(options)
                elif arrow == 'P':
                    idx = (idx + 1) % len(options)
                _render()

    # 非 Windows: 简单输入选择
    while True:
        choice = input("  请选择格式 [txt/epub] > ").strip().lower()
        if choice in options:
            return choice
        p("  输入无效，请输入 txt 或 epub", "y")
