"""HTTPS to pinned Census hosts. Redirects are refused. Proxies are unused."""

from __future__ import annotations

import socket
import urllib.error
import urllib.parse
import urllib.request

from .errors import fail
from .hosts import ALLOWED_HOSTS, API_HOST, USER_AGENT


class RedirectError(Exception):
    def __init__(self, code: int, location: str = ""):
        self.code = code
        self.location = location or ""
        super().__init__("redirect")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise RedirectError(code, newurl or "")


def _opener() -> urllib.request.OpenerDirector:
    # ProxyHandler({}) leaves HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, and the
    # lowercase proxy variables unused, so a key on the query string stays here.
    return urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


class HttpResult:
    def __init__(
        self,
        kind: str,
        status: int = 0,
        body: bytes = b"",
        content_type: str = "",
        truncated: bool = False,
        shape: str = "any",
        path_class: str = "any",
    ):
        self.kind = kind
        self.status = status
        self.body = body
        self.content_type = content_type
        self.truncated = truncated
        self.shape = shape
        self.path_class = path_class


def _host_refused() -> HttpResult:
    return HttpResult("host_refused")


def check_destination(url: str) -> bool:
    """True when the URL is https on a pinned host and carries no key to the wrong place."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = parts.hostname
    if parts.scheme != "https" or not host or host not in ALLOWED_HOSTS:
        return False
    if parts.username or parts.password:
        return False
    if host != host.lower():
        return False
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if "key" in query:
        if host != API_HOST or parts.path.endswith(".json"):
            return False
    return True


def fetch(url: str, *, timeout: float, max_bytes: int, opener_factory=_opener) -> HttpResult:
    if not check_destination(url):
        return _host_refused()
    try:
        opener = opener_factory()
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            method="GET",
        )
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            content_type = ""
            headers = getattr(resp, "headers", None)
            if headers is not None:
                content_type = headers.get("Content-Type", "") or ""
            body = resp.read(max_bytes + 1)
    except RedirectError as exc:
        return HttpResult(_redirect_kind(exc.location))
    except urllib.error.HTTPError as exc:
        return _from_http_error(exc, url)
    except Exception as exc:  # noqa: BLE001 — mapped to a fixed kind, text is dropped
        return HttpResult(_transport_kind(exc))
    truncated = len(body) > max_bytes
    if truncated:
        body = body[:max_bytes]
    return _classify_body(status, body, content_type, truncated, url)


def _transport_kind(exc: BaseException) -> str:
    reason = str(getattr(exc, "reason", exc))
    if "Query timed out after" in reason or "timed out" in reason.lower():
        return "timeout"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    return "connection_failed"


def _redirect_kind(location: str) -> str:
    """Name a same-host Census key page. Any other redirect stays unfollowed."""
    if not location:
        return "redirect"
    try:
        parts = urllib.parse.urlsplit(location)
    except ValueError:
        return "redirect"
    host = parts.hostname
    if parts.scheme != "https" or not host or host != API_HOST or host != host.lower():
        return "redirect"
    if parts.username or parts.password:
        return "redirect"
    path = parts.path.lower()
    if path == "/data/invalid_key.html":
        return "invalid_key_redirect"
    if path == "/data/missing_key.html":
        return "missing_key_redirect"
    return "redirect"


# One row per response class. First match wins. An unnamed status retries.
# empty_table is a data call with HTTP 204 and no body: the vintage has no row.
_REFUSED = (
    "The host refused this request. Check the year, dataset, id, and key. "
    "No estimate was read from the error page."
)
_UNLISTED = (
    "Check the year, dataset, and id. That path is unlisted. No estimate was read from the error page."
)
_RETRY = "Retry later. No estimate was read from the error page."
_NO_TABLE = "That status has no estimate table. No estimate was read."
_NO_DOCUMENT = "That response has no document. No estimate was read."
_DROPPED_BODY = "No estimate was read from that body."


def _status_error(status: int) -> str:
    return "The Census host returned HTTP " + str(status) + "."


def _build_response_rows():
    rows = [
        (204, "empty", "data", "empty_table", None, "", ""),
        (204, "empty", "other", "http_status", "http_status",
         "The Census host returned HTTP 204 with an empty body.", _NO_DOCUMENT),
        (204, "bytes", "any", "http_status", "unexpected_body",
         "The Census host returned HTTP 204 with a body, so the body was not parsed.", _DROPPED_BODY),
        (204, "html", "any", "http_status", "unexpected_body",
         "The Census host returned HTTP 204 with a body, so the body was not parsed.", _DROPPED_BODY),
        (204, "unknown_variable", "any", "http_status", "unexpected_body",
         "The Census host returned HTTP 204 with a body, so the body was not parsed.", _DROPPED_BODY),
        (204, "any", "any", "http_status", "http_status", _status_error(204), _NO_TABLE),
        (404, "any", "any", "http_status", "http_status", _status_error(404), _UNLISTED),
        (400, "unknown_variable", "any", "http_status", "http_status", _status_error(400),
         "That variable id is unlisted. Check the id. No estimate was read from the error page."),
        (400, "any", "any", "http_status", "http_status", _status_error(400), _REFUSED),
        (401, "any", "any", "http_status", "http_status", _status_error(401), _REFUSED),
        (403, "any", "any", "http_status", "http_status", _status_error(403), _REFUSED),
        (200, "html_invalid", "any", "invalid_key_html", None, "", ""),
        (200, "html_missing", "any", "missing_key_html", None, "", ""),
        (200, "html", "any", "html", None, "", ""),
        (200, "bytes", "any", "ok", None, "", ""),
        (200, "empty", "any", "ok", None, "", ""),
    ]
    for status in (201, 202, 203, 205, 206, 207, 208, 226):
        rows.append((status, "any", "any", "http_status", "http_status", _status_error(status), _NO_TABLE))
    for status in (408, 429, 500, 502, 503, 504):
        rows.append((status, "any", "any", "http_status", "http_status", _status_error(status), _RETRY))
    return tuple(rows)


RESPONSE_ROWS = _build_response_rows()


def response_row(status: int, shape: str, path_class: str):
    """Return the RESPONSE_ROWS match. An unnamed status is a retry, not an estimate."""
    for row in RESPONSE_ROWS:
        if row[0] != status:
            continue
        if row[1] != "any" and row[1] != shape:
            continue
        if row[2] != "any" and row[2] != path_class:
            continue
        return row
    return (
        status,
        shape,
        path_class,
        "http_status",
        "http_status",
        _status_error(status),
        _RETRY,
    )


def path_class_of(url: str) -> str:
    """Data calls are /data/{year}/acs/acs1 and /data/{year}/acs/acs5. Documents stay other."""
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return "other"
    parts = [part for part in path.split("/") if part]
    if (
        len(parts) == 4
        and parts[0] == "data"
        and parts[1].isdigit()
        and parts[2] == "acs"
        and parts[3] in ("acs1", "acs5")
    ):
        return "data"
    return "other"


def body_shape(status: int, body: bytes, content_type: str) -> str:
    if not body:
        return "empty"
    sample = body[:800].lower()
    if status != 200 and sample.startswith(b"error: unknown variable"):
        return "unknown_variable"
    html = b"<html" in sample or b"<!doctype" in sample or "text/html" in (content_type or "").lower()
    if html and status == 200:
        if b"invalid key" in sample:
            return "html_invalid"
        if b"missing key" in sample:
            return "html_missing"
        return "html"
    if html:
        return "html"
    return "bytes"


def _from_http_error(exc: urllib.error.HTTPError, url: str) -> HttpResult:
    content_type = ""
    headers = getattr(exc, "headers", None)
    if headers is not None:
        try:
            content_type = headers.get("Content-Type", "") or ""
        except Exception:
            content_type = ""
    raw = b""
    try:
        chunk = exc.read(512)
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", errors="replace")
        if chunk:
            raw = bytes(chunk[:512])
    except Exception:
        raw = b""
    return _classify_body(int(exc.code), raw, content_type, False, url)


def _classify_body(status: int, body: bytes, content_type: str, truncated: bool, url: str) -> HttpResult:
    pclass = path_class_of(url)
    if truncated and status == 200:
        return HttpResult(
            "ok",
            status=200,
            body=body,
            content_type=content_type,
            truncated=True,
            shape="bytes",
            path_class=pclass,
        )
    shape = body_shape(status, body, content_type)
    row = response_row(status, shape, pclass)
    kind = row[3]
    if kind == "ok":
        return HttpResult("ok", status=status, body=body, content_type=content_type, shape=shape, path_class=pclass)
    return HttpResult(kind, status=status, body=b"", content_type=content_type, shape=shape, path_class=pclass)


def as_failure(result: HttpResult) -> dict | None:
    """Map a transport result to a tool failure. None when the body can be parsed."""
    if result.kind == "empty_table":
        return None
    if result.kind == "ok":
        if result.truncated:
            return fail(
                "incomplete",
                "The response was longer than the cap, so it was not parsed.",
                "Narrow the request. No estimate was read from the cut-off body.",
            )
        return None
    if result.kind == "missing_key_html":
        from .errors import KEY_HTML, KEY_NEXT
        return fail("missing_key", KEY_HTML, KEY_NEXT)
    if result.kind == "missing_key_redirect":
        from .errors import KEY_NEXT, KEY_REDIRECT_MISSING
        return fail("missing_key", KEY_REDIRECT_MISSING, KEY_NEXT)
    if result.kind == "invalid_key_html":
        from .errors import KEY_REJECTED_HTML, KEY_REJECTED_NEXT
        return fail("invalid_key", KEY_REJECTED_HTML, KEY_REJECTED_NEXT)
    if result.kind == "invalid_key_redirect":
        from .errors import KEY_REJECTED, KEY_REJECTED_NEXT
        return fail("invalid_key", KEY_REJECTED, KEY_REJECTED_NEXT)
    if result.kind == "html":
        return fail(
            "unexpected_body",
            "The server returned an HTML page, so no estimate was read.",
            "Retry the Census host. The HTML page was not passed on.",
        )
    if result.kind == "redirect":
        return fail(
            "redirect",
            "The pinned host returned a redirect. It was not followed.",
            "Retry later. No estimate was read from another host.",
        )
    if result.kind == "timeout":
        return fail(
            "timeout",
            "The Census host timed out. A timeout is a timeout.",
            "Retry later. No estimate was read.",
        )
    if result.kind == "host_refused":
        return fail(
            "host_refused",
            "That destination is not a pinned Census host.",
            "Use the Census hosts this plugin already calls. Nothing was requested.",
        )
    if result.kind == "http_status":
        row = response_row(int(result.status), result.shape, result.path_class)
        if row[4] is None:
            return None
        return fail(row[4], row[5], row[6])
    return fail(
        "connection_failed",
        "The Census host did not connect.",
        "Retry later. No estimate was read.",
    )
