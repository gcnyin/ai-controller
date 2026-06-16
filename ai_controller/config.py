"""配置文件读取模块。

支持从项目目录下的 .ai-controller.toml 或 .ai-controller.yaml 读取预设参数。
配置文件为可选项，不存在时不报错。

支持的配置格式：
    TOML (.ai-controller.toml) —— 需要 Python 3.11+ 或安装 tomli
    YAML (.ai-controller.yaml) —— 需要安装 PyYAML

参数优先级：命令行参数 > 配置文件参数 > argparse 默认值
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any


# 支持的配置文件名（按优先级排序）
CONFIG_FILE_NAMES = [
    ".ai-controller.toml",
    ".ai-controller.yaml",
    ".ai-controller.yml",
]


def _try_load_toml(path: Path) -> Optional[Dict[str, Any]]:
    """尝试加载 TOML 配置文件，失败返回 None。"""
    try:
        # Python 3.11+ 内置 tomllib
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass
    except Exception:
        return None

    try:
        # 回退到第三方库 tomli
        import tomli
        with open(path, "rb") as f:
            return tomli.load(f)
    except ImportError:
        pass
    except Exception:
        return None

    return None


def _try_load_yaml(path: Path) -> Optional[Dict[str, Any]]:
    """尝试加载 YAML 配置文件，失败返回 None。"""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data
            return None
    except ImportError:
        return None
    except Exception:
        return None


def _find_config_file(target_dir: str) -> Optional[Path]:
    """在目标目录中查找第一个存在的配置文件。

    按 CONFIG_FILE_NAMES 顺序查找，找到第一个即返回。
    """
    target = Path(target_dir)
    for name in CONFIG_FILE_NAMES:
        cfg_path = target / name
        if cfg_path.is_file():
            return cfg_path
    return None


def _load_config_file(config_path: Path) -> Optional[Dict[str, Any]]:
    """根据文件后缀选择对应的解析器加载配置文件。"""
    suffix = config_path.suffix.lower()
    if suffix == ".toml":
        return _try_load_toml(config_path)
    elif suffix in (".yaml", ".yml"):
        return _try_load_yaml(config_path)
    return None


def load_config(target_dir: str) -> Dict[str, Any]:
    """加载目标目录下的配置文件，返回解析后的字典。

    如果配置文件不存在或无法解析，返回空字典。
    所有参数名与 argparse 一致，便于直接用作 defaults。

    Returns:
        dict 例如:
            {"agent": "pi", "max_rounds": 10, "ext": ".py,.ts", "timeout": 600, ...}
    """
    config_path = _find_config_file(target_dir)
    if config_path is None:
        return {}

    raw = _load_config_file(config_path)
    if raw is None:
        return {}

    # 标准化：只保留已知参数，过滤未知键
    known_params = {
        "agent",
        "max_rounds",
        "ext",
        "timeout",
        "sleep",
        "no_backup",
        "no_git",
        "agent_args",
        "keep_backups",
        "no_plan",
        "plan_only",
        "replan",
        "tasks_per_run",
        "dry_run",
        "auto_test",
        "test_command",
        "review",
    }

    config: Dict[str, Any] = {}
    for key in known_params:
        if key in raw:
            value = raw[key]
            # 布尔值直接保留，数值也直接保留，字符串同理
            # 将 int 转为 int，float 转为 float，防止 TOML/YAML 的类型问题
            if key in ("max_rounds", "timeout", "keep_backups", "tasks_per_run"):
                try:
                    config[key] = int(value)
                except (TypeError, ValueError):
                    pass  # 类型不对则忽略
            elif key == "sleep":
                try:
                    config[key] = float(value)
                except (TypeError, ValueError):
                    pass
            elif key in ("agent", "ext", "agent_args", "test_command"):
                config[key] = str(value)
            elif key in ("no_backup", "no_git", "no_plan", "plan_only", "replan", "dry_run", "review", "auto_test"):
                config[key] = bool(value)

    return config
