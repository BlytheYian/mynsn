"""
探針記錄與執行緒安全的記錄日誌。

TC-U-05: ProbeLog 執行緒安全測試
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class ProbeRecord:
    """單筆探針觀測記錄。"""

    test_id: str
    cond_id: str
    value: bool
    decision: bool
    timestamp: float = field(default_factory=time.time)
    error_msg: str | None = None


@dataclass
class ProbeLog:
    """執行緒安全的探針日誌容器。"""

    records: list[ProbeRecord] = field(default_factory=list)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def append(self, record: ProbeRecord) -> None:
        """執行緒安全地追加一筆記錄。"""
        with self._lock:
            self.records.append(record)

    def get_by_test(self, test_id: str) -> list[ProbeRecord]:
        """回傳指定 test_id 的所有記錄。"""
        with self._lock:
            return [r for r in self.records if r.test_id == test_id]

    # def get_by_cond(self, cond_id: str) -> list[ProbeRecord]:
    #     """回傳指定 cond_id 的所有記錄。"""
    #     with self._lock:
    #         return [r for r in self.records if r.cond_id == cond_id]

    def clear(self) -> None:
        """清空所有記錄。"""
        with self._lock:
            self.records.clear()
