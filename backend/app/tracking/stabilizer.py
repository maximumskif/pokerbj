"""Per-region temporal stabilization.

A region's value is committed only after `required_frames` consecutive
confident, identical readings. Low-confidence or flickering readings never
change committed state; if they persist the region is flagged `uncertain`.
Manual corrections override recognition until cleared (or the hand resets).
"""
from __future__ import annotations

from collections import deque

from ..schemas import Observation, RegionState


class Stabilizer:
    def __init__(self, required_frames: int = 3, min_confidence: float = 0.6, uncertain_after: int = 6):
        self.required = required_frames
        self.min_conf = min_confidence
        self.uncertain_after = uncertain_after
        self.states: dict[str, RegionState] = {}
        self._hist: dict[str, deque] = {}
        self._unsettled: dict[str, int] = {}
        self.overrides: dict[str, str | None] = {}

    def reset(self) -> None:
        self.states.clear()
        self._hist.clear()
        self._unsettled.clear()
        self.overrides.clear()

    def update(self, observations: list[Observation]) -> list[str]:
        """Feed one frame. Returns ids of regions whose effective value changed."""
        changed = []
        for o in observations:
            st = self.states.get(o.region_id)
            if st is None:
                st = RegionState(region_id=o.region_id, kind=o.kind, value=None, confidence=0.0, status="pending")
                self.states[o.region_id] = st
            st.last_raw, st.last_raw_confidence = o.value, o.confidence
            if o.region_id in self.overrides:
                continue
            hist = self._hist.setdefault(o.region_id, deque(maxlen=self.required))
            if o.value is not None and o.confidence >= self.min_conf:
                hist.append(o.value)
            else:
                hist.clear()
            if len(hist) == self.required and len(set(hist)) == 1:
                self._unsettled[o.region_id] = 0
                if st.value != o.value or st.status != "stable":
                    old = st.value
                    st.value, st.status, st.committed_at = o.value, "stable", o.captured_at
                    if old != o.value:
                        changed.append(o.region_id)
                st.confidence = o.confidence
            else:
                if o.value == st.value and o.confidence >= self.min_conf:
                    continue  # agrees with committed value; nothing to do
                n = self._unsettled.get(o.region_id, 0) + 1
                self._unsettled[o.region_id] = n
                if n >= self.uncertain_after and st.status != "uncertain":
                    st.status = "uncertain"
                    changed.append(o.region_id)
        return changed

    def set_override(self, region_id: str, value: str | None, kind: str, now: float) -> None:
        if value is None:
            self.overrides.pop(region_id, None)
            st = self.states.get(region_id)
            if st:
                st.status, st.source = "pending", "local"
                self._hist.pop(region_id, None)
            return
        self.overrides[region_id] = value
        st = self.states.setdefault(region_id, RegionState(region_id=region_id, kind=kind, value=None, confidence=0.0, status="manual"))
        st.value, st.status, st.source, st.confidence, st.committed_at = value, "manual", "manual", 1.0, now

    def clear_overrides(self, prefixes: tuple[str, ...]) -> None:
        for rid in [r for r in self.overrides if r.startswith(prefixes)]:
            self.set_override(rid, None, "", 0)

    def settled(self) -> bool:
        """True when every region's latest raw reading agrees with its committed value."""
        return all(st.status == "manual" or st.last_raw == st.value for st in self.states.values())

    def effective(self, region_id: str) -> RegionState | None:
        return self.states.get(region_id)
