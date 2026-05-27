#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.8.1 — 通用中文小说下载器

支持大部分小说网站，自动识别章节目录和正文，导出为 TXT/EPUB 文件。

模块结构:
- config.py: 全局设置与网站配置
- console.py: 控制台工具（颜色、横幅、格式选择）
- parsers.py: HTML 解析与内容提取
- exporters.py: 文件导出（TXT/EPUB）
- updater.py: 增量更新逻辑
- crawler.py: 核心爬虫逻辑
"""

__version__ = "3.8.1"
__author__ = "万能小说爬虫"

from .config import GLOBAL_SETTINGS, DEFAULT_SITE_CONFIG, SiteConfig, WEBSITE_CONFIGS
from .console import init_console, banner, p, select_format
from .parsers import HTMLParser
from .exporters import save_txt, save_epub, generate_safe_filename
from .updater import find_existing_file, parse_existing_chapters, update_existing_file
from .crawler import NovelCrawler

__all__ = [
    'GLOBAL_SETTINGS',
    'DEFAULT_SITE_CONFIG',
    'SiteConfig',
    'WEBSITE_CONFIGS',
    'init_console',
    'banner',
    'p',
    'select_format',
    'HTMLParser',
    'save_txt',
    'save_epub',
    'generate_safe_filename',
    'find_existing_file',
    'parse_existing_chapters',
    'update_existing_file',
    'NovelCrawler',
]
