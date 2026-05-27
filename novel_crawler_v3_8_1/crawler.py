#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.8.1 — 核心爬虫逻辑
"""

import sys
import os
import re
import time
import random
import signal
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests

from .config import GLOBAL_SETTINGS, DEFAULT_SITE_CONFIG, SiteConfig
from .console import p, init_console, banner, select_format
from .parsers import HTMLParser
from .exporters import save_txt, save_epub
from .updater import find_existing_file, parse_existing_chapters, update_existing_file


class NovelCrawler:
    """核心爬虫类"""

    def __init__(self, site_config=None, global_settings=None):
        self.config = site_config or DEFAULT_SITE_CONFIG
        self.global_settings = global_settings or GLOBAL_SETTINGS

        self.session = requests.Session()
        self.session.headers.update(self.global_settings["headers"])
        self.base_url = ""
        self.domain = ""
        self.chapters = []      # [(title, url)]
        self.contents = []      # [(idx, title, [line, ...])]
        self.failed = []
        self.stop = False
        self.js_book_id = ""    # 从 javascript: 链接中提取的 book_id
        self.novel_title = ""   # 小说标题
        self.lock = threading.Lock()
        self.progress_done = 0  # 已完成的章节数
        self.export_format = 'txt'  # 导出格式

        # 初始化 HTML 解析器
        self.parser = HTMLParser(self.config)

    # ── 请求 ──────────────────────────────────────────────────
    def fetch(self, url, retries=None, referer=None):
        """发送 HTTP GET 请求，支持重试和退避"""
        if retries is None:
            retries = self.global_settings["max_retries"]
        timeout = self.global_settings["timeout"]
        last_status = 0
        delay = random.uniform(
            self.global_settings["min_delay"],
            self.global_settings["max_delay"],
        )
        if self.stop:
            return None
        time.sleep(delay)
        for i in range(retries):
            if self.stop:
                return None
            try:
                headers = {}
                if referer:
                    headers["Referer"] = referer
                r = self.session.get(url, timeout=timeout, allow_redirects=True,
                                     headers=headers if headers else None)
                if r.status_code == 200:
                    encoding = r.encoding
                    if encoding and encoding.lower() in ('iso-8859-1', 'latin-1'):
                        raw = r.content[:4096]
                        m = re.search(rb'charset[=\s]+([a-zA-Z0-9_-]+)', raw)
                        if m:
                            encoding = m.group(1).decode('ascii')
                        else:
                            try:
                                encoding = r.apparent_encoding or 'utf-8'
                            except Exception:
                                encoding = 'utf-8'
                    r.encoding = encoding or 'utf-8'
                    return r
                elif r.status_code in (429, 403):
                    wait = min(2 ** (i + 1), 120)
                    p(f"  [!] HTTP {r.status_code} 限流/禁止，{wait}s 后重试 ({i+1}/{retries})", "y")
                    sleep_total = wait + random.uniform(0, wait * 0.5)
                    # 分段 sleep，每秒检查一次中断
                    for _ in range(int(sleep_total)):
                        if self.stop:
                            return None
                        time.sleep(1)
                    last_status = r.status_code
                else:
                    p(f"  [!] HTTP {r.status_code}: {url}", "y")
            except Exception as e:
                wait = random.uniform(1, 4)
                p(f"  [!] 请求失败 ({i+1}/{retries}): {e}", "y")
                if i < retries - 1:
                    if self.stop:
                        return None
                    time.sleep(wait)
                traceback.print_exc()
        return None

    # ── 解析 javascript: 链接 ─────────────────────────────────
    def _parse_js_link(self, href):
        """解析各种 JS 章节链接，返回构造的 URL 路径
        匹配: gobook, readbook, goRead, gotochapter, runbook 等
        格式: func('bookid','chapterid')"""
        m = re.search(
            self.config.js_chapter_pattern,
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
        """判断 URL 是否是章节链接"""
        # 先去掉 fragment（#bottom, #section 等），只判断 path
        clean_url = url.split('#')[0] if '#' in url else url
        if not clean_url:
            return False
        parsed = urlparse(clean_url)
        skip = ('.css', '.js', '.png', '.jpg', '.gif', '.ico',
                '.mp3', '.mp4', '.zip', '.rar', '.txt', '.pdf')
        if parsed.path.lower().endswith(skip):
            return False
        if parsed.netloc and parsed.netloc != self.domain:
            return False

        # 排除分类/标签/列表/目录页 — 这些不是章节
        for pat in self.config.category_path_patterns:
            if pat.search(parsed.path):
                return False

        # 匹配章节模式: 逐段匹配，避免跨段误判
        # /\d{3,}\.htm 不应跨段匹配 /novel 的 / + 50851 的数字
        # /chapter/\d+ 需要完整路径匹配（跨段）
        path_segments = [p for p in parsed.path.split('/') if p]
        for pat in self.config.chapter_patterns:
            pat_str = pat.pattern
            # 跨段模式（含多个 /）: 匹配完整路径
            if pat_str.count('/') >= 2:
                if pat.search(parsed.path):
                    return True
            else:
                # 单段模式: 逐段匹配，加 / 前缀模拟路径段
                for seg in path_segments:
                    if pat.fullmatch('/' + seg):
                        return True

        # 通用回退: 路径最后段是纯数字
        parts = [p for p in parsed.path.split('/') if p]
        # 至少需要3段 (如 /book/200806/13457451.html)，排除 /book/200806/ 这种主页
        if len(parts) >= 3:
            last = parts[-1]
            name, ext = os.path.splitext(last)
            # 必须是 "数字.html" 格式
            if name.isdigit() and ext.lower() in ('.html', '.htm', ''):
                # 额外检查: 前一段不能是 catalog/toc/index 等非章节路径
                prev = parts[-2].lower() if len(parts) >= 2 else ''
                if prev in ('catalog', 'toc', 'index', 'list', 'tag', 'tags',
                           'category', 'sort', 'class', 'search', 'page',
                           'author', 'top', 'rank', 'hot'):
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
            for pat in self.config.toc_page_patterns:
                if pat.search(parsed.path):
                    matched = True
                    break
            if matched:
                if full not in seen:
                    seen.add(full)
                    pages.append(full)
            # 也处理 javascript: 翻页链接
            elif href.startswith('javascript:'):
                m = re.search(self.config.toc_js_jump_pattern, href, re.I)
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

        # 只在标题类元素中搜索，避免匹配 <title>/<script>/<meta> 等
        search_tags = ['dt', 'h1', 'h2', 'h3', 'h4', 'p', 'span']
        for tag in search_tags:
            for keyword in ['最新章节', '全部章节', '章节列表']:
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
                            for _ in range(10):
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
                                            for a_el in dd.xpath('.//a[@href]'):
                                                href = (a_el.get('href') or '').strip()
                                                title = (a_el.text_content() or '').strip()
                                                if href and title and len(title) <= 100:
                                                    links.append((title, href))
                                        dd = dd.getnext()
                                    if links:
                                        result['all_links'] = links

                        if '全部章节' in text or '章节列表' in text:
                            result['has_all'] = True
                            nxt = elem
                            for _ in range(10):
                                nxt = nxt.getnext()
                                if nxt is None:
                                    break
                                if nxt.tag in ('ul', 'dl', 'ol'):
                                    result['all_container'] = nxt
                                    break
                except Exception:
                    if not getattr(sys, 'frozen', False):
                        traceback.print_exc()
                    continue

        return result

    # ── 检测目录 ──────────────────────────────────────────────
    def detect_toc(self, doc):
        """检测章节目录"""
        p("[1/3] 分析页面结构，识别章节目录...", "c")

        # 检测"最新章节"/"全部章节"分类
        section_info = self._detect_toc_sections(doc)
        if section_info['has_latest'] and section_info['has_all']:
            p("  [*] 检测到分类: 最新章节 + 全部章节", "g")
            p('  -> 优先使用"全部章节"区域（完整列表）', "d")
        elif section_info['has_latest']:
            p("  [*] 检测到: 最新章节（可能只有部分章节）", "y")
        elif section_info['has_all']:
            p("  [*] 检测到: 全部章节", "g")

        # 检测分页，收集所有目录页
        toc_pages = self._collect_toc_pages(doc, self.base_url)
        if len(toc_pages) > 1:
            p(f"  [*] 检测到 {len(toc_pages)} 页目录，正在加载...", "y")
            all_docs = [doc]
            for page_url in toc_pages[1:]:
                p(f"  [*] 加载: {page_url}", "d")
                resp = self.fetch(page_url, referer=self.base_url)
                if resp:
                    all_docs.append(self.parser.parse(resp))
        else:
            all_docs = [doc]

        # 从所有页面收集章节链接
        all_links = []
        for d in all_docs:
            all_links.extend(d.xpath('//a[@href]'))

        if not all_links:
            p("  [FAIL] 页面中没有链接", "r")
            return []

        # 按父容器分组，找章节链接最多的容器
        containers = {}

        for a in all_links:
            href = (a.get('href') or '').strip()
            title = (a.text_content() or '').strip()
            if not href or not title or len(title) > 100:
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
            for _ in range(5):
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
                p(f'  [OK] 使用"正文"区域: {len(unique)} 个章节', "g")
                if len(unique) >= 3:
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
                if not href or not title or len(title) > 100:
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
                p(f'  [OK] 使用"全部章节"区域: {len(unique)} 个章节', "g")
                if len(unique) >= 3:
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

        p(f"  [OK] 主目录容器: {best}", "g")
        p(f"  [OK] 识别到 {len(unique)} 个章节", "g")

        # ── 检测正序/倒序 ────────────────────────────────────
        if len(unique) >= 3:
            unique = self._fix_chapter_order(unique)

        return unique

    # ── 章节排序修正 (v3.2: 仅判断正序/倒序，不按章节号重排) ──
    def _fix_chapter_order(self, chapters):
        """检测目录是正序还是倒序，倒序则翻转。
        不再按章节号排序，避免子小节被打乱。"""

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
                if desc_ratio > 0.7:
                    p("  [>] 检测到目录倒序（URL数字递减），已翻转为正序", "y")
                    return list(reversed(chapters))
                elif desc_ratio < 0.3:
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
                if desc_ratio > 0.7:
                    p("  [>] 检测到目录倒序（标题数字递减），已翻转为正序", "y")
                    return list(reversed(chapters))

        # 无法判断，保持原序
        return chapters

    # ── 查找章节内"下一页"链接 ────────────────────────────────
    def _find_next_page(self, doc, chapter_urls):
        """查找当前章节内的"下一页"链接。
        chapter_urls: 目录中所有章节的 URL 集合（用于判断是否跳到了下一章）
        返回: 下一页 URL 或 None"""
        # 常见"下一页"文本
        next_texts = ['下一页', '下一頁', '下页', 'next', 'Next', 'NEXT']

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
        """爬取单个章节（含子页拼接）"""
        if self.stop:
            return (index, None, None)

        resp = self.fetch(url, referer=self.base_url)
        if not resp:
            with self.lock:
                self.failed.append((title, url, index, "请求失败"))
                self.progress_done += 1
            self._print_progress(total)
            return (index, None, None)

        doc = self.parser.parse(resp)
        page_title = self.parser.extract_title(doc) or title
        lines = self.parser.extract_content(doc, selector)

        if not lines:
            with self.lock:
                self.failed.append((title, url, index, "无法提取正文"))
                self.progress_done += 1
            self._print_progress(total)
            return (index, None, None)

        sub_pages = 0
        max_sub_pages = 50
        visited_sub_urls = set()
        next_url = self._find_next_page(doc, chapter_url_set)
        while next_url and sub_pages < max_sub_pages and not self.stop:
            if self.stop:
                break
            if next_url in visited_sub_urls:
                break
            visited_sub_urls.add(next_url)
            sub_resp = self.fetch(next_url, referer=url)
            if not sub_resp:
                break
            sub_doc = self.parser.parse(sub_resp)
            sub_lines = self.parser.extract_content(sub_doc, selector)
            if sub_lines:
                lines.extend(sub_lines)
                sub_pages += 1
            else:
                break
            next_url = self._find_next_page(sub_doc, chapter_url_set)

        with self.lock:
            self.progress_done += 1

        self._print_progress(total)
        return (index, page_title, lines)  # index = 原始目录中的位置

    def _print_progress(self, total):
        """打印进度条"""
        with self.lock:
            done = self.progress_done
        pct = done / total * 100
        bar_w = 30
        filled = int(bar_w * done / total)
        bar = '█' * filled + '░' * (bar_w - filled)
        p(f"\r  [{bar}] {pct:.0f}% ({done}/{total}) {self._failed_count_locked()} 失败", "c", end="")

    def _failed_count_locked(self):
        """获取失败章节数（需要在锁内调用）"""
        with self.lock:
            return len(self.failed)

    # ── 主流程 ────────────────────────────────────────────────
    def run(self):
        """主运行流程"""
        init_console()
        banner()

        # 获取 URL
        p("[*] 请输入小说目录页 URL:", "b")
        p("   (大部分小说网站的目录/索引页均可)", "d")
        url = input("   URL > ").strip()
        if not url:
            p("[错误] URL 不能为空", "r")
            input("按回车键退出...")
            return

        if not url.startswith('http'):
            url = 'https://' + url

        self.base_url = url
        self.domain = urlparse(url).netloc

        print()
        p(f"[*] 目标: {self.domain}", "c")
        p(f"    地址: {url}", "d")
        print()

        # 获取目录页
        p("[连接] 获取目录页面...", "y")
        resp = self.fetch(url)  # 首次请求，无 Referer
        if not resp:
            p("[错误] 无法访问目标页面", "r")
            input("\n按回车键退出...")
            return

        p(f"  [OK] 页面获取成功 (HTTP {resp.status_code})", "g")
        doc = self.parser.parse(resp)

        # 提取小说标题
        self.novel_title = self.parser.extract_novel_title(doc)
        if self.novel_title:
            p(f"  [*] 小说标题: {self.novel_title}", "g")

        # 检测目录
        chapters = self.detect_toc(doc)
        all_chapters_for_url_set = list(chapters)  # 保存完整列表用于 chapter_url_set
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
        p("-" * 50, "d")
        p(f"  [*] 共 {len(chapters)} 个章节", "b")
        p(f"  [*] 首章: {chapters[0][0]}", "d")
        p(f"  [*] 末章: {chapters[-1][0]}", "d")
        p("-" * 50, "d")
        print()

        # 检测是否存在旧文件
        existing_file = None
        update_mode = False
        if self.novel_title:
            existing_file = find_existing_file(self.novel_title)
        
        if existing_file:
            if existing_file.endswith('.epub'):
                p(f"  [!] 检测到同名 EPUB 文件: {os.path.basename(existing_file)}", "y")
                p("  [*] 更新功能仅支持 TXT，将新建文件", "d")
                existing_file = None
                update_mode = False
            else:
                p(f"  [!] 检测到已存在同名文件: {os.path.basename(existing_file)}", "y")
                print()
                p("  [n] 新建文件 - 生成全新的 txt 文件（覆盖检测）", "d")
                p("  [u] 更新文件 - 只爬取失败章节，更新旧文件", "d")
                choice = input("  请选择操作 [n/u] > ").strip().lower()
                if choice == 'u':
                    update_mode = True
                    p(f"  [OK] 将以更新模式运行，仅处理失败章节", "g")
                    # 解析旧文件，获取失败章节信息
                    failed_indices = parse_existing_chapters(existing_file)
                    p(f"  [*] 旧文件中检测到 {len(failed_indices)} 个失败章节", "y")
                    # 过滤出需要重新爬取的章节
                    chapters_to_fetch = [chapters[i] for i in failed_indices if i < len(chapters)]
                    if not chapters_to_fetch:
                        p("  [!] 没有需要更新的失败章节", "y")
                        p("  [*] 自动转为新建模式", "d")
                        update_mode = False
                    else:
                        chapters = chapters_to_fetch
        else:
            p("  [*] 未检测到同名旧文件", "d")
        print()

        # 并发线程数选择
        default_workers = self.global_settings["max_workers"]
        p("[*] 并发线程数设置（同时下载的章节数）:", "b")
        print()
        p("  [龟] 保守 [5~6] - 几乎不触发反爬，适合小站 / 敏感网站", "d")
        p("  [衡] 平衡 [8~12] - 速度与安全兼顾，适合大多数网站（推荐）", "d")
        p("  [快] 激进 [15~20] - 速度快但可能触发限流 / 临时封 IP", "d")
        p("  [危] 危险 [30+] - 必定触发反爬，可能导致永久封 IP", "d")
        print()
        p(f"  默认 {default_workers} 线程，直接回车使用默认值", "y")
        workers_input = input("  请输入线程数 > ").strip()
        if workers_input == "":
            num_workers = default_workers
        else:
            try:
                num_workers = int(workers_input)
                if num_workers < 1:
                    p("  线程数不能小于 1，使用默认值", "y")
                    num_workers = default_workers
                elif num_workers > 50:
                    p("  [!] 线程数过大（>50），已限制为 50", "y")
                    num_workers = 50
            except ValueError:
                p("  输入无效，使用默认值", "y")
                num_workers = default_workers
        print()

        # 导出格式选择
        p("[*] 导出格式选择（方向键上下选择，回车确认）:", "b")
        print()
        self.export_format = select_format()
        p(f"  [OK] 已选择: {self.export_format.upper()} 格式", "g")
        print()

        # 确认
        p(f"确认开始爬取？({num_workers} 线程, {self.export_format.upper()} 格式) (y/n)", "y")
        choice = input("   > ").strip().lower()
        if choice not in ('y', 'yes', ''):
            p("已取消", "y")
            input("\n按回车键退出...")
            return

        # 探测正文选择器
        print()
        selector = self.parser.detect_content_selector(
            chapters[0][1],
            lambda url, referer=None: self.fetch(url, referer=referer),
            self.base_url
        )

        # 开始爬取
        print()
        p("═" * 50, "c")
        p(f"  [>>] 开始并发爬取 {len(chapters)} 个章节 ({num_workers} 线程)", "b")
        p("═" * 50, "c")
        print()

        total = len(chapters)
        t0 = time.time()
        # 完整目录 URL 集合 — 不受更新模式过滤影响，子页拼接需要完整集合
        full_chapter_url_set = set(u for _, u in (all_chapters_for_url_set or chapters))
        chapter_url_set = full_chapter_url_set

        def on_interrupt(sig, frame):
            self.stop = True
            p("\n\n[!] 收到中断信号，等待进行中的任务完成...", "y")
        signal.signal(signal.SIGINT, on_interrupt)

        results = []
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {}
            for i, (title, url) in enumerate(chapters):
                if self.stop:
                    break
                future = executor.submit(
                    self._fetch_single_chapter,
                    i, title, url, chapter_url_set, selector, total
                )
                futures[future] = i

            for future in as_completed(futures):
                try:
                    idx, page_title, lines = future.result()
                    if page_title is not None and lines is not None:
                        results.append((idx, page_title, lines))
                except Exception as e:
                    with self.lock:
                        self.failed.append(("unknown", "", -1, f"线程异常: {e}"))

        print("\n")

        results.sort(key=lambda x: x[0])
        for idx, page_title, lines in results:
            self.contents.append((idx, page_title, lines))

        # 统计
        elapsed = time.time() - t0
        print("\n")
        p("=" * 50, "g")
        p(f"  [OK] 爬取完成！", "g")
        p(f"  [*] 成功: {len(self.contents)}/{total} 章", "g")
        if self.failed:
            p(f"  [FAIL] 失败: {len(self.failed)} 章", "r")
        p(f"  [*] 耗时: {elapsed:.1f} 秒", "g")
        p("=" * 50, "g")

        # ── 失败章节自动重试 ─────────────────────────────────
        if self.failed:
            print()
            p("失败章节:", "y")
            for ft, fu, fi, fe in self.failed[:10]:
                p(f"  - {ft}: {fe}", "d")
            if len(self.failed) > 10:
                p(f"  ... 还有 {len(self.failed)-10} 个", "d")

            print()
            print()  # 新行，避免覆盖之前的进度条
            p(f"[*] 自动重试 {len(self.failed)} 个失败章节（2 线程）...", "c")
            retry_failed = list(self.failed)
            self.failed = []
            retry_chapters = [(ft, fu) for ft, fu, fi, fe in retry_failed]
            # 提取原始索引，用于重试后正确对齐
            retry_original_indices = [fi for ft, fu, fi, fe in retry_failed]
            retry_total = len(retry_chapters)
            self.progress_done = 0

            retry_results = []
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {}
                for i, (title, url) in enumerate(retry_chapters):
                    if self.stop:
                        break
                    orig_idx = retry_original_indices[i]
                    future = executor.submit(
                        self._fetch_single_chapter,
                        orig_idx, title, url, chapter_url_set, selector, retry_total
                    )
                    futures[future] = i
                for future in as_completed(futures):
                    try:
                        idx, page_title, lines = future.result()
                        if page_title is not None and lines is not None:
                            retry_results.append((idx, page_title, lines))
                    except Exception:
                        pass

            if retry_results:
                retry_results.sort(key=lambda x: x[0])
                for idx, page_title, lines in retry_results:
                    self.contents.append((idx, page_title, lines))
                p(f"\n  [OK] 重试恢复 {len(retry_results)} 章", "g")

            if self.failed:
                p(f"  [FAIL] 仍有 {len(self.failed)} 章失败", "r")

        # 保存
        ext = self.export_format
        if self.contents:
            print()
            if update_mode and existing_file:
                # 更新模式：只更新失败章节
                if ext == 'epub':
                    path = save_epub(
                        self.contents, self.domain, self.novel_title,
                        chapters, self.failed
                    )
                else:
                    path = update_existing_file(existing_file, self.contents)
                size_kb = os.path.getsize(path) / 1024
                total_chars = sum(len('\n'.join(c)) for _, _, c in self.contents)
                print()
                p(f"[*] 已更新: {path}", "g")
                p(f"   大小: {size_kb:.1f} KB | 本次更新字数: {total_chars:,}", "d")
            else:
                # 新建模式：保存完整内容，失败章节插入标记
                if ext == 'epub':
                    path = save_epub(
                        self.contents, self.domain, self.novel_title,
                        chapters, self.failed
                    )
                else:
                    path = save_txt(
                        self.contents, self.domain, self.novel_title,
                        chapters, self.failed
                    )
                size_kb = os.path.getsize(path) / 1024
                total_chars = sum(len('\n'.join(c)) for _, _, c in self.contents)
                print()
                p(f"[*] 已保存: {path}", "g")
                p(f"   大小: {size_kb:.1f} KB | 字数: {total_chars:,}", "d")
        else:
            p("\n[失败] 未成功爬取任何章节", "r")

        input("\n按回车键退出...")
