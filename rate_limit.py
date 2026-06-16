#!/usr/bin/env python3
"""
Bot Collective Rate Limiter — Token bucket per API key.
Lightweight in-memory implementation, no Redis needed.
"""

import time
import threading
from collections import defaultdict


class RateLimiter:
    """
    Token bucket rate limiter keyed by identifier (key_id or IP).
    
    Each bucket refills at `rate` tokens/sec, max capacity `burst`.
    Default free tier: 60 req/min search, 10 req/min contribute.
    """
    
    def __init__(self, rate: float = 1.0, burst: int = 60):
        self.rate = rate          # Tokens refilled per second
        self.burst = burst        # Max token capacity
        self.tokens: dict[str, float] = defaultdict(lambda: burst)
        self.last_refill: dict[str, float] = defaultdict(time.time)
        self.lock = threading.Lock()
    
    def _refill(self, key: str):
        """Refill tokens for a key based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill[key]
        new_tokens = min(self.burst, self.tokens[key] + elapsed * self.rate)
        self.tokens[key] = new_tokens
        self.last_refill[key] = now
    
    def consume(self, key: str, cost: float = 1.0) -> bool:
        """
        Try to consume tokens. Returns True if allowed, False if rate-limited.
        """
        with self.lock:
            self._refill(key)
            if self.tokens[key] >= cost:
                self.tokens[key] -= cost
                return True
            return False
    
    def remaining(self, key: str) -> float:
        """Check remaining tokens without consuming."""
        with self.lock:
            self._refill(key)
            return self.tokens[key]


# Pre-configured limiters
# NOTE: Beta period — high limits as anti-abuse only. When paid tier launches, RESTORE:
#   SEARCH_LIMITER = RateLimiter(rate=1.0, burst=60)       # 60/min free
#   CONTRIBUTE_LIMITER = RateLimiter(rate=0.17, burst=10)   # 10/min free
SEARCH_LIMITER = RateLimiter(rate=166.0, burst=10000)     # 10000/min = anti-abuse only
CONTRIBUTE_LIMITER = RateLimiter(rate=166.0, burst=10000) # 10000/min = anti-abuse only
SIGNUP_LIMITER = RateLimiter(rate=0.05, burst=3)          # 3 signups/min per IP
PRO_RATE = 5.0      # Pro tier: 5x rate multiplier

# Tier rate multipliers
TIER_MULTIPLIER = {
    'free': 1.0,
    'pro': 5.0,
    'enterprise': 100.0,
}


def get_limit_key(auth_info: dict, request_ip: str) -> str:
    """
    Get rate limit key. Uses key_id for auth'd users, IP for anonymous.
    Tier multiplier applied at consume time.
    """
    if auth_info and auth_info.get('key_id'):
        # System key: unlimited
        if auth_info.get('bypass'):
            return None
        return auth_info['key_id']
    return f"ip:{request_ip}"


def check_search_limit(auth_info: dict, request_ip: str = "unknown") -> tuple[bool, float]:
    """
    Check if search is rate-limited. Returns (allowed, remaining_tokens).
    """
    key = get_limit_key(auth_info, request_ip)
    if key is None:
        return True, 999.0  # System key = unlimited
    
    # Apply tier multiplier: pro users get effectively higher burst/rate
    tier = auth_info.get('tier', 'free') if auth_info else 'free'
    multiplier = TIER_MULTIPLIER.get(tier, 1.0)
    cost = 1.0 / multiplier
    
    remaining = SEARCH_LIMITER.remaining(key)
    allowed = SEARCH_LIMITER.consume(key, cost)
    return allowed, remaining * multiplier


def check_contribute_limit(auth_info: dict, request_ip: str = "unknown") -> tuple[bool, float]:
    """Check if contribute is rate-limited."""
    key = get_limit_key(auth_info, request_ip)
    if key is None:
        return True, 999.0
    
    tier = auth_info.get('tier', 'free') if auth_info else 'free'
    multiplier = TIER_MULTIPLIER.get(tier, 1.0)
    cost = 1.0 / multiplier
    
    remaining = CONTRIBUTE_LIMITER.remaining(key)
    allowed = CONTRIBUTE_LIMITER.consume(key, cost)
    return allowed, remaining * multiplier


def check_signup_limit(request_ip: str) -> tuple[bool, float]:
    """Check if signup is rate-limited per IP."""
    key = f"signup:{request_ip}"
    remaining = SIGNUP_LIMITER.remaining(key)
    allowed = SIGNUP_LIMITER.consume(key)
    return allowed, remaining
