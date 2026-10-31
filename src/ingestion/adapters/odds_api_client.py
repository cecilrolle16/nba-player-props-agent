"""HTTP calls for The Odds API v4.

The V4 guide documents two calls used here. GET /v4/sports/{sport}/events
costs 0 credits. GET /v4/sports/{sport}/events/{eventId}/odds costs
unique markets returned times region units. One region, or up to 10
bookmakers, is one region unit. Bookmakers take priority when both are sent.
Auth and quota failures are returned once; this module does not retry them.
"""

from collections.abc import Mapping
import re

HOST = "https://api.the-odds-api.com"
EVENT_LIST_DOCUMENTED_COST = 0
PLAYER_MARKETS = ("player_points", "player_assists", "player_rebounds")
RETRYABLE_ERRORS = frozenset(
    {"Timeout", "ConnectTimeout", "ReadTimeout", "TimeoutError", "ConnectionError"}
)
_NON_RETRYABLE_STATUS = frozenset({401, 403, 429})
_API_KEY_QUERY = re.compile(r"(?i)(apiKey=)[^&\s\"']+")


def region_units(*, regions: tuple[str, ...], bookmakers: tuple[str, ...]) -> int:
    """Region units from the V4 bookmaker rule. Bookmakers replace regions."""

    if bookmakers:
        return (len(bookmakers) + 9) // 10
    return len(regions)


def event_odds_max_cost(*, markets: tuple[str, ...], regions: tuple[str, ...], bookmakers: tuple[str, ...]) -> int:
    """Upper bound charged if every requested market is returned."""

    return len(set(markets)) * region_units(regions=regions, bookmakers=bookmakers)


def redact_sensitive(text: str, secret: str) -> str:
    cleaned = text.replace(secret, "REDACTED") if secret else text
    return _API_KEY_QUERY.sub(r"\1REDACTED", cleaned)


def _header_map(headers: object) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        items = headers.items() if hasattr(headers, "items") else ()
    else:
        items = headers.items()
    return {str(key).lower(): str(value) for key, value in items}


def _status_reason(status: int) -> str:
    if status in {401, 403}:
        return "authentication_failed"
    if status == 429:
        return "rate_limited"
    return "provider_error"


class OddsResponse:
    def __init__(
        self,
        *,
        status: int,
        payload: object,
        headers: dict[str, str],
        reason: str | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.headers = headers
        self.reason = reason

    @property
    def retryable(self) -> bool:
        return False


def get_json(*, path: str, query: dict[str, str], timeout: float, api_key: str) -> OddsResponse:
    """GET one Odds API path. The key is not included in any returned text."""

    parse_mod = __import__("urllib.parse", fromlist=["urlencode"])
    request_mod = __import__("urllib.request", fromlist=["Request", "build_opener"])
    error_mod = __import__("urllib.error", fromlist=["HTTPError", "URLError"])
    http_error = error_mod.HTTPError
    url_error = error_mod.URLError
    full_query = dict(query)
    full_query["apiKey"] = api_key
    url = HOST + path + "?" + parse_mod.urlencode(full_query)
    opener = request_mod.build_opener(request_mod.HTTPHandler, request_mod.HTTPSHandler)
    request = request_mod.Request(url, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            headers = _header_map(response.headers)
            status = int(response.status)
    except http_error as exc:
        return OddsResponse(
            status=int(exc.code),
            payload=None,
            headers=_header_map(exc.headers),
            reason=_status_reason(int(exc.code)),
        )
    except url_error as exc:
        reason = "provider_timeout" if type(exc.reason).__name__ in RETRYABLE_ERRORS else "provider_error"
        if reason == "provider_timeout":
            timeout_error = TimeoutError(reason)
            raise timeout_error from None
        return OddsResponse(status=0, payload=None, headers={}, reason="provider_error")
    except Exception as exc:
        if type(exc).__name__ in RETRYABLE_ERRORS:
            raise
        return OddsResponse(status=0, payload=None, headers={}, reason="provider_error")
    if status in _NON_RETRYABLE_STATUS or status >= 400:
        return OddsResponse(status=status, payload=None, headers=headers, reason=_status_reason(status))
    json_mod = __import__("json")
    try:
        payload = json_mod.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return OddsResponse(status=status, payload=None, headers=headers, reason="malformed_payload")
    return OddsResponse(status=status, payload=payload, headers=headers, reason=None)
