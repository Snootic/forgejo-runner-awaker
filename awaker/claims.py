import threading


def claim_key(repo: str, handle: str) -> str:
    return f"{repo}#{handle}"


class Claims:
    """Weighted reservations against a fixed capacity."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._weights: dict[str, int] = {}

    def acquire(self, handle: str, weight: int = 1) -> bool:
        with self._lock:
            if (
                handle in self._weights
                or sum(self._weights.values()) + weight > self._capacity
            ):
                return False
            self._weights[handle] = weight
            return True

    def restore(self, handle: str, weight: int = 1) -> None:
        with self._lock:
            self._weights[handle] = weight

    def release(self, handle: str) -> None:
        with self._lock:
            self._weights.pop(handle, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._weights)
