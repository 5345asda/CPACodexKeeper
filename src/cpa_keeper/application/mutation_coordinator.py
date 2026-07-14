"""Order fast-scan and inspection writes for each CPA auth file."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType


@dataclass(slots=True)
class _ResourceState:
    mutation_lock: Lock = field(default_factory=Lock)
    generation: int = 0


class AuthFileMutationCoordinator:
    """Keep fast-scan actions authoritative over older inspection snapshots."""

    def __init__(self) -> None:
        self._states_lock = Lock()
        self._states: dict[str, _ResourceState] = {}

    def _state_locked(self, name: str) -> _ResourceState:
        state = self._states.get(name)
        if state is None:
            state = _ResourceState()
            self._states[name] = state
        return state

    def _state(self, name: str) -> _ResourceState:
        with self._states_lock:
            return self._state_locked(name)

    def _generation_locked(self, name: str) -> int:
        state = self._states.get(name)
        return state.generation if state is not None else 0

    def capture_generation(self, name: str) -> int:
        """Read one resource generation for a published list snapshot."""
        with self._states_lock:
            return self._generation_locked(name)

    def snapshot_generations(self, names: Iterable[str]) -> Mapping[str, int]:
        """Freeze the generations that belong to one fast-scan result."""
        with self._states_lock:
            return MappingProxyType(
                {name: self._generation_locked(name) for name in names}
            )

    def execute_fast_scan(self, name: str, action: Callable[[], object]) -> object:
        """Run a fast-scan write and invalidate older inspection snapshots."""
        state = self._state(name)
        with state.mutation_lock:
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
        with state.mutation_lock:
            with self._states_lock:
                current_generation = state.generation
            if current_generation != expected_generation:
                return None
            return action()
