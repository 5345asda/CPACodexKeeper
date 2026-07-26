"""Order fast-scan and inspection writes for each CPA auth file."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from threading import Lock


@dataclass(slots=True)
class _ResourceState:
    write_lock: Lock = field(default_factory=Lock)
    generation: int = 0


class AuthFileMutationCoordinator:
    """Keep fast-scan actions authoritative over older inspection snapshots.

    Every fast-scan write attempt advances the resource generation. An
    inspection write only proceeds while the generation still matches the
    fast-scan snapshot it was derived from.
    """

    def __init__(self) -> None:
        self._states_lock = Lock()
        self._states: dict[str, _ResourceState] = {}

    def _state(self, name: str) -> _ResourceState:
        with self._states_lock:
            return self._states.setdefault(name, _ResourceState())

    def snapshot_generations(self, names: Iterable[str]) -> Mapping[str, int]:
        """Freeze the generations that belong to one fast-scan result."""
        with self._states_lock:
            return {
                name: self._states[name].generation if name in self._states else 0
                for name in names
            }

    def execute_fast_scan(self, name: str, action: Callable[[], object]) -> object:
        """Run a fast-scan write and invalidate older inspection snapshots."""
        state = self._state(name)
        with state.write_lock:
            try:
                return action()
            finally:
                with self._states_lock:
                    state.generation += 1

    def execute_inspection(
        self,
        name: str,
        expected_generation: int,
        action: Callable[[], object],
    ) -> object | None:
        """Run an inspection write only when its fast-scan snapshot remains current."""
        state = self._state(name)
        with state.write_lock:
            with self._states_lock:
                if state.generation != expected_generation:
                    return None
            return action()
