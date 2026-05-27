# 万能小说爬虫 v3.8.1 — 模块化重构版

## 项目结构

```
novel_crawler_v3_8_1/
├── __init__.py      # 包初始化，导出公共接口
├── __main__.py      # 入口点（python -m 方式运行）
├── config.py        # 全局设置与网站配置
├── console.py       # 控制台工具（颜色、横幅、格式选择）
├── parsers.py       # HTML 解析与内容提取
├── exporters.py     # 文件导出（TXT/EPUB）
├── updater.py       # 增量更新逻辑
├── crawler.py       # 核心爬虫逻辑
└── README.md        # 本文件
```

## 快速开始

### 方式 2: 命令行运行
```bash
# Windows CMD / PowerShell
cd F:\导出\爱丽丝书屋小说生成器
python -m novel_crawler_v3_8_1

# 或者
python 启动爬虫.py
```

### 方式 3: 作为包导入
```python
from novel_crawler_v3_8_1 import NovelCrawler
NovelCrawler().run()
```

## 安装依赖

```bash
pip install -r requirements.txt
```

或者手动安装：
```bash
pip install requests lxml
```

## 打包为 EXE

### 方法 1: 使用打包脚本（推荐）
```bash
# 双击运行
打包.bat
```

### 方法 2: 使用 spec 文件
```bash
pyinstaller 万能小说爬虫.spec
```

### 方法 3: 命令行打包
```bash
pyinstaller ^
    --name "万能小说爬虫v3.8.1" ^
    --onefile ^
    --console ^
    --clean ^
    --noconfirm ^
    --add-data "novel_crawler_v3_8_1;novel_crawler_v3_8_1" ^
    启动爬虫.py
```

打包完成后，EXE 文件在 `dist/` 目录中。

## 模块说明

### config.py
- `GLOBAL_SETTINGS`: 全局配置字典（输出目录、延迟、重试、线程数、请求头）
- `SiteConfig`: 网站配置数据类（章节模式、广告关键词、选择器等）
- `DEFAULT_SITE_CONFIG`: 默认网站配置
- `WEBSITE_CONFIGS`: 网站配置集合（可扩展）

### console.py
- `init_console()`: 初始化控制台（Windows UTF-8）
- `p()`: 带颜色的打印函数
- `banner()`: 显示程序横幅
- `select_format()`: 选择导出格式（方向键交互）

### parsers.py
- `HTMLParser`: HTML 解析器类
  - `parse()`: 解析 HTTP 响应
  - `detect_content_selector()`: 探测正文选择器
  - `extract_content()`: 提取正文内容
  - `extract_novel_title()`: 提取小说标题
  - `extract_title()`: 提取章节标题

### exporters.py
- `save_txt()`: 保存为 TXT 文件
- `save_epub()`: 保存为 EPUB 电子书
- `generate_safe_filename()`: 生成安全文件名

### updater.py
- `find_existing_file()`: 查找已存在的同名文件
- `parse_existing_chapters()`: 解析旧文件中的失败章节
- `update_existing_file()`: 更新旧文件中的失败章节

### crawler.py
- `NovelCrawler`: 核心爬虫类
  - `fetch()`: HTTP 请求（支持重试、退避）
  - `is_chapter_url()`: 判断是否为章节 URL
  - `detect_toc()`: 检测章节目录
  - `_fetch_single_chapter()`: 爬取单个章节（线程安全）
  - `run()`: 主运行流程

## 重构优势

1. **模块化**: 每个文件职责单一，1700 行拆分为 6 个模块
2. **可维护**: 修改某个功能只需编辑对应模块
3. **可复用**: 各模块可独立导入使用
4. **可测试**: 每个模块可单独编写单元测试
5. **可扩展**: 新增网站配置或导出格式只需修改对应模块
6. **可打包**: 支持 PyInstaller 打包为独立 EXE

## 版本历史

- v3.8.1: 失败章节自动重试 + EPUB导出格式
- v3.8: 增量更新功能
- v3.7: 架构重构 — 配置与逻辑分离
- v3.6: 代码加固 — 去 emoji、429/403 退避、编码增强
- v3.5: 自定义线程数 + 免责声明
- v3.4: 多线程并发爬取
- v3.3: 跟随章节内"下一页"子页
- v3.2: 简化排序逻辑
- v3.1: 排除 catalog/toc/index 路径误判
- v3.0: 链接密度过滤、扩展广告关键词

## 依赖

- Python 3.7+
- requests>=2.28.0
- lxml>=4.9.0
- PyInstaller（仅打包时需要）

## 免责声明

本程序仅供学习交流使用，请勿用于非法用途。请在下载后24小时内删除，支持正版书籍。
