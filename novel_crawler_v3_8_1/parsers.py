#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.8.1 — HTML 解析与内容提取
"""

import sys
import re
import copy
import traceback
from urllib.parse import urlparse

from lxml import html as lxml_html

from .config import SiteConfig, DEFAULT_SITE_CONFIG
from .console import p


class HTMLParser:
    """HTML 解析器"""

    def __init__(self, site_config=None):
        self.config = site_config or DEFAULT_SITE_CONFIG

    def parse(self, resp):
        """解析 HTTP 响应为 lxml 文档"""
        parser = lxml_html.HTMLParser(encoding=resp.encoding or 'utf-8', recover=True)
        return lxml_html.fromstring(resp.content, parser=parser)

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

    def _clean_text(self, elem):
        """清理元素文本 — 移除噪音子元素（不修改原文档树）"""
        elem = copy.deepcopy(elem)

        for bad in elem.xpath('.//script|//style|//noscript|//ins'):
            bad.getparent().remove(bad)

        for node in list(elem.iter()):
            if node is elem:
                continue
            cls = set(node.get('class', '').split()) if node.get('class') else set()
            nid = node.get('id', '') or ''
            if cls & self.config.noise_classes or nid in self.config.noise_ids:
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)

        parts = []
        for t in elem.itertext():
            t = t.strip()
            if t and len(t) > 1:
                parts.append(t)
        return '\n'.join(parts)

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
        if re.match(r'^[\s\-=─\*＊·\.。,，、:：;；!！?？\[\]【】()（）《》<>＜＞「」『』""\'\'\"\'`]+$', stripped):
            return True

        # 4. 链接类行
        if stripped.startswith(('http://', 'https://', 'www.')):
            return True

        return False

    def detect_content_selector(self, sample_url, fetch_func, base_url):
        """探测正文选择器"""
        p("  [探测] 分析正文选择器...", "d")
        resp = fetch_func(sample_url, referer=base_url)
        if not resp:
            return None

        doc = self.parse(resp)
        selectors = self.config.content_selectors

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
                    if text_len < 100:
                        continue

                    # 链接密度惩罚: 密度越高，score 越低
                    density = self._link_density(elem)
                    if density > 0.3:
                        continue  # 导航/侧边栏，直接跳过

                    # 综合评分: 文本长度 × (1 - 密度)²
                    # 平方惩罚: density=0.16 → 系数0.71, density=0.3 → 系数0.49
                    score = text_len * (1.0 - density) ** 2
                    if score > best_score:
                        best_score = score
                        best = sel
            except Exception:
                if getattr(sys, 'frozen', False):
                    continue
                traceback.print_exc()
                continue

        if best and best_score > 100:
            p(f"  [OK] 正文选择器: {best} (score={best_score:.0f})", "g")
        else:
            p("  [!] 通用模式: 找最大文本块", "y")

        return best if best_score > 100 else None

    def extract_content(self, doc, selector=None):
        """提取正文内容"""
        text = ""

        if selector:
            try:
                if selector.startswith('//'):
                    elems = doc.xpath(selector)
                else:
                    elems = doc.cssselect(selector)
                if elems:
                    text = self._clean_text(elems[0])
            except Exception:
                if not getattr(sys, 'frozen', False):
                    traceback.print_exc()
                pass

        if not text or len(text) < 100:
            # 启发式: 找最长文本的 div，但排除链接密度高的
            best_text = ""
            best_score = 0
            for div in doc.xpath('//div'):
                try:
                    t = self._clean_text(div)
                    t_len = len(t)
                    if t_len < 200:
                        continue

                    density = self._link_density(div)
                    if density > 0.3:
                        continue  # 导航/侧边栏，跳过

                    score = t_len * (1.0 - density)
                    if score > best_score:
                        best_score = score
                        best_text = t
                except Exception:
                    if not getattr(sys, 'frozen', False):
                        traceback.print_exc()
                    continue
            if best_text:
                text = best_text

        if not text or len(text) < 50:
            # 最后手段: 所有 p 标签，但排除高链接密度的
            paras = []
            for p_elem in doc.xpath('//p'):
                try:
                    density = self._link_density(p_elem)
                    if density > 0.5:
                        continue
                    t = p_elem.text_content().strip()
                    if t and len(t) > 5:
                        paras.append(t)
                except Exception:
                    if not getattr(sys, 'frozen', False):
                        traceback.print_exc()
                    continue
            text = '\n'.join(paras)

        # 清理: 逐行过滤噪音
        lines = []
        for line in text.split('\n'):
            line = line.strip()
            if not self._is_noise_line(line):
                lines.append(line)

        return lines

    def extract_novel_title(self, doc):
        """从目录页提取小说标题"""
        for sel in self.config.novel_title_selectors:
            try:
                titles = doc.xpath(sel)
                for t in titles:
                    t = t.strip()
                    # 清理常见的后缀（如"最新章节"、"全文阅读"等）
                    t = re.sub(r'最新章节.*|全文阅读.*|全文.*|在线阅读.*', '', t)
                    t = re.sub(r'_(小说网|笔趣阁|阅读网|文学网|书屋)$', '', t)
                    t = re.sub(r'[\s\-_]+$', '', t)
                    if t and 2 < len(t) < 100:
                        return t
            except Exception:
                if not getattr(sys, 'frozen', False):
                    traceback.print_exc()
                continue
        return None

    def extract_title(self, doc):
        """提取章节标题"""
        for sel in self.config.chapter_title_selectors:
            try:
                titles = doc.xpath(sel)
                for t in titles:
                    t = t.strip()
                    if t and 2 < len(t) < 80:
                        return t
            except Exception:
                if not getattr(sys, 'frozen', False):
                    traceback.print_exc()
                continue
        return None
