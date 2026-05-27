#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.8.1 — 全局设置与网站配置
"""

import sys
import os
import re
from dataclasses import dataclass, field
from typing import List, Set


# ═══════════════════════════════════════════════════════════════════
# 全局设置 (GLOBAL_SETTINGS)
# ═══════════════════════════════════════════════════════════════════
if getattr(sys, 'frozen', False):
    _OUTPUT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

GLOBAL_SETTINGS = {
    # 输出目录
    "output_dir": _OUTPUT_DIR,

    # 请求延迟（秒）
    "min_delay": 0.3,
    "max_delay": 1.5,

    # 请求重试
    "max_retries": 3,
    "timeout": 20,

    # 默认并发线程数
    "max_workers": 10,

    # HTTP 请求头
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    },
}


# ═══════════════════════════════════════════════════════════════════
# 网站配置 (SiteConfig + WEBSITE_CONFIGS)
# ═══════════════════════════════════════════════════════════════════
@dataclass
class SiteConfig:
    """单个网站的匹配规则配置

    封装了章节识别、正文提取、噪音过滤等所有与特定网站相关的规则。
    核心逻辑通过 self.config.xxx 引用这些规则，不包含任何硬编码。
    """
    # 章节 URL 特征
    chapter_patterns: List[re.Pattern] = field(default_factory=list)

    # 排除: 分类/标签/列表/目录页路径 — 这些不是章节
    category_path_patterns: List[re.Pattern] = field(default_factory=list)

    # 常见分类/标签文本 — 过滤掉这些短词
    category_tags: Set[str] = field(default_factory=set)

    # 广告/导航关键词 — 包含这些的行直接丢弃
    ad_keywords: List[str] = field(default_factory=list)

    # 已知噪音元素的 class/id — 正文提取时移除
    noise_classes: Set[str] = field(default_factory=set)
    noise_ids: Set[str] = field(default_factory=set)

    # 正文选择器（按优先级排序，包含反爬变体如 C0NTENT, c0ntent）
    content_selectors: List[str] = field(default_factory=list)

    # 小说标题选择器（从目录页提取）
    novel_title_selectors: List[str] = field(default_factory=list)

    # 章节标题选择器（从章节页提取）
    chapter_title_selectors: List[str] = field(default_factory=list)

    # 目录分页 URL 特征（正则）
    toc_page_patterns: List[re.Pattern] = field(default_factory=list)

    # 目录 JS 分页跳转正则
    toc_js_jump_pattern: str = ""

    # JS 章节链接解析正则
    js_chapter_pattern: str = ""


# ── 通用默认网站配置 ───────────────────────────────────────────
DEFAULT_SITE_CONFIG = SiteConfig(
    chapter_patterns=[
        re.compile(r'/\d{3,}\.html?$', re.I),
        re.compile(r'/\d{3,}/\d{3,}\.html?$', re.I),
        re.compile(r'/chapter/\d+', re.I),
        re.compile(r'/read/\d+', re.I),
        re.compile(r'/book/\d+/\d+', re.I),
        # 哈希ID章节: /book/52085/b19fa610e8c3b.html (爱丽丝书屋等)
        re.compile(r'/book/\d+/[a-f0-9]+\.html?$', re.I),
        re.compile(r'/novel/\d+/\d+', re.I),
        re.compile(r'/txt/\d+/\d+', re.I),
        re.compile(r'chapter[_-]?\d+', re.I),
        re.compile(r'/p/\d+', re.I),
        re.compile(r'/show/\d+', re.I),
        re.compile(r'/view/\d+', re.I),
        # 字母编码章节: /read_o8tr/sjtdp.html, /read_xxx/abcd.html
        re.compile(r'/read_[a-z0-9]+/[a-z0-9]+\.html?$', re.I),
    ],

    category_path_patterns=[
        re.compile(r'/category/', re.I),
        re.compile(r'/tag/', re.I),
        re.compile(r'/tags/', re.I),
        re.compile(r'/list/', re.I),
        re.compile(r'/sort/', re.I),
        re.compile(r'/class/', re.I),
        re.compile(r'/fenlei/', re.I),
        re.compile(r'/zuopin/', re.I),
        re.compile(r'/author/', re.I),
        re.compile(r'/search', re.I),
        re.compile(r'/top/', re.I),
        re.compile(r'/rank/', re.I),
        re.compile(r'/hot/', re.I),
        re.compile(r'/catalog/', re.I),    # 目录页
        re.compile(r'/toc/', re.I),        # 目录页
        re.compile(r'/index\.htm', re.I),  # 首页/目录
        re.compile(r'/index\.html', re.I),
        re.compile(r'/novel/', re.I),       # 小说主页 /novel/50851.html
        re.compile(r'/other/', re.I),       # 分类页 /other/chapters/...
    ],

    category_tags={
        '科幻', '玄幻', '奇幻', '仙侠', '武侠', '都市', '言情', '历史', '军事',
        '游戏', '竞技', '体育', '灵异', '悬疑', '推理', '恐怖', '末世', '修真',
        '重生', '穿越', '系统', '网游', '二次元', '同人', '女频', '男频',
        '连载', '完结', '全本', '完本', 'VIP', '精品', '热门', '推荐',
        '最新', '排行', '热榜', '排行', '书架', '收藏', '订阅',
        '首页', '书库', '分类', '排行', '搜索', '登录', '注册',
        '科幻小说', '玄幻小说', '仙侠小说', '都市小说', '历史小说',
        '言情小说', '武侠小说', '军事小说', '游戏小说', '悬疑小说',
        '上一页', '下一页', '上一章', '下一章', '返回目录', '加入书签',
        '最新章节', '天才一秒', '请记住', '手机阅读', '百度搜索',
        '本章未完', '点击下一页', '返回书页', '加入书架', '投推荐票',
        '章节错误', '举报', '投诉', '下载APP', '客户端',
    },

    ad_keywords=[
        '百度搜索', '手机阅读', '加入书签', '返回书签',
        '最新章节', '天才一秒', '请记住', '本章未完',
        '点击下一页', '返回书页', '加入书架', '投推荐票',
        '章节错误', '举报', '投诉', '下载APP', '客户端',
        'www.', 'http://', 'https://', '.com', '.cn', '.net', '.org',
        '笔趣阁', '起点', '纵横', '晋江', '红袖',
        '百度', '谷歌', '搜狗', '搜索',
        '广告', '推广', '合作', '商务',
        '手机站', '电脑版', '触屏版',
        '缓存', '书签', '签到', '打卡',
        '微信', 'QQ', '客服',
        '免责声明', '隐私政策', '用户协议',
        '本章报错', '纠错', '报错',
        '下一页继续阅读', '点击继续阅读', '继续阅读',
        '下一页', '下章', '下一节', '下一回',
    ],

    noise_classes={
        'con_top', 'bookname', 'bottem2', 'bottem', 'bottom',
        'hot_tui', 'hot', 'recommend', 'tuijian', 'listtj',
        'footer', 'header', 'nav', 'banner', 'dahengfu',
        'bookinfo', 'book_intro', 'crumb', 'breadcrumb',
        'ad', 'ads', 'advert', 'gg', 'guanggao',
    },

    noise_ids={
        'hm_t_20123', 'ad', 'ads', 'advert', 'gg',
    },

    content_selectors=[
        '#content', '#chaptercontent', '#booktext',
        '#readcontent', '#TextContent', '#htmlContent',
        '#chapter_content', '#chaptercontent',
        '.content', '.chapter-content', '.book-content',
        '.read-content', '.novel-content', '.article-content',
        '.chapter_content', '.booktxt',
        '#texts', '#booktxt', '#BookText', '#BookText_c0',
        '#C0NTENT',  # 反爬: 用数字0替代字母O
        '//div[contains(@id,"content")]',
        '//div[contains(@class,"content")]',
        '//div[contains(@id,"chapter")]',
        '//div[contains(@class,"chapter")]',
        '//div[contains(@id,"read")]',
        '//div[contains(@id,"C0NTENT")]',  # 反爬变体
        '//div[contains(@class,"C0NTENT")]',
        '//article',
    ],

    novel_title_selectors=[
        '//h1/text()',
        '//div[@class="bookname"]/h1/text()',
        '//div[contains(@class,"bookinfo")]//h1/text()',
        '//div[@id="bookinfo"]//h1/text()',
        '//h1[@class="book-title"]/text()',
        '//div[contains(@class,"title")]/h1/text()',
        '//title/text()',
    ],

    chapter_title_selectors=[
        '//h1/text()',
        '//*[@class="content"]/h1/text()',
        '//*[contains(@class,"chapter")]/h1/text()',
        '//*[contains(@id,"chapter")]/text()',
        '//h1/text()',
    ],

    toc_page_patterns=[
        re.compile(r'/catalog/d_\d+\.html', re.I),
        re.compile(r'/catalog/\d+\.html', re.I),
    ],

    toc_js_jump_pattern=r"(?:bookjump|chapterjump|runbookjump)\s*\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]",

    js_chapter_pattern=r"(?:gobook|readbook|goRead|gotochapter|runbook)\s*\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]",
)

# ── 所有网站配置集中管理 ───────────────────────────────────────
WEBSITE_CONFIGS = {
    "default": DEFAULT_SITE_CONFIG,
}
