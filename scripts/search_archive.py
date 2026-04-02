"""
搜索结果持久化层（SQLite）
按档案隔离，每次运行存档一次，支持重新生成报告而不重跑搜索。
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Optional


def _get_db_path(profile_name: str = "default") -> str:
    """数据库路径：data/profiles/{profile}/search_archive.db"""
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "profiles", profile_name)
    return os.path.join(base, "search_archive.db")


def _get_connection(profile_name: str = "default") -> sqlite3.Connection:
    """获取数据库连接（自动建表）"""
    db_path = _get_db_path(profile_name)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            brand TEXT,
            title TEXT,
            url TEXT NOT NULL,
            content TEXT,
            query_type TEXT,
            score REAL,
            published_date TEXT,
            is_english_title INTEGER DEFAULT 0,
            track_name TEXT,
            UNIQUE(url, run_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_meta (
            run_date TEXT PRIMARY KEY,
            total_count INTEGER,
            profile_config TEXT,
            created_at TEXT
        )
    """)
    # 兼容旧数据库：补充缺失的列
    try:
        conn.execute("ALTER TABLE run_meta ADD COLUMN profile_config TEXT DEFAULT '{}'")
    except Exception as e:
        print(f"  [警告] 迁移旧数据库失败: {e}")
    conn.commit()
    return conn


def save_results(results: list[dict], date_str: str, profile_name: str = "default", profile_config: dict = None) -> int:
    """将去燥后的搜索结果存档，同时存档当时的 profile 配置快照"""
    if not results:
        return 0
    conn = _get_connection(profile_name)
    cursor = conn.cursor()
    saved = 0
    for r in results:
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO search_results
                (run_date, brand, title, url, content, query_type, score, published_date, is_english_title, track_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date_str,
                r.get("brand", ""),
                r.get("title", ""),
                r.get("url", ""),
                r.get("content", ""),
                r.get("query_type", ""),
                r.get("score", 0.0),
                r.get("published_date", ""),
                1 if r.get("is_english_title") else 0,
                r.get("track_name", ""),
            ))
            saved += 1
        except Exception as e:
            print(f"  [警告] 保存结果失败 ({r.get('url', '')}): {e}")
    # profile_config 快照：存储 brands + industries 名称列表
    config_snapshot = json.dumps(profile_config, ensure_ascii=False) if profile_config else "{}"
    cursor.execute("""
        INSERT OR REPLACE INTO run_meta (run_date, total_count, profile_config, created_at)
        VALUES (?, ?, ?, ?)
    """, (date_str, saved, config_snapshot, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return saved


def load_results(date_str: str, profile_name: str = "default") -> list[dict]:
    """加载指定档案+日期的存档搜索结果"""
    conn = _get_connection(profile_name)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT brand, title, url, content, query_type, score, published_date, is_english_title, track_name
        FROM search_results WHERE run_date = ?
    """, (date_str,))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "brand": row[0] or "",
            "title": row[1] or "",
            "url": row[2] or "",
            "content": row[3] or "",
            "query_type": row[4] or "",
            "score": row[5] or 0.0,
            "published_date": row[6] or "",
            "is_english_title": bool(row[7]),
            "track_name": row[8] or "",
        }
        for row in rows
    ]


def load_profile_config(date_str: str, profile_name: str = "default") -> Optional[dict]:
    """加载指定日期存档时的 profile 配置快照"""
    conn = _get_connection(profile_name)
    cursor = conn.cursor()
    cursor.execute("SELECT profile_config FROM run_meta WHERE run_date = ?", (date_str,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    return None


def has_archive(date_str: str, profile_name: str = "default") -> bool:
    """检查指定档案+日期是否有存档"""
    conn = _get_connection(profile_name)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM run_meta WHERE run_date = ?", (date_str,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def get_archived_brand_names(date_str: str, profile_name: str = "default") -> set:
    """从存档中提取已有的品牌名集合（用于判断新增）"""
    results = load_results(date_str, profile_name)
    brands = set()
    for r in results:
        qt = r.get("query_type", "")
        if qt in ("brand_main", "sub_brand", "brand_en"):
            brands.add(r.get("brand", ""))
    return brands
