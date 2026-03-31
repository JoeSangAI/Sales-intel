"""
知识库接口 — 分众专属领域知识

按需加载分众百科中的相关知识片段，嵌入 LLM 分析 prompt 中。
当前状态：接口预留，返回空字符串（知识库文件尚未就绪）。

未来实现：
- 从 data/knowledge/ 目录按行业加载 .md 文件
- 如: data/knowledge/skincare.md, data/knowledge/ev.yaml, etc.
"""

import os

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
_KNOWLEDGE_DIR = os.path.join(PROJECT_ROOT, "data", "knowledge")


def get_knowledge_for_brand(brand: str, industry: str) -> str:
    """
    返回与该品牌/行业相关的分众知识片段。

    当前实现：返回空字符串（知识库未就绪）。
    未来从 data/knowledge/{industry_slug}.md 加载。
    """
    return ""


def get_knowledge_for_industry(industry: str) -> str:
    """
    返回与该行业相关的分众知识片段。

    包含：
    - 该行业的分众打法要点
    - 典型客户案例
    - 销售切入话术建议

    当前实现：返回空字符串（知识库未就绪）。
    """
    return ""


def get_knowledge_for_analysis() -> str:
    """
    返回给 AI 分析时的通用分众知识背景。

    包含：
    - 分众的核心价值和独特场景
    - 适合分众的客户类型
    - 判断广告投放需求的信号

    当前实现：返回空字符串（知识库未就绪）。
    """
    return ""


def _slugify(name: str) -> str:
    """将行业/品牌名转为文件 slug"""
    import re
    # 去掉括号内容
    name = re.sub(r'[（(].*?[）)]', '', name)
    # 转为小写，替换空格和/为_
    name = name.lower().strip()
    name = name.replace("/", "_").replace(" ", "_")
    # 只保留字母数字和下划线
    name = re.sub(r'[^a-z0-9_]', '', name)
    return name
