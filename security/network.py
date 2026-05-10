"""Network Proxy — Domain ACL proxy with Unix domain socket."""

from __future__ import annotations

import logging
from typing import List, Optional, Set

logger = logging.getLogger("edac.security.network")


class NetworkProxy:
    """Proxy with domain ACL for sandboxed network access."""

    def __init__(self, allowlist: Optional[List[str]] = None, blocklist: Optional[List[str]] = None) -> None:
        self.allowlist: Set[str] = set(allowlist or [])
        self.blocklist: Set[str] = set(blocklist or [])

    def is_allowed(self, domain: str) -> bool:
        if domain in self.blocklist:
            return False
        if self.allowlist and domain not in self.allowlist:
            return False
        return True

    def allow(self, domain: str) -> None:
        self.allowlist.add(domain)
        self.blocklist.discard(domain)

    def block(self, domain: str) -> None:
        self.blocklist.add(domain)
        self.allowlist.discard(domain)
