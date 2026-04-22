"""
runtime_metrics.py — универсальные in-memory метрики рантайма.

Цели:
- Одинаково работать для любых user goals, количества агентов и числа интеграций.
- Не раздувать память от высокой кардинальности лейблов.
- Давать быстрый snapshot для health/status инструментов.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional, Tuple


_MAX_UNIQUE_VALUES_PER_TAG = int(os.getenv("RUNTIME_METRICS_MAX_UNIQUE_VALUES_PER_TAG", "200"))
_MAX_COUNTER_ROWS = int(os.getenv("RUNTIME_METRICS_MAX_COUNTER_ROWS", "10000"))
_MAX_TIMER_ROWS = int(os.getenv("RUNTIME_METRICS_MAX_TIMER_ROWS", "10000"))


def _norm(v: object) -> str:
    s = str(v or "unknown").strip().lower()
    if not s:
        return "unknown"
    if len(s) > 80:
        s = s[:80]
    return s.replace(" ", "_")


class _RuntimeMetrics:
    def __init__(self):
        self._lock = threading.RLock()
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], int] = {}
        self._timers: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], Dict[str, float]] = {}
        self._known_tag_values: Dict[str, set] = {}
        self._started_at = time.time()

    def _normalize_tags(self, tags: Optional[dict]) -> Tuple[Tuple[str, str], ...]:
        if not tags:
            return tuple()
        normalized = []
        for raw_k, raw_v in tags.items():
            k = _norm(raw_k)
            v = _norm(raw_v)

            with self._lock:
                bucket = self._known_tag_values.setdefault(k, set())
                if v not in bucket:
                    if len(bucket) >= _MAX_UNIQUE_VALUES_PER_TAG:
                        v = "__other__"
                    else:
                        bucket.add(v)

            normalized.append((k, v))
        normalized.sort(key=lambda x: x[0])
        return tuple(normalized)

    def count(self, metric: str, amount: int = 1, tags: Optional[dict] = None):
        m = _norm(metric)
        t = self._normalize_tags(tags)
        key = (m, t)
        with self._lock:
            if key not in self._counters and len(self._counters) >= _MAX_COUNTER_ROWS:
                key = (m, tuple(sorted(list(t)[:3])))
            self._counters[key] = self._counters.get(key, 0) + int(amount)

    def timing(self, metric: str, seconds: float, tags: Optional[dict] = None):
        m = _norm(metric)
        t = self._normalize_tags(tags)
        key = (m, t)
        val = max(0.0, float(seconds))
        with self._lock:
            if key not in self._timers and len(self._timers) >= _MAX_TIMER_ROWS:
                key = (m, tuple(sorted(list(t)[:3])))
            item = self._timers.get(key)
            if item is None:
                self._timers[key] = {
                    "count": 1,
                    "sum": val,
                    "max": val,
                    "min": val,
                }
                return
            item["count"] += 1
            item["sum"] += val
            item["max"] = max(item["max"], val)
            item["min"] = min(item["min"], val)

    def snapshot(self, top_n: int = 100) -> dict:
        with self._lock:
            counters = sorted(
                [
                    {
                        "metric": m,
                        "tags": dict(t),
                        "value": v,
                    }
                    for (m, t), v in self._counters.items()
                ],
                key=lambda x: x["value"],
                reverse=True,
            )[:top_n]

            timers = []
            for (m, t), agg in self._timers.items():
                c = int(agg["count"]) or 1
                timers.append(
                    {
                        "metric": m,
                        "tags": dict(t),
                        "count": c,
                        "avg": round(agg["sum"] / c, 4),
                        "min": round(agg["min"], 4),
                        "max": round(agg["max"], 4),
                    }
                )
            timers.sort(key=lambda x: x["count"], reverse=True)
            timers = timers[:top_n]

            return {
                "uptime_sec": int(time.time() - self._started_at),
                "unique_counter_rows": len(self._counters),
                "unique_timer_rows": len(self._timers),
                "counters": counters,
                "timers": timers,
                "limits": {
                    "max_unique_values_per_tag": _MAX_UNIQUE_VALUES_PER_TAG,
                    "max_counter_rows": _MAX_COUNTER_ROWS,
                    "max_timer_rows": _MAX_TIMER_ROWS,
                },
            }


_REGISTRY = _RuntimeMetrics()


def record_counter(metric: str, amount: int = 1, tags: Optional[dict] = None):
    _REGISTRY.count(metric=metric, amount=amount, tags=tags)


def record_timing(metric: str, seconds: float, tags: Optional[dict] = None):
    _REGISTRY.timing(metric=metric, seconds=seconds, tags=tags)


def get_runtime_metrics_snapshot(top_n: int = 100) -> dict:
    return _REGISTRY.snapshot(top_n=top_n)
