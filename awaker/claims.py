import threading


def claim_key(repo: str, handle: str) -> str:
    return f"{repo}#{handle}"


class Claims:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._handles: set[str] = set()

    def acquire(self, handle: str) -> bool:
        with self._lock:
            if handle in self._handles or len(self._handles) >= self._capacity:
                return False
            self._handles.add(handle)
            return True

    def restore(self, handle: str) -> None:
        with self._lock:
            self._handles.add(handle)

    def release(self, handle: str) -> None:
        with self._lock:
            self._handles.discard(handle)

    def __len__(self) -> int:
        with self._lock:
            return len(self._handles)
