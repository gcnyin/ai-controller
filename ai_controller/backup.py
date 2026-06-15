"""备份管理 —— 目录备份与旧备份清理。"""

import shutil
from pathlib import Path
from datetime import datetime

from . import SKIP_DIRS

BACKUP_DIR_NAME = ".ai-controller-backups"


def backup_all(target_dir: str, round_num: int) -> Path | None:
    """备份整个目标目录"""
    backup_root = Path(target_dir) / BACKUP_DIR_NAME
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_folder = backup_root / f"round{round_num:04d}_{timestamp}"

    try:
        shutil.copytree(
            target_dir, backup_folder,
            ignore=shutil.ignore_patterns(*SKIP_DIRS),
            dirs_exist_ok=True,
        )
        return backup_folder
    except Exception as e:
        print(f"  ⚠ 备份失败: {e}")
        return None


def cleanup_old_backups(target_dir: str, keep_count: int):
    """删除旧备份，只保留最近 keep_count 个备份目录。

    备份目录按名称排序（名称含时间戳，自然顺序即为时间顺序），
    删除最旧的超出数量的目录。

    Args:
        target_dir: 目标目录路径
        keep_count: 保留的备份数量，<=0 表示不限制
    """
    if keep_count <= 0:
        return

    backup_root = Path(target_dir) / BACKUP_DIR_NAME
    if not backup_root.is_dir():
        return

    # 收集所有备份子目录，按名称排序（含时间戳，自然顺序即时间顺序）
    backups = sorted(
        [d for d in backup_root.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )

    if len(backups) <= keep_count:
        return

    # 删除最旧的超出部分
    to_delete = backups[:len(backups) - keep_count]
    for d in to_delete:
        try:
            shutil.rmtree(d)
        except Exception as e:
            print(f"  ⚠ 清理旧备份 {d.name} 失败: {e}")
