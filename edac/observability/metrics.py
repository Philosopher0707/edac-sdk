"""Prometheus-compatible metrics for token cost, latency, success rate."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Counter:
    """Monotonically increasing counter."""
    name: str
    value: float = 0.0
    labels: Dict[str, str] = field(default_factory=dict)

    def inc(self, amount: float = 1.0) -> None:
        self.value += amount


@dataclass
class Gauge:
    """Arbitrary value gauge."""
    name: str
    value: float = 0.0
    labels: Dict[str, str] = field(default_factory=dict)

    def set(self, value: float) -> None:
        self.value = value


@dataclass
class Histogram:
    """Histogram with linear buckets."""
    name: str
    buckets: List[float] = field(default_factory=lambda: [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000])
    counts: List[int] = field(default_factory=list)
    sum_value: float = 0.0
    labels: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.counts:
            self.counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        self.sum_value += value
        for i, bucket in enumerate(self.buckets):
            if value <= bucket:
                self.counts[i] += 1
                break


class MetricsCollector:
    """Collects and exports metrics."""

    def __init__(self) -> None:
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}

    def counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> Counter:
        key = self._key(name, labels)
        if key not in self._counters:
            self._counters[key] = Counter(name=name, labels=labels or {})
        return self._counters[key]

    def gauge(self, name: str, labels: Optional[Dict[str, str]] = None) -> Gauge:
        key = self._key(name, labels)
        if key not in self._gauges:
            self._gauges[key] = Gauge(name=name, labels=labels or {})
        return self._gauges[key]

    def histogram(self, name: str, labels: Optional[Dict[str, str]] = None, buckets: Optional[List[float]] = None) -> Histogram:
        key = self._key(name, labels)
        if key not in self._histograms:
            self._histograms[key] = Histogram(name=name, labels=labels or {}, buckets=buckets or [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000])
        return self._histograms[key]

    def export(self) -> str:
        lines: List[str] = []
        for c in self._counters.values():
            labels = ",".join(f'{k}="{v}"' for k, v in c.labels.items())
            lines.append(f"# HELP {c.name} counter")
            lines.append(f"# TYPE {c.name} counter")
            suffix = f"{{{labels}}}" if labels else ""
            lines.append(f"{c.name}{suffix} {c.value}")
        for g in self._gauges.values():
            labels = ",".join(f'{k}="{v}"' for k, v in g.labels.items())
            lines.append(f"# HELP {g.name} gauge")
            lines.append(f"# TYPE {g.name} gauge")
            suffix = f"{{{labels}}}" if labels else ""
            lines.append(f"{g.name}{suffix} {g.value}")
        for h in self._histograms.values():
            labels = ",".join(f'{k}="{v}"' for k, v in h.labels.items())
            for bucket, count in zip(h.buckets, h.counts):
                lines.append(f'{h.name}_bucket{{le="{bucket}"{("," + labels) if labels else ""}}} {count}')
            suffix = f"{{{labels}}}" if labels else ""
            lines.append(f"{h.name}_sum{suffix} {h.sum_value}")
            lines.append(f"{h.name}_count{suffix} {sum(h.counts)}")
        return "\n".join(lines)

    @staticmethod
    def _key(name: str, labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return name
        return name + ":" + ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
