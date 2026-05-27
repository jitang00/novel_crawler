#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.8.1 — 文件导出（TXT/EPUB）
"""

import os
import re
import time
import zipfile
import uuid
from xml.sax.saxutils import escape

from .config import GLOBAL_SETTINGS


def generate_safe_filename(novel_title):
    """生成安全的文件名（不含特殊字符）"""
    name = novel_title or "小说"
    safe = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', name)[:80]
    return safe


def save_txt(contents, domain, novel_title=None, all_chapters=None, failed_list=None, output_dir=None):
    """将爬取内容保存为 TXT 文件
    
    Args:
        contents: [(idx, title, lines), ...] 爬取到的章节内容
        domain: 来源域名
        novel_title: 小说标题
        all_chapters: 完整章节列表（用于显示进度）
        failed_list: 失败章节列表
        output_dir: 输出目录
        
    Returns:
        保存的文件路径
    """
    if output_dir is None:
        output_dir = GLOBAL_SETTINGS["output_dir"]

    # 优先使用提取到的小说标题
    name = novel_title
    if not name:
        if contents:
            # 回退：从首章标题提取
            first = contents[0][1]
            name = re.sub(r'^第[一二三四五六七八九十百千万零\d]+[章回节集卷篇第部].*', '', first).strip()
            if not name:
                name = re.sub(r'^\d+[.、\s]+', '', first).strip()
            if not name:
                name = "小说"
        else:
            name = "小说"

    safe = generate_safe_filename(name)
    path = os.path.join(output_dir, f"{safe}.txt")

    if os.path.exists(path):
        i = 2
        while os.path.exists(os.path.join(output_dir, f"{safe}_{i}.txt")):
            i += 1
        path = os.path.join(output_dir, f"{safe}_{i}.txt")

    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write(f"{'='*60}\n")
        f.write(f"【免责声明】\n")
        f.write(f"本文件仅供个人学习和研究使用，禁止用于商业用途。\n")
        f.write(f"请在下载后24小时内删除，支持正版书籍。\n")
        f.write(f"{'='*60}\n\n")
        f.write(f"{'='*60}\n")
        f.write(f"  {name}\n")
        f.write(f"  来源: {domain}\n")
        if all_chapters:
            f.write(f"  章节: {len(contents)}/{len(all_chapters)}\n")
        else:
            f.write(f"  章节: {len(contents)}\n")
        f.write(f"  导出: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{'='*60}\n\n\n")

        if all_chapters and failed_list:
            # 完整模式：按索引对齐，不依赖标题匹配
            contents_dict = {idx: lines for idx, _, lines in contents}
            failed_indices = {fi for _, _, fi, _ in failed_list}

            for i, (title, _) in enumerate(all_chapters):
                f.write(f"\n{'─'*40}\n")
                f.write(f"{title}\n")
                f.write(f"{'─'*40}\n\n")
                if i in contents_dict:
                    f.write('\n'.join(contents_dict[i]))
                    f.write('\n')
                elif i in failed_indices:
                    f.write("【章节获取失败】\n")
                    f.write("该章节未能成功获取，请重新运行程序进行更新。\n")
        else:
            # 简单模式：只写入已获取的章节
            for _, title, lines in contents:
                f.write(f"\n{'─'*40}\n")
                f.write(f"{title}\n")
                f.write(f"{'─'*40}\n\n")
                f.write('\n'.join(lines))
                f.write('\n')

    return path


def save_epub(contents, domain, novel_title=None, all_chapters=None, failed_list=None, output_dir=None):
    """将爬取内容保存为 EPUB 电子书（含导航、封面）
    
    Args:
        contents: [(idx, title, lines), ...] 爬取到的章节内容
        domain: 来源域名
        novel_title: 小说标题
        all_chapters: 完整章节列表
        failed_list: 失败章节列表
        output_dir: 输出目录
        
    Returns:
        保存的文件路径
    """
    if output_dir is None:
        output_dir = GLOBAL_SETTINGS["output_dir"]

    name = novel_title or "小说"
    safe = generate_safe_filename(name)

    path = os.path.join(output_dir, f"{safe}.epub")
    if os.path.exists(path):
        i = 2
        while os.path.exists(os.path.join(output_dir, f"{safe}_{i}.epub")):
            i += 1
        path = os.path.join(output_dir, f"{safe}_{i}.epub")

    # 按索引对齐章节，不依赖标题
    if all_chapters and failed_list:
        contents_dict = {idx: (title, lines) for idx, title, lines in contents}
        failed_indices = {fi for _, _, fi, _ in failed_list}
        ordered = []
        for i, (title, _) in enumerate(all_chapters):
            if i in contents_dict:
                ordered.append(contents_dict[i])
            elif i in failed_indices:
                ordered.append((title, ["【章节获取失败】", "该章节未能成功获取，请重新运行程序进行更新。"]))
            else:
                ordered.append((title, ["【章节未爬取】"]))
    else:
        ordered = [(title, lines) for _, title, lines in contents]

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('mimetype', 'application/epub+zip')

        zf.writestr('META-INF/container.xml',
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>')

        # ── content.opf（含封面、导航、章节） ──
        opf = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="3.0">',
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
            f'<dc:identifier id="uid">urn:uuid:{uuid.uuid4()}</dc:identifier>',
            f'<dc:title>{escape(name)}</dc:title>',
            f'<dc:language>zh-CN</dc:language>',
            f'<dc:date>{time.strftime("%Y-%m-%d")}</dc:date>',
            '</metadata>',
            '<manifest>',
            '<item id="style" href="style.css" media-type="text/css"/>',
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
            '<item id="intro" href="intro.xhtml" media-type="application/xhtml+xml"/>',
        ]
        spine = ['<itemref idref="cover"/>', '<itemref idref="intro"/>']

        for i, (ch_title, _) in enumerate(ordered):
            ch_id = f"ch{i}"
            opf.append(f'<item id="{ch_id}" href="{ch_id}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{ch_id}"/>')

        opf.append('</manifest>')
        opf.append(f'<spine>{"".join(spine)}</spine>')
        opf.append('</package>')
        zf.writestr('OEBPS/content.opf', ''.join(opf))

        # ── CSS ──
        css = ('body{font-family:serif;line-height:1.8;margin:1em;}'
               'h1{font-size:1.4em;margin:1.5em 0 0.8em;border-bottom:1px solid #ccc;padding-bottom:0.3em;}'
               'p{text-indent:2em;margin:0.3em 0;}'
               'nav ol{list-style:none;padding-left:0;}'
               'nav li{margin:0.3em 0;}'
               'nav a{text-decoration:none;color:#333;}')
        zf.writestr('OEBPS/style.css', css)

        # ── 封面 ──
        cover_xhtml = (f'<?xml version="1.0" encoding="UTF-8"?>'
                      f'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                      f'<title>{escape(name)}</title>'
                      f'<link rel="stylesheet" href="style.css"/></head>'
                      f'<body style="text-align:center;padding-top:30%;">'
                      f'<h1 style="font-size:2em;border:none;">{escape(name)}</h1>'
                      f'<p style="text-indent:0;color:#666;">{escape(domain)}</p>'
                      f'<p style="text-indent:0;color:#999;">{time.strftime("%Y-%m-%d")}</p>'
                      f'</body></html>')
        zf.writestr('OEBPS/cover.xhtml', cover_xhtml)

        # ── 简介页 ──
        disclaimer = ('<div style="text-align:center;margin-top:3em;color:#666;">'
                      '<p>免责声明</p>'
                      '<p>本文件仅供个人学习和研究使用，禁止用于商业用途。</p>'
                      '<p>请在下载后24小时内删除，支持正版书籍。</p>'
                      '</div>')
        intro_xhtml = (f'<?xml version="1.0" encoding="UTF-8"?>'
                      f'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                      f'<title>{escape(name)}</title>'
                      f'<link rel="stylesheet" href="style.css"/></head><body>'
                      f'<h1>{escape(name)}</h1>'
                      f'<p>来源: {domain}</p>'
                      f'<p>章节: {len(contents)}/{len(all_chapters) if all_chapters else len(contents)}</p>'
                      f'<p>导出: {time.strftime("%Y-%m-%d %H:%M:%S")}</p>'
                      f'{disclaimer}</body></html>')
        zf.writestr('OEBPS/intro.xhtml', intro_xhtml)

        # ── nav.xhtml 导航文档 ──
        nav_items = []
        for i, (ch_title, _) in enumerate(ordered):
            nav_items.append(f'<li><a href="ch{i}.xhtml">{escape(ch_title)}</a></li>')
        nav_xhtml = (f'<?xml version="1.0" encoding="UTF-8"?>'
                    f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                    f'<head><title>目录</title>'
                    f'<link rel="stylesheet" href="style.css"/></head><body>'
                    f'<nav epub:type="toc" id="toc"><h1>目录</h1>'
                    f'<ol>{"".join(nav_items)}</ol>'
                    f'</nav></body></html>')
        zf.writestr('OEBPS/nav.xhtml', nav_xhtml)

        # ── 章节内容 ──
        for i, (ch_title, lines) in enumerate(ordered):
            ch_id = f"ch{i}"
            paras = []
            for line in lines:
                paras.append(f'<p>{escape(line)}</p>')
            body = '\n'.join(paras)
            xhtml = (f'<?xml version="1.0" encoding="UTF-8"?>'
                    f'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                    f'<title>{escape(ch_title)}</title>'
                    f'<link rel="stylesheet" href="style.css"/></head><body>'
                    f'<h1>{escape(ch_title)}</h1>'
                    f'{body}</body></html>')
            zf.writestr(f'OEBPS/{ch_id}.xhtml', xhtml)

    return path
