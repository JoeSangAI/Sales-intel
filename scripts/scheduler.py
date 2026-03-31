"""
调度层 — 决定"今天搜什么"

职责：
1. 读取 profile 配置
2. 根据周规则决定搜索范围（行业融资、代言人）
3. 合并共享 config 与 profile 配置
"""

import os
import yaml
from datetime import datetime

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


def load_config(config_path: str = None) -> dict:
    """加载 config.yaml"""
    if config_path is None:
        config_path = os.path.join(PROJECT_ROOT, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_profiles(profile_names: list = None) -> list[dict]:
    """
    加载 profiles/ 目录下的所有档案配置。

    profile_names: 指定加载哪些档案，None=加载全部
    返回: [{name, brands, industries, fundraising, _include_industry}, ...]
    """
    profiles_dir = os.path.join(PROJECT_ROOT, "profiles")
    if not os.path.isdir(profiles_dir):
        return []

    profiles = []
    for fname in os.listdir(profiles_dir):
        if not fname.endswith(".yaml"):
            continue
        name = fname[:-5]
        if profile_names and name not in profile_names:
            continue
        fpath = os.path.join(profiles_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            if cfg and cfg.get("name"):
                profiles.append(cfg)
        except Exception as e:
            print(f"[档案加载失败] {fname}: {e}")

    return profiles


def _merge_config_with_profile(shared_config: dict, profile: dict) -> dict:
    """将共享 config 的 search/report 配置与 profile 合并（fundraising 做深度合并）"""
    base_fundraising = shared_config.get("fundraising", {})
    profile_fundraising = profile.get("fundraising", {})
    # 深度合并：base 的 stages/excluded_tracks + profile 的 tracks（后者覆盖前者）
    merged_fundraising = {
        "stages": base_fundraising.get("stages", []),
        "tracks": profile_fundraising.get("tracks", base_fundraising.get("tracks", [])),
        "excluded_tracks": base_fundraising.get("excluded_tracks", []),
    }
    return {
        "brands": profile.get("brands", []),
        "industries": profile.get("industries", []),
        "fundraising": merged_fundraising,
        # search/report 配置沿用 shared config
        "search": shared_config.get("search", {}),
        "report": shared_config.get("report", {}),
    }


def get_schedule_flags() -> dict:
    """
    根据星期几决定今天搜索什么内容。

    周规则：
    - 周一(0)、周三(2): 客户新闻 + 行业融资新闻 + 代言人新闻(仅周三)
    - 周二(1)、周四(3)、周五(4)、周六(5)、周日(6): 仅客户新闻
    """
    weekday = datetime.now().weekday()
    include_industry = weekday in (0, 2)
    include_endorsement = weekday == 2
    day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    print(f"  [周规则] 今天是{day_names[weekday]}：行业融资={'是' if include_industry else '否'}，代言人={'是' if include_endorsement else '否'}")
    return {
        "include_industry": include_industry,
        "include_endorsement": include_endorsement,
        "weekday": weekday,
    }
