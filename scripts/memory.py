"""
记忆层 - 用户偏好与反馈迭代
负责持久化用户偏好、跟踪反馈历史，为日报生成提供个性化依据
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional

# 使用统一的档案上下文管理
from scripts.profile_context import get_profile, set_profile, get_profile_data_dir


PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")

def _memory_path() -> str:
    return os.path.join(get_profile_data_dir(), "memory.json")

def _feedback_path() -> str:
    return os.path.join(get_profile_data_dir(), "feedback.json")

# 记忆衰减配置
INTEREST_DECAY_DAYS = 30  # 兴趣权重超过30天不更新则轻微衰减
INTERACTION_DECAY_DAYS = 14  # 交互记录超过14天不更新则清除
EXPLORATION_RATIO = 0.1  # 10% 探索比例


def _ensure_data_dir():
    os.makedirs(os.path.dirname(_memory_path()), exist_ok=True)


def load_memory() -> dict:
    """加载用户偏好档案"""
    _ensure_data_dir()
    path = _memory_path()
    if not os.path.exists(path):
        return _default_memory()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  [警告] 加载 memory 失败 ({path}), 使用默认: {e}")
        return _default_memory()


def _default_memory() -> dict:
    return {
        "user_id": "小龙虾",
        "focus_brands": [],
        "focus_industries": [],
        "interest_level": {},  # {"AI大模型": 0.9, "机器人": 0.7}
        "last_interactions": [],  # [{"date": "2026-03-19", "entity": "宇树科技", "type": "brand", "action": "asked_detail"}]
        "preference_notes": "",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }


def save_memory(memory: dict):
    """保存用户偏好档案"""
    _ensure_data_dir()
    path = _memory_path()
    memory["updated_at"] = datetime.now().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def load_feedback() -> list:
    """加载反馈历史"""
    _ensure_data_dir()
    path = _feedback_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  [警告] 加载 feedback 失败 ({path}): {e}")
        return []


def save_feedback(feedbacks: list):
    """保存反馈历史"""
    _ensure_data_dir()
    path = _feedback_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feedbacks, f, ensure_ascii=False, indent=2)


def record_interaction(entity: str, entity_type: str, action: str, note: str = ""):
    """
    记录用户与某品牌/行业的一次交互

    entity: 品牌名或行业名
    entity_type: "brand" | "industry"
    action: "asked_detail" | "asked_top10" | "asked_contact" | "praised" | "dismissed" | "query"
    note: 用户补充说明（如"老板对融资信号特别敏感"）
    """
    memory = load_memory()

    today = datetime.now().strftime("%Y-%m-%d")

    # 更新兴趣权重
    interest_delta = {
        "asked_detail": 0.05,
        "asked_top10": 0.1,
        "asked_contact": 0.15,
        "praised": 0.1,
        "dismissed": -0.15,
        "query": 0.05,
    }.get(action, 0.02)

    # 找到对应的行业
    if entity_type == "brand":
        # 品牌 -> 找到所属行业
        industry = _get_brand_industry(entity)
        if industry:
            _update_interest(memory, industry, interest_delta * 0.6)
        _update_interest(memory, entity, interest_delta)
    else:
        _update_interest(memory, entity, interest_delta)

    # 记录交互
    interaction = {
        "date": today,
        "entity": entity,
        "type": entity_type,
        "action": action,
        "note": note,
    }
    # 去重：新记录覆盖旧记录（同entity同类action保留最新）
    memory["last_interactions"] = [
        i for i in memory["last_interactions"]
        if not (i["entity"] == entity and i["type"] == entity_type and i["action"] == action)
    ]
    memory["last_interactions"].append(interaction)

    # 清理过期交互
    cutoff = (datetime.now() - timedelta(days=INTERACTION_DECAY_DAYS)).strftime("%Y-%m-%d")
    memory["last_interactions"] = [
        i for i in memory["last_interactions"] if i["date"] >= cutoff
    ]

    # 更新 focus 列表
    _refresh_focus_lists(memory)

    save_memory(memory)


def record_feedback(content_hash: str, feedback_type: str, note: str = ""):
    """
    记录用户对某条情报的反馈

    content_hash: 情报内容摘要的哈希（用于去重）
    feedback_type: "positive" | "negative" | "neutral"
    """
    feedbacks = load_feedback()
    today = datetime.now().strftime("%Y-%m-%d")

    feedbacks.append({
        "date": today,
        "content_hash": content_hash,
        "feedback": feedback_type,
        "note": note,
    })

    # 只保留最近90天
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    feedbacks = [f for f in feedbacks if f["date"] >= cutoff]

    save_feedback(feedbacks)


def get_personalized_weights() -> dict:
    """
    返回用于日报生成的个性化权重

    返回结构：
    {
        "brand_weights": {"vivo": 0.9, "宇树科技": 0.7},
        "industry_weights": {"AI大模型": 0.9, "机器人": 0.7},
        "dismissed_entities": ["半导体"],  # 被用户明确否定的实体
        "exploration_ratio": 0.1,  # 本期探索比例
    }
    """
    memory = load_memory()
    feedback = load_feedback()

    # 从反馈中提取被否定的实体
    dismissed = set()
    for f in feedback:
        if f.get("feedback") == "negative":
            # content_hash 格式：品牌-事件key 或 行业-行业名
            dismissed.add(f.get("content_hash", ""))

    # 兴趣权重 + 衰减
    now = datetime.now()
    interest_level = memory.get("interest_level", {})

    # 应用衰减
    for key in list(interest_level.keys()):
        last_update = memory.get("updated_at", "")
        if last_update:
            try:
                updated = datetime.fromisoformat(last_update)
                if (now - updated).days > INTEREST_DECAY_DAYS:
                    interest_level[key] *= 0.9  # 轻微衰减，不归零
            except ValueError:
                pass

    # 提取品牌权重和行业权重
    brand_weights = {}
    industry_weights = {}

    for entity, weight in interest_level.items():
        # 判断是品牌还是行业（依赖 config 中的定义）
        from scripts.search import load_brand_industry_map
        brand_industry_map = load_brand_industry_map()
        if entity in brand_industry_map:
            brand_weights[entity] = weight
        else:
            industry_weights[entity] = weight

    return {
        "brand_weights": brand_weights,
        "industry_weights": industry_weights,
        "dismissed_entities": list(dismissed),
        "exploration_ratio": EXPLORATION_RATIO,
        "focus_brands": memory.get("focus_brands", []),
        "focus_industries": memory.get("focus_industries", []),
    }


def get_exploration_candidates(industry_configs: list, n: int = 2) -> list:
    """
    返回 n 个用户未重点关注但值得探索的行业
    基于行业活跃度和分众匹配度排序
    """
    weights = get_personalized_weights()
    focused = set(weights["focus_industries"])

    # 候选：不在 focus 列表中，且没被否定的行业
    candidates = []
    for cfg in industry_configs:
        name = cfg.get("name", "")
        if name not in focused and name not in weights["dismissed_entities"]:
            # 按 priority 排序（如果 config 中有的话）
            priority = cfg.get("priority", 5)
            candidates.append((priority, name))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in candidates[:n]]


def _update_interest(memory: dict, entity: str, delta: float):
    """更新某个实体的兴趣权重"""
    current = memory.get("interest_level", {}).get(entity, 0.5)  # 默认0.5
    new_val = max(0.0, min(1.0, current + delta))
    if "interest_level" not in memory:
        memory["interest_level"] = {}
    memory["interest_level"][entity] = new_val


def _refresh_focus_lists(memory: dict):
    """刷新 focus_brands 和 focus_industries 列表（权重 >= 0.6）"""
    interest = memory.get("interest_level", {})
    brands = []
    industries = []

    from scripts.search import load_brand_industry_map
    brand_industry_map = load_brand_industry_map()

    for entity, weight in interest.items():
        if weight >= 0.6:
            if entity in brand_industry_map:
                brands.append(entity)
            else:
                industries.append(entity)

    memory["focus_brands"] = brands
    memory["focus_industries"] = industries


def _get_brand_industry(brand: str) -> Optional[str]:
    """根据品牌名找到所属行业"""
    from scripts.search import load_brand_industry_map
    return load_brand_industry_map().get(brand)


# ── 辅助工具 ──────────────────────────────────────────

def content_hash_from_result(result: dict) -> str:
    """从搜索结果生成内容哈希（用于反馈去重）"""
    brand = result.get("brand", "")
    event_key = result.get("analysis", {}).get("event_key", "")
    if brand.startswith("[行业]"):
        return f"行业-{brand.replace('[行业]', '')}"
    return f"{brand}-{event_key}"


def reset_memory():
    """重置记忆（用于调试）"""
    _ensure_data_dir()
    save_memory(_default_memory())
    save_feedback([])
    print("记忆已重置（当前 profile: {}）".format(get_profile()))
