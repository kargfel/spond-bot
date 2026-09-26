"""
Shared rate limiter (slowapi). Routers decorate endpoints with
`@limiter.limit("5/minute")`; the endpoint must accept a `request: Request`.
Limits are per client IP and kept in memory (reset on restart).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
