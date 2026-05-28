#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.7 — 通用中文小说下载器
支持大部分小说网站，自动识别章节目录和正文，导出为 TXT 文件。

v3.0: 链接密度过滤、扩展广告关键词、排除分类路径
v3.1: 排除 catalog/toc/index 路径误判、解析 javascript: 链接、
      支持 C0NTENT 等反爬 class 名
v3.2: 简化排序逻辑 — 识别"最新章节"/"全部章节"分类，通过 URL 判断正序/倒序，
      不再按章节号重排（避免子小节被打乱）
v3.3: 跟随章节内"下一页"子页 — 一章分多页时自动拼接完整内容
v3.4: 多线程并发爬取 — 10 线程同时下载，自动按序拼接
v3.5: 自定义线程数 + 免责声明
v3.6: CrawlerConfig 类收敛全局变量，逻辑与配置初步分离
v3.7: 配置与逻辑彻底分离 — SiteConfig 数据类 + WEBSITE_CONFIGS 字典
      + GLOBAL_SETTINGS 字典；所有硬编码规则集中在文件顶部；
      NovelCrawler 通过 self.config / self.settings 引用一切配置

双击运行 EXE → 粘贴目录页 URL → 自动爬取全部章节 → 导出 TXT

依赖: requests + lxml (轻量)
"""

import sys
import os
import re
import time
import random
import signal
import threading
import traceback
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Pattern, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

try:
    import requests
    from lxml import html as lxml_html
except ImportError as e:
    print(f"[错误] 缺少依赖: {e}")
    print("请运行: pip install requests lxml")
    input("按回车键退出...")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════
# ════════════════════  配置区域（全部集中在此）  ════════════════════
# ═══════════════════════════════════════════════════════════════════

# ── 计算输出目录 ──────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    _OUTPUT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════════════════════
# 全局设置字典 — 存放所有非网站特定的运行参数
# ═══════════════════════════════════════════════════════════════
GLOBAL_SETTINGS: Dict = {
    # ── 输出 ──
    "output_dir": _OUTPUT_DIR,

    # ── 请求头 ──
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    },

    # ── 请求延迟与重试 ──
    "min_delay": 0.3,
    "max_delay": 1.5,
    "max_retries": 3,
    "timeout": 20,
    "retry_delay_min": 2,
    "retry_delay_max": 4,
    "toc_page_delay_min": 0.3,
    "toc_page_delay_max": 0.8,

    # ── 并发 ──
    "max_workers": 10,
    "max_workers_limit": 50,

    # ── 目录检测 ──
    "toc_sibling_search_range": 10,
    "toc_container_search_depth": 5,
    "toc_min_chapters_for_order": 3,
    "toc_desc_ratio_threshold": 0.7,
    "toc_asc_ratio_threshold": 0.3,

    # ── 正文选择器探测 ──
    "content_selector_score_threshold": 100,

    # ── 正文提取阈值 ──
    "content_min_text_len": 100,
    "content_heuristic_min_len": 200,
    "content_fallback_min_len": 50,
    "content_para_min_len": 5,
    "content_part_min_len": 1,
    "link_density_max": 0.3,
    "link_density_para_max": 0.5,

    # ── 标题长度约束 ──
    "title_min_len": 2,
    "title_max_len": 100,
    "chapter_title_min_len": 2,
    "chapter_title_max_len": 80,

    # ── 子页爬取 ──
    "sub_page_max": 50,

    # ── 进度条 ──
    "progress_bar_width": 30,

    # ── 文件名 ──
    "filename_max_len": 80,
    "default_novel_name": "小说",

    # ── 编码检测 ──
    "encoding_detect_bytes": 2000,
    "encoding_fallbacks": ('iso-8859-1', 'latin-1'),
    "default_encoding": 'utf-8',
}


# ═══════════════════════════════════════════════════════════════
# 网站配置数据类 — 封装单个网站的所有匹配规则
# ═══════════════════════════════════════════════════════════════
@dataclass
class SiteConfig:
    """单个网站的爬取配置 — 所有匹配规则、选择器、关键词集中于此"""

    # ── 标识 ──
    name: str = ""

    # ── 章节 URL 特征 — 用于识别哪些链接是章节 ──
    chapter_patterns: List[Pattern] = field(default_factory=list)

    # ── 排除: 分类/标签/列表/目录页路径 — 这些不是章节 ──
    category_path_patterns: List[Pattern] = field(default_factory=list)

    # ── 常见分类/标签文本 — 过滤掉这些短词 ──
    category_tags: Set[str] = field(default_factory=set)

    # ── 广告/导航关键词 — 包含这些的行直接丢弃 ──
    ad_keywords: List[str] = field(default_factory=list)

    # ── 已知噪音元素的 class/id — 正文提取时移除 ──
    noise_classes: Set[str] = field(default_factory=set)
    noise_ids: Set[str] = field(default_factory=set)

    # ── 正文选择器探测列表 (包含反爬变体如 C0NTENT) ──
    content_selectors: List[str] = field(default_factory=list)

    # ── 小说标题选择器 ──
    novel_title_selectors: List[str] = field(default_factory=list)

    # ── 章节标题选择器 ──
    chapter_title_selectors: List[str] = field(default_factory=list)

    # ── "下一页"链接文本 — 用于跟踪章节内分页 ──
    next_page_texts: List[str] = field(default_factory=list)

    # ── URL 跳过扩展名 — 这些后缀的链接不视为章节 ──
    url_skip_extensions: Tuple[str, ...] = ()

    # ── 目录/索引关键词 — URL 路径段中这些词表示非章节页 ──
    catalog_toc_index_words: Set[str] = field(default_factory=set)

    # ── 目录分页 URL 正则 — 用于检测多页目录 ──
    catalog_page_patterns: List[Pattern] = field(default_factory=list)

    # ── "最新/全部章节"检测用的 HTML 标签 ──
    toc_section_search_tags: List[str] = field(default_factory=list)

    # ── "最新/全部章节"检测用的关键词 ──
    toc_section_keywords: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 通用默认配置 — 适用于大多数未知网站
# ═══════════════════════════════════════════════════════════════
UNIVERSAL_CONFIG = SiteConfig(
    name="通用",

    # 章节 URL 特征
    chapter_patterns=[
        re.compile(r'/\d{3,}\.htm', re.I),
        re.compile(r'/\d{3,}/\d{3,}\.htm', re.I),
        re.compile(r'/chapter/\d+', re.I),
        re.compile(r'/read/\d+', re.I),
        re.compile(r'/book/\d+/\d+', re.I),
        re.compile(r'/novel/\d+/\d+', re.I),
        re.compile(r'/txt/\d+/\d+', re.I),
        re.compile(r'chapter[_-]?\d+', re.I),
        re.compile(r'/p/\d+', re.I),
        re.compile(r'/show/\d+', re.I),
        re.compile(r'/view/\d+', re.I),
        # 字母编码章节: /read_o8tr/sjtdp.html, /read_xxx/abcd.html
        re.compile(r'/read_[a-z0-9]+/[a-z0-9]+\.html?$', re.I),
    ],

    # 排除: 分类/标签/列表/目录页路径 — 这些不是章节
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
    ],

    # 常见分类/标签文本 — 过滤掉这些短词
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

    # 广告/导航关键词 — 包含这些的行直接丢弃
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

    # 已知噪音元素的 class/id — 正文提取时移除
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

    # 常见选择器 (包含反爬变体如 C0NTENT, c0ntent)
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

    # 小说标题选择器
    novel_title_selectors=[
        '//h1/text()',
        '//div[@class="bookname"]/h1/text()',
        '//div[contains(@class,"bookinfo")]//h1/text()',
        '//div[@id="bookinfo"]//h1/text()',
        '//h1[@class="book-title"]/text()',
        '//div[contains(@class,"title")]/h1/text()',
        '//title/text()',
    ],

    # 章节标题选择器
    chapter_title_selectors=[
        '//h1/text()',
        '//*[@class="content"]/h1/text()',
        '//*[contains(@class,"chapter")]/h1/text()',
        '//*[contains(@id,"chapter")]/text()',
        '//h1/text()',
    ],

    # "下一页"文本
    next_page_texts=['下一页', '下一頁', '下页', 'next', 'Next', 'NEXT'],

    # URL 跳过扩展名
    url_skip_extensions=('.css', '.js', '.png', '.jpg', '.gif', '.ico',
                         '.mp3', '.mp4', '.zip', '.rar', '.txt', '.pdf'),

    # 目录/索引关键词 — URL 路径段排除用
    catalog_toc_index_words={'catalog', 'toc', 'index', 'list', 'tag', 'tags',
                             'category', 'sort', 'class', 'search', 'page',
                             'author', 'top', 'rank', 'hot'},

    # 目录分页 URL 正则
    catalog_page_patterns=[
        # 匹配 catalog/d_N.html 模式
        re.compile(r'/catalog/d_\d+\.html', re.I),
        # 匹配 catalog/N.html 模式
        re.compile(r'/catalog/\d+\.html', re.I),
    ],

    # "最新/全部章节"检测用的搜索标签 — 只在标题类元素中搜索
    toc_section_search_tags=['dt', 'h1', 'h2', 'h3', 'h4', 'p', 'span'],

    # "最新/全部章节"检测用的关键词
    toc_section_keywords=['最新章节', '全部章节', '章节列表'],
)

# ═══════════════════════════════════════════════════════════════
# 网站配置字典 — 集中管理所有网站
# ═══════════════════════════════════════════════════════════════
WEBSITE_CONFIGS: Dict[str, SiteConfig] = {
    "universal": UNIVERSAL_CONFIG,
}


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════  控制台工具（独立区域）  ══════════════════════
# ═══════════════════════════════════════════════════════════════════
def init_console():
    if sys.platform == 'win32':
        os.system('chcp 65001 >nul 2>&1')
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            pass

C = {
    "g": "\033[92m", "y": "\033[93m", "r": "\033[91m",
    "c": "\033[96m", "m": "\033[95m", "b": "\033[1m",
    "d": "\033[2m", "R": "\033[0m",
}

def p(text, color="", end="\n"):
    prefix = C.get(color, "")
    suffix = C["R"] if prefix else ""
    print(f"{prefix}{text}{suffix}", end=end, flush=True)

def banner():
    p("═" * 56, "c")
    p("  📚 万能小说爬虫 v3.7", "b")
    p("  自动识别目录 | 智能提取正文 | 导出 TXT", "d")
    p("  ⚠ 本程序仅供学习交流使用，请勿用于非法用途", "y")
    p("═" * 56, "c")
    print()


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════  核心爬虫逻辑（零硬编码规则）  ════════════════
# ═══════════════════════════════════════════════════════════════════
class NovelCrawler:
    """通用小说爬虫 — 所有网站规则通过 self.config 引用，全局设置通过 self.settings 引用"""

    def __init__(self, config: Optional[SiteConfig] = None):
        self.config = config if config is not None else UNIVERSAL_CONFIG
        self.settings = GLOBAL_SETTINGS

        self.session = requests.Session()
        self.session.headers.update(self.settings["headers"])
        self.base_url = ""
        self.domain = ""
        self.chapters = []      # [(title, url)]
        self.contents = []      # [(title, [line, ...])]
        self.failed = []
        self.stop = False
        self.js_book_id = ""    # 从 javascript: 链接中提取的 book_id
        self.novel_title = ""   # 小说标题
        self.lock = threading.Lock()
        self.progress_done = 0  # 已完成的章节数

    # ── 请求 ──────────────────────────────────────────────────
    def fetch(self, url, retries=None):
        if retries is None:
            retries = self.settings["max_retries"]
        timeout = self.settings["timeout"]
        for i in range(retries):
            try:
                r = self.session.get(url, timeout=timeout, allow_redirects=True)
                if r.status_code == 200:
                    # 检测真实编码: HTTP 头的 ISO-8859-1 通常是默认值，不可靠
                    encoding = r.encoding
                    if encoding and encoding.lower() in self.settings["encoding_fallbacks"]:
                        # 从 HTML <meta charset> 或 XML 声明检测
                        raw = r.content[:self.settings["encoding_detect_bytes"]]
                        m = re.search(rb'charset[="\s]+([a-zA-Z0-9_-]+)', raw)
                        if m:
                            encoding = m.group(1).decode('ascii')
                        else:
                            encoding = self.settings["default_encoding"]
                    r.encoding = encoding or self.settings["default_encoding"]
                    return r
                p(f"  ⚠ HTTP {r.status_code}: {url}", "y")
            except Exception as e:
                p(f"  ⚠ 请求失败 ({i+1}/{retries}): {e}", "y")
                if i < retries - 1:
                    time.sleep(random.uniform(
                        self.settings["retry_delay_min"],
                        self.settings["retry_delay_max"]))
        return None

    # ── 解析 HTML ─────────────────────────────────────────────
    def parse(self, resp):
        encoding = resp.encoding or self.settings["default_encoding"]
        parser = lxml_html.HTMLParser(encoding=encoding, recover=True)
        return lxml_html.fromstring(resp.content, parser=parser)

    # ── 解析 javascript: 链接 ─────────────────────────────────
    def _parse_js_link(self, href):
        """解析各种 JS 章节链接，返回构造的 URL 路径
        匹配: gobook, readbook, goRead, gotochapter, runbook 等
        格式: func('bookid','chapterid')"""
        m = re.search(
            r"(?:gobook|readbook|goRead|gotochapter|runbook)\s*\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]",
            href, re.I
        )
        if m:
            book_id = m.group(1)
            chapter_id = m.group(2)
            if not self.js_book_id:
                self.js_book_id = book_id
            return f"/book/{book_id}/{chapter_id}.html"
        # 翻页: bookjump('bookid','page') — 不是章节
        return None

    # ── URL 判断 ──────────────────────────────────────────────
    def is_chapter_url(self, url):
        parsed = urlparse(url)
        skip = self.config.url_skip_extensions
        if parsed.path.lower().endswith(skip):
            return False
        if parsed.netloc and parsed.netloc != self.domain:
            return False

        # 排除分类/标签/列表/目录页 — 这些不是章节
        for pat in self.config.category_path_patterns:
            if pat.search(parsed.path):
                return False

        for pat in self.config.chapter_patterns:
            if pat.search(parsed.path):
                return True

        # 通用回退: 路径最后段是纯数字
        parts = [p for p in parsed.path.split('/') if p]
        # 至少需要3段 (如 /book/200806/13457451.html)，排除 /book/200806/ 这种主页
        if len(parts) >= 3:
            last = parts[-1]
            name, ext = os.path.splitext(last)
            # 必须是 "数字.html" 格式
            if name.isdigit() and ext.lower() in ('.html', '.htm', ''):
                # 额外检查: 前一段不能是 catalog/toc/index 等
                prev = parts[-2].lower() if len(parts) >= 2 else ''
                if prev in self.config.catalog_toc_index_words:
                    return False
                return True
        return False

    # ── 检测目录分页 ──────────────────────────────────────────
    def _collect_toc_pages(self, doc, base_url):
        """检测目录分页链接，返回所有分页URL列表（含当前页）"""
        pages = [base_url]
        seen = {base_url}

        # 查找分页链接: catalog/d_1.html, catalog/d_2.html 等
        for a in doc.xpath('//a[@href]'):
            href = (a.get('href') or '').strip()
            if not href:
                continue
            full = urljoin(base_url, href)
            parsed = urlparse(full)
            # 匹配 catalog/d_N.html 或 catalog/N.html 模式
            matched = False
            for pat in self.config.catalog_page_patterns:
                if pat.search(parsed.path):
                    matched = True
                    break
            if matched:
                if full not in seen:
                    seen.add(full)
                    pages.append(full)
            # 也处理 javascript: 翻页链接
            elif href.startswith('javascript:'):
                m = re.search(r"(?:bookjump|chapterjump|runbookjump)\s*\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]", href, re.I)
                if m:
                    book_id = m.group(1)
                    page_num = m.group(2)
                    # 直接构造完整 URL，不用 urljoin（javascript: 前缀会干扰）
                    parsed_base = urlparse(base_url)
                    page_url = f"{parsed_base.scheme}://{parsed_base.netloc}/book/{book_id}/catalog/d_{page_num}.html"
                    if page_url not in seen:
                        seen.add(page_url)
                        pages.append(page_url)

        return pages

    # ── 检测"最新章节"和"全部章节"分类 ───────────────────────
    def _detect_toc_sections(self, doc):
        """检测页面中是否有"最新章节"和"全部章节"分类区域。
        返回: {
            'has_latest': bool,
            'has_all': bool,
            'all_container': element or None,   # "全部章节"标题后面的 ul/dl/ol
            'latest_container': element or None, # "最新章节"标题后面的 ul/dl/ol
            'all_links': list or None,  # 直接提供的链接列表 (dl/dt/dd 结构)
        }"""
        result = {'has_latest': False, 'has_all': False,
                  'all_container': None, 'latest_container': None,
                  'all_links': None}

        search_tags = self.config.toc_section_search_tags
        keywords = self.config.toc_section_keywords
        search_range = self.settings["toc_sibling_search_range"]
        title_max = self.settings["title_max_len"]

        # 只在标题类元素中搜索，避免匹配 <title>/<script>/<meta> 等
        for tag in search_tags:
            for keyword in keywords:
                xpath = f'//{tag}[contains(text(),"{keyword}")]'
                try:
                    for elem in doc.xpath(xpath):
                        text = (elem.text or '').strip()
                        if not text:
                            text = (elem.text_content() or '').strip()

                        if '最新章节' in text:
                            result['has_latest'] = True
                            # 找后面的 ul/dl/ol 兄弟
                            nxt = elem
                            for _ in range(search_range):
                                nxt = nxt.getnext()
                                if nxt is None:
                                    break
                                if nxt.tag in ('ul', 'dl', 'ol'):
                                    result['latest_container'] = nxt
                                    break

                            # dl/dt/dd 结构: "最新章节"在 <dt> 中，
                            # 下一个 <dt> 就是"正文"区域
                            if elem.tag == 'dt':
                                dt_next = elem.getnext()
                                while dt_next is not None and dt_next.tag != 'dt':
                                    dt_next = dt_next.getnext()
                                if dt_next is not None:
                                    result['has_all'] = True
                                    # 收集该 <dt> 后面的所有 <dd> 链接
                                    links = []
                                    dd = dt_next.getnext()
                                    while dd is not None:
                                        if dd.tag == 'dt':
                                            break
                                        if dd.tag == 'dd':
                                            for a in dd.xpath('.//a[@href]'):
                                                href = (a.get('href') or '').strip()
                                                title = (a.text_content() or '').strip()
                                                if href and title and len(title) <= title_max:
                                                    links.append((title, href))
                                        dd = dd.getnext()
                                    if links:
                                        result['all_links'] = links

                        if '全部章节' in text or '章节列表' in text:
                            result['has_all'] = True
                            nxt = elem
                            for _ in range(search_range):
                                nxt = nxt.getnext()
                                if nxt is None:
                                    break
                                if nxt.tag in ('ul', 'dl', 'ol'):
                                    result['all_container'] = nxt
                                    break
                except Exception:
                    continue

        return result

    # ── 检测目录 ──────────────────────────────────────────────
    def detect_toc(self, doc):
        p("[1/3] 分析页面结构，识别章节目录...", "c")

        # 检测"最新章节"/"全部章节"分类
        section_info = self._detect_toc_sections(doc)
        if section_info['has_latest'] and section_info['has_all']:
            p("  📑 检测到分类: 最新章节 + 全部章节", "g")
            p('  → 优先使用"全部章节"区域（完整列表）', "d")
        elif section_info['has_latest']:
            p("  📑 检测到: 最新章节（可能只有部分章节）", "y")
        elif section_info['has_all']:
            p("  📑 检测到: 全部章节", "g")

        # 检测分页，收集所有目录页
        toc_pages = self._collect_toc_pages(doc, self.base_url)
        if len(toc_pages) > 1:
            p(f"  📄 检测到 {len(toc_pages)} 页目录，正在加载...", "y")
            all_docs = [doc]
            toc_d_min = self.settings["toc_page_delay_min"]
            toc_d_max = self.settings["toc_page_delay_max"]
            for page_url in toc_pages[1:]:
                p(f"  📄 加载: {page_url}", "d")
                resp = self.fetch(page_url)
                if resp:
                    all_docs.append(self.parse(resp))
                time.sleep(random.uniform(toc_d_min, toc_d_max))
        else:
            all_docs = [doc]

        # 从所有页面收集章节链接
        all_links = []
        for d in all_docs:
            all_links.extend(d.xpath('//a[@href]'))

        if not all_links:
            p("  ✗ 页面中没有链接", "r")
            return []

        # 按父容器分组，找章节链接最多的容器
        containers = {}
        title_max = self.settings["title_max_len"]
        container_depth = self.settings["toc_container_search_depth"]

        for a in all_links:
            href = (a.get('href') or '').strip()
            title = (a.text_content() or '').strip()
            if not href or not title or len(title) > title_max:
                continue

            # 处理 javascript: 链接
            if href.startswith('javascript:'):
                js_path = self._parse_js_link(href)
                if js_path:
                    full_url = urljoin(self.base_url, js_path)
                else:
                    continue  # 无法解析的 JS 链接跳过
            else:
                full_url = urljoin(self.base_url, href)

            if not self.is_chapter_url(full_url):
                continue

            # 找父容器
            parent = a.getparent()
            container_key = None
            for _ in range(container_depth):
                if parent is None:
                    break
                tag = parent.tag
                pid = parent.get('id', '')
                pcl = ' '.join(parent.get('class', []))
                if tag in ('ul', 'div', 'dl', 'ol', 'nav', 'section'):
                    container_key = f"{tag}#{pid}.{pcl}"
                    break
                parent = parent.getparent()

            if not container_key:
                container_key = "__fallback__"

            if container_key not in containers:
                containers[container_key] = []
            containers[container_key].append((title, full_url))

        if not containers:
            return []

        order_threshold = self.settings["toc_min_chapters_for_order"]

        # ── 如果检测到"全部章节"链接列表（dl/dt/dd 结构）───
        if section_info['all_links']:
            unique = []
            seen = set()
            for title, href in section_info['all_links']:
                full_url = urljoin(self.base_url, href)
                if not self.is_chapter_url(full_url):
                    continue
                if full_url not in seen:
                    seen.add(full_url)
                    unique.append((title, full_url))
            if unique:
                p(f"  ✓ 使用\"正文\"区域: {len(unique)} 个章节", "g")
                if len(unique) >= order_threshold:
                    unique = self._fix_chapter_order(unique)
                return unique

        # ── 如果找到"全部章节"容器，直接从该元素收集链接 ────
        if section_info['all_container'] is not None:
            all_ul = section_info['all_container']
            unique = []
            seen = set()
            for a in all_ul.xpath('.//a[@href]'):
                href = (a.get('href') or '').strip()
                title = (a.text_content() or '').strip()
                if not href or not title or len(title) > title_max:
                    continue
                if href.startswith('javascript:'):
                    js_path = self._parse_js_link(href)
                    if js_path:
                        full_url = urljoin(self.base_url, js_path)
                    else:
                        continue
                else:
                    full_url = urljoin(self.base_url, href)
                if not self.is_chapter_url(full_url):
                    continue
                if full_url not in seen:
                    seen.add(full_url)
                    unique.append((title, full_url))

            if unique:
                p(f"  ✓ 使用\"全部章节\"区域: {len(unique)} 个章节", "g")
                if len(unique) >= order_threshold:
                    unique = self._fix_chapter_order(unique)
                return unique

        # ── 通用流程: 按父容器分组 ────────────────────────────
        best = max(containers, key=lambda k: len(containers[k]))

        seen = set()
        unique = []

        # 先加最佳容器的（保证主列表顺序）
        for t, u in containers[best]:
            if u not in seen:
                seen.add(u)
                unique.append((t, u))

        # 再加其他容器的（不漏掉 JS 链接等分散的章节）
        for key, links in containers.items():
            if key == best:
                continue
            for t, u in links:
                if u not in seen:
                    seen.add(u)
                    unique.append((t, u))

        p(f"  ✓ 主目录容器: {best}", "g")
        p(f"  ✓ 识别到 {len(unique)} 个章节", "g")

        # ── 检测正序/倒序 ────────────────────────────────────
        if len(unique) >= order_threshold:
            unique = self._fix_chapter_order(unique)

        return unique

    # ── 章节排序修正 (v3.2: 仅判断正序/倒序，不按章节号重排) ──
    def _fix_chapter_order(self, chapters):
        """检测目录是正序还是倒序，倒序则翻转。
        不再按章节号排序，避免子小节被打乱。"""
        desc_threshold = self.settings["toc_desc_ratio_threshold"]
        asc_threshold = self.settings["toc_asc_ratio_threshold"]

        # 策略1: 提取 URL 路径中的数字，判断趋势
        url_nums = []
        for _, url in chapters:
            parsed = urlparse(url)
            parts = [p for p in parsed.path.split('/') if p]
            # 取最后一段数字
            if parts:
                last = parts[-1]
                name, _ = os.path.splitext(last)
                if name.isdigit():
                    url_nums.append(int(name))
                else:
                    url_nums.append(None)
            else:
                url_nums.append(None)

        # 取有效数字对，计算趋势
        valid = [(i, n) for i, n in enumerate(url_nums) if n is not None]
        if len(valid) >= 3:
            # 比较前后差值: 正序 → 差值>0, 倒序 → 差值<0
            asc_count = 0
            desc_count = 0
            for i in range(len(valid) - 1):
                diff = valid[i+1][1] - valid[i][1]
                if diff > 0:
                    asc_count += 1
                elif diff < 0:
                    desc_count += 1

            total_pairs = asc_count + desc_count
            if total_pairs > 0:
                desc_ratio = desc_count / total_pairs
                if desc_ratio > desc_threshold:
                    p("  ↻ 检测到目录倒序（URL数字递减），已翻转为正序", "y")
                    return list(reversed(chapters))
                elif desc_ratio < asc_threshold:
                    # 正序，不需要处理
                    return chapters

        # 策略2: URL数字无法判断时，检查标题中的数字趋势
        # 提取标题开头的数字（如 "1." "2." 或 "第1章" "第2章"）
        title_nums = []
        for title, _ in chapters:
            n = None
            # "第N章/回/节" 模式
            m = re.search(r'^第(\d+)[章回节]', title)
            if m:
                n = int(m.group(1))
            else:
                # 开头数字: "1." "2、" "001 " 等
                m = re.match(r'^(\d+)[.、\s]', title)
                if m:
                    n = int(m.group(1))
            title_nums.append(n)

        valid_t = [(i, n) for i, n in enumerate(title_nums) if n is not None]
        if len(valid_t) >= 3:
            asc_count = 0
            desc_count = 0
            for i in range(len(valid_t) - 1):
                diff = valid_t[i+1][1] - valid_t[i][1]
                if diff > 0:
                    asc_count += 1
                elif diff < 0:
                    desc_count += 1

            total_pairs = asc_count + desc_count
            if total_pairs > 0:
                desc_ratio = desc_count / total_pairs
                if desc_ratio > desc_threshold:
                    p("  ↻ 检测到目录倒序（标题数字递减），已翻转为正序", "y")
                    return list(reversed(chapters))

        # 无法判断，保持原序
        return chapters

    # ── 链接密度计算 ──────────────────────────────────────────
    def _link_density(self, elem):
        """计算元素中文本在 <a> 标签中的比例。
        侧边栏/推荐列表/导航: 链接密度 0.3~1.0
        正文段落: 链接密度通常 < 0.1"""
        total_text = ''
        link_text = ''
        for t in elem.itertext():
            total_text += t
        for a in elem.xpath('.//a'):
            for t in a.itertext():
                link_text += t
        total_len = len(total_text.strip())
        if total_len == 0:
            return 1.0  # 空元素视为无效
        return len(link_text.strip()) / total_len

    # ── 检测正文选择器 ────────────────────────────────────────
    def detect_content_selector(self, sample_url):
        p("  [探测] 分析正文选择器...", "d")
        resp = self.fetch(sample_url)
        if not resp:
            return None

        doc = self.parse(resp)

        selectors = self.config.content_selectors
        content_min = self.settings["content_min_text_len"]
        density_max = self.settings["link_density_max"]
        score_threshold = self.settings["content_selector_score_threshold"]

        best = None
        best_score = 0

        for sel in selectors:
            try:
                if sel.startswith('//'):
                    elems = doc.xpath(sel)
                else:
                    elems = doc.cssselect(sel)
                for elem in elems:
                    text = self._clean_text(elem)
                    text_len = len(text)
                    if text_len < content_min:
                        continue

                    # 链接密度惩罚: 密度越高，score 越低
                    density = self._link_density(elem)
                    if density > density_max:
                        continue  # 导航/侧边栏，直接跳过

                    # 综合评分: 文本长度 × (1 - 密度)²
                    # 平方惩罚: density=0.16 → 系数0.71, density=0.3 → 系数0.49
                    score = text_len * (1.0 - density) ** 2
                    if score > best_score:
                        best_score = score
                        best = sel
            except Exception:
                continue

        if best and best_score > score_threshold:
            p(f"  ✓ 正文选择器: {best} (score={best_score:.0f})", "g")
        else:
            p("  ⚠ 通用模式：找最大文本块", "y")

        return best if best_score > score_threshold else None

    def _clean_text(self, elem):
        """清理元素文本 — 移除噪音子元素"""
        # 移除 script/style/广告容器
        for bad in elem.xpath('.//script|//style|//noscript|//ins'):
            bad.getparent().remove(bad)

        # 移除已知噪音元素 (class/id 匹配)
        noise_classes = self.config.noise_classes
        noise_ids = self.config.noise_ids
        for node in list(elem.iter()):
            if node is elem:
                continue
            cls = set(node.get('class', '').split()) if node.get('class') else set()
            nid = node.get('id', '') or ''
            if cls & noise_classes or nid in noise_ids:
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)

        part_min = self.settings["content_part_min_len"]
        parts = []
        for t in elem.itertext():
            t = t.strip()
            if t and len(t) > part_min:
                parts.append(t)
        return '\n'.join(parts)

    # ── 判断一行是否是广告/导航/标签 ──────────────────────────
    def _is_noise_line(self, line):
        """判断一行文本是否是噪音（广告、导航、标签等）"""
        stripped = line.strip()
        if not stripped:
            return True

        # 1. 匹配广告/导航关键词
        for kw in self.config.ad_keywords:
            if kw in stripped:
                return True

        # 2. 短行分类标签 (2~8字，且属于已知标签集)
        if 2 <= len(stripped) <= 8:
            if stripped in self.config.category_tags:
                return True

        # 3. 纯标点/符号行
        if re.match(r'^[\s\-=─\*＊·\.。,，、:：;；!！?？\[\]【】()（）《》<>＜＞「」『』""\'\'\"\']+$', stripped):
            return True

        # 4. 链接类行
        if stripped.startswith(('http://', 'https://', 'www.')):
            return True

        return False

    # ── 提取正文 ──────────────────────────────────────────────
    def extract_content(self, doc, selector=None):
        text = ""

        content_min = self.settings["content_min_text_len"]
        heuristic_min = self.settings["content_heuristic_min_len"]
        fallback_min = self.settings["content_fallback_min_len"]
        density_max = self.settings["link_density_max"]
        density_para_max = self.settings["link_density_para_max"]
        para_min_len = self.settings["content_para_min_len"]

        if selector:
            try:
                if selector.startswith('//'):
                    elems = doc.xpath(selector)
                else:
                    elems = doc.cssselect(selector)
                if elems:
                    text = self._clean_text(elems[0])
            except Exception:
                pass

        if not text or len(text) < content_min:
            # 启发式: 找最长文本的 div，但排除链接密度高的
            best_text = ""
            best_score = 0
            for div in doc.xpath('//div'):
                try:
                    t = self._clean_text(div)
                    t_len = len(t)
                    if t_len < heuristic_min:
                        continue

                    density = self._link_density(div)
                    if density > density_max:
                        continue  # 导航/侧边栏，跳过

                    score = t_len * (1.0 - density)
                    if score > best_score:
                        best_score = score
                        best_text = t
                except Exception:
                    continue
            if best_text:
                text = best_text

        if not text or len(text) < fallback_min:
            # 最后手段: 所有 p 标签，但排除高链接密度的
            paras = []
            for p_elem in doc.xpath('//p'):
                try:
                    density = self._link_density(p_elem)
                    if density > density_para_max:
                        continue
                    t = p_elem.text_content().strip()
                    if t and len(t) > para_min_len:
                        paras.append(t)
                except Exception:
                    continue
            text = '\n'.join(paras)

        # 清理: 逐行过滤噪音
        lines = []
        for line in text.split('\n'):
            line = line.strip()
            if not self._is_noise_line(line):
                lines.append(line)

        return lines

    # ── 提取小说标题（从目录页） ─────────────────────────────
    def extract_novel_title(self, doc):
        """从目录页提取小说标题"""
        t_min = self.settings["title_min_len"]
        t_max = self.settings["title_max_len"]
        for sel in self.config.novel_title_selectors:
            try:
                titles = doc.xpath(sel)
                for t in titles:
                    t = t.strip()
                    # 清理常见的后缀（如"最新章节"、"全文阅读"等）
                    t = re.sub(r'最新章节.*|全文阅读.*|全文.*|在线阅读.*', '', t)
                    t = re.sub(r'_(小说网|笔趣阁|阅读网|文学网|书屋)$', '', t)
                    t = re.sub(r'[\s\-_]+$', '', t)
                    if t and t_min < len(t) < t_max:
                        return t
            except Exception:
                continue
        return None

    # ── 提取章节标题 ──────────────────────────────────────────
    def extract_title(self, doc):
        ct_min = self.settings["chapter_title_min_len"]
        ct_max = self.settings["chapter_title_max_len"]
        for sel in self.config.chapter_title_selectors:
            try:
                titles = doc.xpath(sel)
                for t in titles:
                    t = t.strip()
                    if t and ct_min < len(t) < ct_max:
                        return t
            except Exception:
                continue
        return None

    # ── 查找章节内"下一页"链接 ────────────────────────────────
    def _find_next_page(self, doc, chapter_urls):
        """查找当前章节内的"下一页"链接。
        chapter_urls: 目录中所有章节的 URL 集合（用于判断是否跳到了下一章）
        返回: 下一页 URL 或 None"""
        next_texts = self.config.next_page_texts

        for a in doc.xpath('//a[@href]'):
            href = (a.get('href') or '').strip()
            if not href or href == '#' or href.startswith('javascript:void'):
                continue

            text = (a.text_content() or '').strip()
            title_attr = (a.get('title') or '').strip()

            is_next = False
            # 匹配链接文本
            for nt in next_texts:
                if nt in text or nt in title_attr:
                    is_next = True
                    break
            # 也匹配 class/id
            cls = (a.get('class') or '').lower()
            aid = (a.get('id') or '').lower()
            if 'next' in cls or 'nextpage' in cls or 'next' in aid:
                is_next = True

            if not is_next:
                continue

            # 构造完整 URL
            full_url = urljoin(self.base_url, href)

            # 关键判断: 如果这个 URL 是目录中的某个章节，说明跳到了下一章，不是子页
            if full_url in chapter_urls:
                return None

            # 额外判断: 同域名
            parsed = urlparse(full_url)
            if parsed.netloc and parsed.netloc != self.domain:
                continue

            return full_url

        return None

    # ── 单章节爬取（线程安全） ─────────────────────────────────
    def _fetch_single_chapter(self, index, title, url, chapter_url_set, selector, total):
        if self.stop:
            return (index, None, None)

        min_d = self.settings["min_delay"]
        max_d = self.settings["max_delay"]
        time.sleep(random.uniform(min_d, max_d))

        resp = self.fetch(url)
        if not resp:
            with self.lock:
                self.failed.append((title, url, "请求失败"))
                self.progress_done += 1
            self._print_progress(total)
            return (index, None, None)

        doc = self.parse(resp)
        page_title = self.extract_title(doc) or title
        lines = self.extract_content(doc, selector)

        if not lines:
            with self.lock:
                self.failed.append((title, url, "无法提取正文"))
                self.progress_done += 1
            self._print_progress(total)
            return (index, None, None)

        sub_pages = 0
        max_sub_pages = self.settings["sub_page_max"]
        next_url = self._find_next_page(doc, chapter_url_set)
        while next_url and sub_pages < max_sub_pages and not self.stop:
            time.sleep(random.uniform(min_d, max_d))
            sub_resp = self.fetch(next_url)
            if not sub_resp:
                break
            sub_doc = self.parse(sub_resp)
            sub_lines = self.extract_content(sub_doc, selector)
            if sub_lines:
                lines.extend(sub_lines)
                sub_pages += 1
            else:
                break
            next_url = self._find_next_page(sub_doc, chapter_url_set)

        with self.lock:
            self.progress_done += 1

        self._print_progress(total)
        return (index, page_title, lines)

    def _print_progress(self, total):
        with self.lock:
            done = self.progress_done
        pct = done / total * 100
        bar_w = self.settings["progress_bar_width"]
        filled = int(bar_w * done / total)
        bar = '█' * filled + '░' * (bar_w - filled)
        p(f"\r  [{bar}] {pct:.0f}% ({done}/{total}) {self._failed_count_locked()} 失败", "c", end="")

    def _failed_count_locked(self):
        with self.lock:
            return len(self.failed)

    # ── 主流程 ────────────────────────────────────────────────
    def run(self):
        init_console()
        banner()

        # 获取 URL
        p("📖 请输入小说目录页 URL:", "b")
        p("   (大部分小说网站的目录/索引页均可)", "d")
        url = input("   URL ▶ ").strip()
        if not url:
            p("[错误] URL 不能为空", "r")
            input("按回车键退出...")
            return

        if not url.startswith('http'):
            url = 'https://' + url

        self.base_url = url
        self.domain = urlparse(url).netloc

        print()
        p(f"🌐 目标: {self.domain}", "c")
        p(f"🔗 地址: {url}", "d")
        print()

        # 获取目录页
        p("[连接] 获取目录页面...", "y")
        resp = self.fetch(url)
        if not resp:
            p("[错误] 无法访问目标页面", "r")
            input("\n按回车键退出...")
            return

        p(f"  ✓ 页面获取成功 (HTTP {resp.status_code})", "g")
        doc = self.parse(resp)

        # 提取小说标题
        self.novel_title = self.extract_novel_title(doc)
        if self.novel_title:
            p(f"  📚 小说标题: {self.novel_title}", "g")

        # 检测目录
        chapters = self.detect_toc(doc)
        if not chapters:
            print()
            p("[失败] 无法识别章节目录", "r")
            p("  可能原因:", "d")
            p("  1. URL 不是目录页（请粘贴目录页链接）", "d")
            p("  2. 网站需要 JavaScript 渲染", "d")
            p("  3. 网站结构非常特殊", "d")
            input("\n按回车键退出...")
            return

        # 显示信息
        print()
        p("─" * 50, "d")
        p(f"  📋 共 {len(chapters)} 个章节", "b")
        p(f"  📖 首章: {chapters[0][0]}", "d")
        p(f"  📖 末章: {chapters[-1][0]}", "d")
        p("─" * 50, "d")
        print()

        # 并发线程数选择
        max_workers = self.settings["max_workers"]
        workers_limit = self.settings["max_workers_limit"]
        p("⚡ 并发线程数设置（同时下载的章节数）:", "b")
        print()
        p("  🐢 保守 [5~6] — 几乎不触发反爬，适合小站 / 敏感网站", "d")
        p("  ⚖️ 平衡 [8~12] — 速度与安全兼顾，适合大多数网站（推荐）", "d")
        p("  🚀 激进 [15~20] — 速度快但可能触发限流 / 临时封 IP", "d")
        p("  ☠️ 危险 [30+] — 必定触发反爬，可能导致永久封 IP", "d")
        print()
        p(f"  默认 {max_workers} 线程，直接回车使用默认值", "y")
        workers_input = input("  请输入线程数 ▶ ").strip()
        if workers_input == "":
            num_workers = max_workers
        else:
            try:
                num_workers = int(workers_input)
                if num_workers < 1:
                    p("  线程数不能小于 1，使用默认值", "y")
                    num_workers = max_workers
                elif num_workers > workers_limit:
                    p(f"  ⚠ 线程数过大（>{workers_limit}），已限制为 {workers_limit}", "y")
                    num_workers = workers_limit
            except ValueError:
                p("  输入无效，使用默认值", "y")
                num_workers = max_workers
        print()

        # 确认
        p(f"确认开始爬取？({num_workers} 线程) (y/n)", "y")
        choice = input("   ▶ ").strip().lower()
        if choice not in ('y', 'yes', ''):
            p("已取消", "y")
            input("\n按回车键退出...")
            return

        # 探测正文选择器
        print()
        selector = self.detect_content_selector(chapters[0][1])

        # 开始爬取
        print()
        p("═" * 50, "c")
        p(f"  🚀 开始并发爬取 {len(chapters)} 个章节 ({num_workers} 线程)", "b")
        p("═" * 50, "c")
        print()

        total = len(chapters)
        t0 = time.time()
        chapter_url_set = set(u for _, u in chapters)

        def on_interrupt(sig, frame):
            self.stop = True
            p("\n\n⚠ 收到中断信号，等待进行中的任务完成...", "y")
        signal.signal(signal.SIGINT, on_interrupt)

        results = []
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {}
            for i, (title, url_ch) in enumerate(chapters):
                if self.stop:
                    break
                future = executor.submit(
                    self._fetch_single_chapter,
                    i, title, url_ch, chapter_url_set, selector, total
                )
                futures[future] = i

            for future in as_completed(futures):
                if self.stop:
                    executor.shutdown(wait=False, cancel_futures=True)
                try:
                    idx, page_title, lines = future.result()
                    if page_title is not None and lines is not None:
                        results.append((idx, page_title, lines))
                except Exception as e:
                    with self.lock:
                        self.failed.append(("unknown", "", f"线程异常: {e}"))

        print("\n")

        results.sort(key=lambda x: x[0])
        for _, page_title, lines in results:
            self.contents.append((page_title, lines))

        # 统计
        elapsed = time.time() - t0
        print("\n")
        p("═" * 50, "g")
        p(f"  ✅ 爬取完成！", "g")
        p(f"  📊 成功: {len(self.contents)}/{total} 章", "g")
        if self.failed:
            p(f"  ❌ 失败: {len(self.failed)} 章", "r")
        p(f"  ⏱️  耗时: {elapsed:.1f} 秒", "g")
        p("═" * 50, "g")

        if self.failed:
            print()
            p("失败章节:", "y")
            for ft, fu, fe in self.failed[:10]:
                p(f"  - {ft}: {fe}", "d")
            if len(self.failed) > 10:
                p(f"  ... 还有 {len(self.failed)-10} 个", "d")

        # 保存
        if self.contents:
            print()
            path = self.save()
            size_kb = os.path.getsize(path) / 1024
            total_chars = sum(len('\n'.join(c)) for _, c in self.contents)
            print()
            p(f"📄 已保存: {path}", "g")
            p(f"   大小: {size_kb:.1f} KB | 字数: {total_chars:,}", "d")
        else:
            p("\n[失败] 未成功爬取任何章节", "r")

        input("\n按回车键退出...")

    # ── 保存 TXT ──────────────────────────────────────────────
    def save(self):
        # 优先使用提取到的小说标题
        name = self.novel_title
        if not name:
            if self.contents:
                # 回退：从首章标题提取
                first = self.contents[0][0]
                name = re.sub(r'^第[一二三四五六七八九十百千万零\d]+[章回节集卷篇第部].*', '', first).strip()
                if not name:
                    name = re.sub(r'^\d+[.、\s]+', '', first).strip()
                if not name:
                    name = self.settings["default_novel_name"]
            else:
                name = self.settings["default_novel_name"]

        filename_max = self.settings["filename_max_len"]
        safe = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', name)[:filename_max]
        output_dir = self.settings["output_dir"]
        path = os.path.join(output_dir, f"{safe}.txt")

        if os.path.exists(path):
            i = 2
            while os.path.exists(os.path.join(output_dir, f"{safe}_{i}.txt")):
                i += 1
            path = os.path.join(output_dir, f"{safe}_{i}.txt")

        with open(path, 'w', encoding=self.settings["default_encoding"]) as f:
            f.write(f"{'='*60}\n")
            f.write(f"  {name}\n")
            f.write(f"  来源: {self.domain}\n")
            f.write(f"  章节: {len(self.contents)}\n")
            f.write(f"  导出: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*60}\n\n\n")

            for title, lines in self.contents:
                f.write(f"\n{'─'*40}\n")
                f.write(f"{title}\n")
                f.write(f"{'─'*40}\n\n")
                f.write('\n'.join(lines))
                f.write('\n')

        return path


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    try:
        NovelCrawler().run()
    except KeyboardInterrupt:
        print("\n已退出")
    except Exception as e:
        print(f"\n[致命错误] {e}")
        traceback.print_exc()
        input("按回车键退出...")