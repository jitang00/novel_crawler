#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万能小说爬虫 v3.8.1 — 增量更新逻辑
"""

import os
import re

from .config import GLOBAL_SETTINGS
from .exporters import generate_safe_filename
from .console import p


def find_existing_file(novel_title, output_dir=None):
    """查找是否存在同名的旧文件，返回文件路径或 None
    
    Args:
        novel_title: 小说标题
        output_dir: 输出目录
        
    Returns:
        文件路径或 None
    """
    if output_dir is None:
        output_dir = GLOBAL_SETTINGS["output_dir"]
    
    safe = generate_safe_filename(novel_title)
    
    # 检查主文件名（两种格式）
    for ext in ('.txt', '.epub'):
        main_path = os.path.join(output_dir, f"{safe}{ext}")
        if os.path.exists(main_path):
            return main_path
    
    # 检查可能的编号文件
    for ext in ('.txt', '.epub'):
        i = 2
        while i <= 10:
            alt_path = os.path.join(output_dir, f"{safe}_{i}{ext}")
            if os.path.exists(alt_path):
                return alt_path
            i += 1
    
    return None


def parse_existing_chapters(file_path):
    """解析旧文件，按章节分隔符拆分后逐块搜索失败标记
    
    Args:
        file_path: 旧文件路径
        
    Returns:
        失败章节的索引集合
    """
    failed_indices = set()

    with open(file_path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    sep = '─' * 40
    blocks = re.split(
        r'(\n' + re.escape(sep) + r'\n[^\n]+\n' + re.escape(sep) + r'\n)',
        content,
    )

    chapter_index = -1
    for i, block in enumerate(blocks):
        m = re.match(
            r'\n' + re.escape(sep) + r'\n([^\n]+)\n' + re.escape(sep) + r'\n',
            block,
        )
        if m:
            chapter_index += 1
            if i + 1 < len(blocks) and '【章节获取失败】' in blocks[i + 1]:
                failed_indices.add(chapter_index)

    return failed_indices


def update_existing_file(file_path, new_contents):
    """更新旧文件，按章节块切分，定位失败标记块整体替换
    
    Args:
        file_path: 旧文件路径
        new_contents: 新的章节内容 [(idx, title, lines), ...]
        
    Returns:
        更新后的文件路径
    """
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    sep = '─' * 40
    blocks = re.split(
        r'(\n' + re.escape(sep) + r'\n[^\n]+\n' + re.escape(sep) + r'\n)',
        content,
    )

    i = 0
    while i < len(blocks):
        block = blocks[i]
        m = re.match(
            r'\n' + re.escape(sep) + r'\n([^\n]+)\n' + re.escape(sep) + r'\n',
            block,
        )
        if m and i + 1 < len(blocks) and '【章节获取失败】' in blocks[i + 1]:
            title = m.group(1)
            for idx, ct, cl in new_contents:
                if ct == title:
                    new_block = f"\n{sep}\n{title}\n{sep}\n\n"
                    new_block += '\n'.join(cl) + '\n'
                    blocks[i] = new_block
                    blocks[i + 1] = ''
                    break
        i += 1

    with open(file_path, 'w', encoding='utf-8-sig') as f:
        f.write(''.join(blocks))

    return file_path
