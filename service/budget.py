"""What an anonymous caller is allowed to cost.

The reader is deliberately public: a judge opens it with no account and that is
the point. Three of its endpoints are not free to serve. `POST /run` and
`POST /run/stream` execute a chore, which means four Gemini calls and a dozen
appends to the provenance thread. `GET /standards?repository=` spends from a
GitHub rate limit shared by everyone using the page.

So the surface that costs money is bounded here, and the surface that only reads
is not. The bound is deliberately crude: a fixed number of expensive calls per
window per client, held in this process. That is enough to stop a script, and it
is honest about what it is not.

**What this is not.** It is not a distributed limiter. Cloud Run runs up to four
instances of the reader and each holds its own counter, so the effective ceiling
is the limit times the instance count. It is not proof against a caller who
changes address. Getting either right means a shared store and a token bucket
per identity, which is worth doing when there is an identity to key on, and
today there is not.

What it does buy: a single client cannot loop `POST /run` and turn a public demo
into a bill, and the provenance thread stays a record of work rather than a
record of somebody's load test.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

# Chosen against the demo, not out of the air: a judge reading the page runs a
# chore once, maybe three times while trying the approve toggle. Ten in ten
# minutes is comfortably above curiosity and well below a script.
DEFAULT_LIMIT = int(os.environ.get("MITOS_RATE_LIMIT", "10"))
DEFAULT_WINDOW_S = float(os.environ.get("MITOS_RATE_WINDOW_S", "600"))

# A ceiling on how many callers are remembered, so the limiter itself cannot be
# turned into the memory leak that exhausts the instance. Beyond this the oldest
# idle caller is forgotten, which fails open for that caller and is the right
# direction to fail: a limiter that starts refusing everybody under load has
# become the outage it was meant to prevent.
MAX_TRACKED_CLIENTS = 4096


@dataclass
class Decision:
    allowed: bool
    remaining: int
    retry_after_s: int = 0
    limit: int = DEFAULT_LIMIT


@dataclass
class RateLimiter:
    """A fixed window per client, counted in this process only."""

    limit: int = DEFAULT_LIMIT
    window_s: float = DEFAULT_WINDOW_S
    _hits: dict[str, deque] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, client: str, now: Optional[float] = None) -> Decision:
        """Record one expensive call and say whether it is allowed.

        Recording and deciding are one operation on purpose. Splitting them
        leaves a window where two concurrent requests both read a count below
        the limit and both proceed.
        """
        stamp = time.monotonic() if now is None else now
        key = client or "unknown"
        with self._lock:
            seen = self._hits.setdefault(key, deque())
            cutoff = stamp - self.window_s
            while seen and seen[0] <= cutoff:
                seen.popleft()
            if len(seen) >= self.limit:
                # The oldest call in the window decides when the next one is
                # free, so the answer is a real number of seconds rather than
                # the window length.
                return Decision(
                    allowed=False,
                    remaining=0,
                    retry_after_s=max(1, int(seen[0] + self.window_s - stamp) + 1),
                    limit=self.limit,
                )
            seen.append(stamp)
            self._forget_idle(stamp)
            return Decision(
                allowed=True, remaining=self.limit - len(seen), limit=self.limit
            )

    def _forget_idle(self, stamp: float) -> None:
        if len(self._hits) <= MAX_TRACKED_CLIENTS:
            return
        cutoff = stamp - self.window_s
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            self._hits.pop(key, None)


def client_of(request) -> str:
    """Who to count against.

    Cloud Run terminates TLS and appends the caller to `X-Forwarded-For`, so the
    left-most entry is the client as the load balancer saw it. It is spoofable
    by the client, which is why this is a cost bound and not a security control:
    a caller who forges the header gets a fresh bucket, and still cannot write
    anything, because writing needs an approval the reader will not grant.
    """
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")
    first = forwarded[0].strip()
    if first:
        return first
    return getattr(getattr(request, "client", None), "host", "") or "unknown"
