"""ULID generator (Crockford base32, monotonic within process). Stdlib only."""

import os
import threading
import time

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_lock = threading.Lock()
_last_ms = 0
_last_rand = 0


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def ulid() -> str:
    """Return a new 26-char ULID. Monotonic within this process."""
    global _last_ms, _last_rand
    with _lock:
        now_ms = int(time.time() * 1000)
        if now_ms <= _last_ms:
            now_ms = _last_ms
            _last_rand += 1
            rand = _last_rand
        else:
            rand = int.from_bytes(os.urandom(10), "big")
            _last_ms = now_ms
            _last_rand = rand
        _last_ms = now_ms
        _last_rand = rand
    return _encode(now_ms, 10) + _encode(rand, 16)
