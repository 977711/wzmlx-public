"""
Proxy module for translate feature and HubCloud downloads.
"""

from bot import LOGGER

_BRD_PROXY_URL = "http://iirbeagr:8077v7h32pus@31.59.20.176:6754"

# Sticky state for callers that expect a current proxy value.
_CURRENT_PROXY = _BRD_PROXY_URL


def _get_candidates() -> list[str]:
    """Return preferred proxy candidates in order."""
    return [_BRD_PROXY_URL]


def get_translate_proxy() -> str:
    """Get current proxy synchronously (fast)."""
    return _CURRENT_PROXY or _BRD_PROXY_URL


def get_default_proxy() -> str:
    """Return a default proxy URL (no connectivity check)."""
    return _BRD_PROXY_URL


async def get_working_translate_proxy(
    test_url: str = "https://translate.google.com",
) -> str:
    """
    Return configured BRD proxy for translator flow.
    Keeps a compatibility health check while always using configured proxy.
    """
    global _CURRENT_PROXY
    import aiohttp

    proxy = _CURRENT_PROXY or _BRD_PROXY_URL
    timeout = aiohttp.ClientTimeout(total=5)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(test_url, proxy=proxy) as resp:
                if resp.status < 400:
                    if proxy != _CURRENT_PROXY:
                        LOGGER.info("Proxy Rotated: Now using configured BRD proxy")
                        _CURRENT_PROXY = proxy
                    return proxy
    except Exception as err:
        LOGGER.warning(f"Configured BRD proxy health check failed: {err}")

    _CURRENT_PROXY = _BRD_PROXY_URL
    return _BRD_PROXY_URL
