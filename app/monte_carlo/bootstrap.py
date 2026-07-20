from __future__ import annotations

from decimal import Decimal

MASK = (1 << 64) - 1


class DeterministicGenerator:
    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError("seed must be an integer")
        self._state = seed & MASK

    def next_uint64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK
        return (value ^ (value >> 31)) & MASK

    def index(self, size: int) -> int:
        if size <= 0:
            raise ValueError("sampling population must not be empty")
        return self.next_uint64() % size


def bootstrap_sample(values: tuple[Decimal, ...], generator: DeterministicGenerator) -> tuple[Decimal, ...]:
    if not values:
        raise ValueError("sampling population must not be empty")
    return tuple(values[generator.index(len(values))] for _ in values)


def permutation_sample(values: tuple[Decimal, ...], generator: DeterministicGenerator) -> tuple[Decimal, ...]:
    if not values:
        raise ValueError("sampling population must not be empty")
    result = list(values)
    for index in range(len(result) - 1, 0, -1):
        selected = generator.index(index + 1)
        result[index], result[selected] = result[selected], result[index]
    return tuple(result)
