"""
档案上下文管理（单一状态源）

统一管理当前档案状态，避免 dedup.py 和 memory.py 各自维护全局变量。
"""
import os

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
_profile_name = None  # 唯一的全局状态


def set_profile(name: str = None):
    """切换到指定档案（None 表示 default）"""
    global _profile_name
    _profile_name = name
    # 确保目录存在
    os.makedirs(get_profile_data_dir(), exist_ok=True)


def get_profile() -> str:
    """获取当前档案名"""
    return _profile_name or "default"


def get_profile_data_dir() -> str:
    """获取当前档案的数据目录"""
    if _profile_name:
        return os.path.join(PROJECT_ROOT, "data", "profiles", _profile_name)
    return os.path.join(PROJECT_ROOT, "data", "profiles", "default")
