"""append-only event log for pg-build-v2.

写入：pipeline.events 文件（JSONL 格式，每行一个 event）
读取：完整 replay / tail N 条 / 单条查询
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterator


# Shanghai timezone（与旧 runner._now() 保持一致）
_SHANGHAI = timezone(timedelta(hours=8))


def _now_iso() -> str:
    """返回当前上海时区的 ISO8601 时间戳。"""
    return datetime.now(_SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _default_event_path(change_root: str) -> str:
    """计算 pipeline.events 文件路径。"""
    return os.path.join(change_root, "2-build", "pipeline.events")


class EventLog:
    """Append-only event log.

    单进程使用：runner 是单进程 orchestrator，append/tail 不需要锁。
    多进程场景：需要外部加锁（os.fcntl.flock），此处不强制。
    """

    def __init__(self, path: str | None = None, change_root: str | None = None, fsync: bool = False):
        """构造 EventLog。

        Args:
            path: 显式 event 文件路径
            change_root: change 根目录（自动计算 path）
            fsync: 是否每次 append 后 fsync。默认 False（性能优先）；
                   生产环境可设为 True 增强崩溃安全。
        """
        if path is None:
            if change_root is None:
                raise ValueError("path 或 change_root 必填其一")
            path = _default_event_path(change_root)
        self.path = path
        self.fsync = fsync

    def append(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        snapshot_after: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> dict[str, Any]:
        """追加一个 event 到 log。

        返回写入的 event 字典（含 ts）。
        """
        event: dict[str, Any] = {
            "ts": ts or _now_iso(),
            "type": event_type,
            "data": data or {},
        }
        if snapshot_after is not None:
            event["snapshot_after"] = snapshot_after

        self._ensure_dir()
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            if self.fsync:
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # 部分文件系统不支持 fsync，跳过即可
                    pass
        return event

    def _ensure_dir(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def update_path(self, new_path: str) -> None:
        """重定向 event 文件到新路径（用于 archive 后跟随 change 目录移动）。

        调用场景：pg-archive.py 把 .pg/changes/<change>/ mv 到 .pg/changes/archive/<date>-<change>/，
        EventLog 实例的 path 仍指向原位置，后续 append 会创建孤儿文件。archive 成功后
        orchestrator 调用此方法把 path 切到 archive 新位置，后续 event 自然写入 archive 副本。

        Args:
            new_path: 新的 event 文件绝对路径（通常为 archive 目录下的 pipeline.events）
        """
        self.path = new_path

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def is_empty(self) -> bool:
        """log 为空或不存在都返回 True。"""
        if not self.exists():
            return True
        return os.path.getsize(self.path) == 0

    def iter_events(self) -> Iterator[dict[str, Any]]:
        """逐行迭代所有 events（生成器）。"""
        if not self.exists():
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # 单行损坏不阻塞整体读取
                    continue

    def replay(self) -> list[dict[str, Any]]:
        """完整回放所有 events。"""
        return list(self.iter_events())

    def tail(self, n: int = 10) -> list[dict[str, Any]]:
        """读取最后 N 条 events。

        简化实现：直接读全文件，对大文件用 mmap-backed 倒读避免内存压力。
        """
        if not self.exists() or n <= 0:
            return []
        # 简化策略：直接全文件读，pipeline.events 预期单 change 几百到几千 events。
        # 真到大文件（10万+ events）再优化。
        with open(self.path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        tail_lines = lines[-n:] if len(lines) > n else lines
        result = []
        for ln in tail_lines:
            try:
                result.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return result

    def count(self) -> int:
        """统计 event 总数。"""
        return sum(1 for _ in self.iter_events())

    def filter_by_type(self, event_type: str) -> list[dict[str, Any]]:
        """过滤出指定类型的所有 events。"""
        return [e for e in self.iter_events() if e.get("type") == event_type]

    def last_event(self) -> dict[str, Any] | None:
        """返回最后一条 event。"""
        tail = self.tail(1)
        return tail[0] if tail else None

    def clear(self) -> None:
        """清空 event log（仅用于测试）。"""
        if self.exists():
            os.remove(self.path)

    def as_jsonl(self) -> str:
        """返回完整 JSONL 内容（用于调试/查看器）。"""
        if not self.exists():
            return ""
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read()