"""Pinned hosts, redirects, proxies, and dropped HTML."""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from tests.support import SAMPLE, plugin

fetch = plugin.http_client.fetch
check_destination = plugin.http_client.check_destination


class _Resp:
    def __init__(self, body, status=200, content_type="application/json"):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self._body = body

    def read(self, count):
        return self._body[:count]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Opener:
    def __init__(self, behavior):
        self.behavior = behavior
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        return self.behavior(req, timeout)


def test_row2_timeout_phrase_is_a_timeout():
    kind = plugin.http_client._transport_kind
    assert kind(urllib.error.URLError("Query timed out after 20 seconds")) == "timeout"
    assert kind(urllib.error.URLError("connection refused, Query timed out after 1s")) == "timeout"
    assert kind(urllib.error.URLError("connection refused")) == "connection_failed"
    failure = plugin.http_client.as_failure(plugin.http_client.HttpResult("timeout"))
    assert failure["error_type"] == "timeout"
    assert "did not connect" not in failure["error"]
    assert failure["next_step"]


def test_redirect_is_not_followed_and_the_key_stays_out_of_the_result():
    def behavior(_req, _timeout):
        raise plugin.http_client.RedirectError(302)

    result = fetch(
        "https://api.census.gov/data/2024/acs/acs5?get=NAME&for=state:48&key=" + SAMPLE,
        timeout=5,
        max_bytes=1000,
        opener_factory=lambda: _Opener(behavior),
    )
    assert result.kind == "redirect"
    assert result.body == b""
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "redirect"
    assert SAMPLE not in failure["error"]


def test_missing_key_html_drops_the_page():
    page = b"<html><title>Missing Key</title>" + SAMPLE.encode("ascii") + b"</html>"

    def behavior(_req, _timeout):
        return _Resp(page, content_type="text/html")

    result = fetch(
        "https://api.census.gov/data/2024/acs/acs5",
        timeout=5,
        max_bytes=1000,
        opener_factory=lambda: _Opener(behavior),
    )
    assert result.kind == "missing_key_html"
    assert result.body == b""
    assert SAMPLE.encode("ascii") not in result.body


def test_http_error_hides_the_body():
    def behavior(_req, _timeout):
        raise urllib.error.HTTPError(
            "https://api.census.gov/data/2025/acs/acs5.json",
            404,
            "missing",
            hdrs=None,
            fp=None,
        )

    result = fetch(
        "https://api.census.gov/data/2025/acs/acs5.json",
        timeout=5,
        max_bytes=1000,
        opener_factory=lambda: _Opener(behavior),
    )
    assert result.kind == "http_status"
    assert result.status == 404
    assert result.body == b""


def test_exception_text_is_not_a_tool_result():
    def behavior(_req, _timeout):
        raise urllib.error.URLError(SAMPLE)

    result = fetch(
        "https://api.census.gov/data.json",
        timeout=5,
        max_bytes=1000,
        opener_factory=lambda: _Opener(behavior),
    )
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "connection_failed"
    assert SAMPLE not in failure["error"]
    assert SAMPLE not in failure["next_step"]


@pytest.mark.parametrize("url", [
    "https://169.254.169.254/latest/meta-data",
    "https://0.0.0.0/data.json",
    "https://192.0.2.1/data.json",
    "http://api.census.gov/data.json",
    "https://api.census.gov/data/2024/acs/acs5/variables/B01003_001E.json?key=abc",
    "https://tigerweb.geo.census.gov/arcgis?key=abc",
    "https://user:pw@api.census.gov/data.json",
    "https://evil.example/data.json",
])
def test_row4_destinations_outside_the_pin_make_no_request(url):
    opened = {"n": 0}

    def factory():
        opened["n"] += 1
        return _Opener(lambda *_a: _Resp(b"{}"))

    result = fetch(url, timeout=5, max_bytes=100, opener_factory=factory)
    assert result.kind == "host_refused"
    assert opened["n"] == 0
    assert check_destination(url) is False


def test_a_key_on_the_data_path_is_allowed_only_there():
    assert check_destination("https://api.census.gov/data/2024/acs/acs5?get=NAME&for=state:48&key=abc") is True
    assert check_destination("https://api.census.gov/data.json") is True
    assert check_destination("https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress?format=json") is True


@pytest.mark.parametrize("name", [
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
    "all_proxy",
    "ftp_proxy",
])
def test_proxy_variables_are_unused(monkeypatch, name):
    monkeypatch.setenv(name, "http://192.0.2.1:8080")
    opener = plugin.http_client._opener()
    # An empty ProxyHandler is not installed, so the env proxy is not used.
    for handler in opener.handlers:
        proxies = getattr(handler, "proxies", None) or {}
        assert proxies == {}
        assert "192.0.2.1" not in str(proxies)
    assert any(isinstance(handler, plugin.http_client._NoRedirect) for handler in opener.handlers)


def test_user_agent_is_the_plugin_and_the_opener_is_used():
    seen = {}

    def behavior(req, _timeout):
        seen["ua"] = req.get_header("User-agent")
        return _Resp(b'{"dataset":[]}')

    result = fetch(
        "https://api.census.gov/data.json",
        timeout=5,
        max_bytes=100,
        opener_factory=lambda: _Opener(behavior),
    )
    assert result.kind == "ok"
    assert seen["ua"] == plugin.hosts.USER_AGENT


def test_no_redirect_handler_raises_instead_of_returning_a_new_url():
    handler = plugin.http_client._NoRedirect()
    with pytest.raises(plugin.http_client.RedirectError):
        handler.redirect_request(None, None, 302, "moved", {}, "https://evil.example/")


def test_invalid_key_redirect_is_a_key_sentence_and_is_not_fetched():
    seen = {"n": 0}

    def behavior(_req, _timeout):
        seen["n"] += 1
        raise plugin.http_client.RedirectError(302, "https://api.census.gov/data/invalid_key.html")

    result = fetch(
        "https://api.census.gov/data/2024/acs/acs5?get=NAME&for=state:48&key=" + SAMPLE,
        timeout=5,
        max_bytes=1000,
        opener_factory=lambda: _Opener(behavior),
    )
    assert seen["n"] == 1
    assert result.kind == "invalid_key_redirect"
    assert result.body == b""
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "invalid_key"
    assert "Retry later" not in failure["next_step"]
    assert plugin.errors.KEY_URL in failure["error"]
    assert SAMPLE not in failure["error"]
    assert SAMPLE not in failure["next_step"]
    assert "invalid_key.html" not in failure["error"]


def test_missing_key_redirect_does_not_claim_an_http_200_title():
    def behavior(_req, _timeout):
        raise plugin.http_client.RedirectError(302, "https://api.census.gov/data/missing_key.html")

    result = fetch(
        "https://api.census.gov/data/2024/acs/acs5?get=NAME&for=state:48",
        timeout=5,
        max_bytes=1000,
        opener_factory=lambda: _Opener(behavior),
    )
    assert result.kind == "missing_key_redirect"
    assert result.body == b""
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "missing_key"
    assert "HTTP 200" not in failure["error"]
    assert "Retry later" not in failure["next_step"]
    assert SAMPLE not in failure["error"]


def test_a_redirect_to_another_host_stays_a_redirect():
    def behavior(_req, _timeout):
        raise plugin.http_client.RedirectError(302, "https://evil.example/data/invalid_key.html")

    result = fetch(
        "https://api.census.gov/data/2024/acs/acs5?get=NAME&for=state:48&key=" + SAMPLE,
        timeout=5,
        max_bytes=1000,
        opener_factory=lambda: _Opener(behavior),
    )
    assert result.kind == "redirect"
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "redirect"
    assert SAMPLE not in failure["error"]


def test_invalid_key_html_drops_the_page():
    page = b"<html><title>Invalid Key</title>" + SAMPLE.encode("ascii") + b"</html>"

    def behavior(_req, _timeout):
        return _Resp(page, content_type="text/html")

    result = fetch(
        "https://api.census.gov/data/2024/acs/acs5",
        timeout=5,
        max_bytes=1000,
        opener_factory=lambda: _Opener(behavior),
    )
    assert result.kind == "invalid_key_html"
    assert result.body == b""
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "invalid_key"
    assert "HTTP 200" in failure["error"]
    assert SAMPLE not in failure["error"]


def test_404_names_an_unlisted_path_and_5xx_says_retry():
    missing = plugin.http_client.as_failure(plugin.http_client.HttpResult("http_status", status=404))
    assert missing["error_type"] == "http_status"
    assert "Retry later" not in missing["next_step"]
    assert "404" in missing["error"]
    refused = plugin.http_client.as_failure(plugin.http_client.HttpResult("http_status", status=401))
    assert "Retry later" not in refused["next_step"]
    later = plugin.http_client.as_failure(plugin.http_client.HttpResult("http_status", status=503))
    assert "Retry later" in later["next_step"]


def _open(body, status=200, content_type="application/json"):
    def behavior(_req, _timeout):
        return _Resp(body, status=status, content_type=content_type)

    return lambda: _Opener(behavior)


def test_response_rows_cover_every_class():
    """Each RESPONSE_ROWS class. An unnamed status stays a retry and is not a table."""
    seen = set()
    for row in plugin.http_client.RESPONSE_ROWS:
        status, shape, pclass, kind, error_type, error, next_step = row
        assert plugin.http_client.response_row(status, shape, pclass) == row
        seen.add((status, shape, pclass))
        if kind == "empty_table":
            result = plugin.http_client.HttpResult(
                "empty_table", status=status, body=b"", shape=shape, path_class=pclass,
            )
            assert plugin.http_client.as_failure(result) is None
            assert "Retry later" not in next_step
        elif error_type is not None:
            result = plugin.http_client.HttpResult(
                "http_status", status=status, body=b"", shape=shape, path_class=pclass,
            )
            failure = plugin.http_client.as_failure(result)
            assert failure["error_type"] == error_type
            assert failure["error"] == error
            assert failure["next_step"] == next_step
            if status < 500 and status not in (408, 429):
                assert "Retry later" not in failure["next_step"]
    assert len(seen) == len(plugin.http_client.RESPONSE_ROWS)
    unnamed = plugin.http_client.response_row(418, "bytes", "data")
    assert unnamed not in plugin.http_client.RESPONSE_ROWS
    assert unnamed[3] == "http_status"
    assert "Retry later" in unnamed[6]
    failure = plugin.http_client.as_failure(plugin.http_client.HttpResult("http_status", status=418))
    assert "Retry later" in failure["next_step"]


def test_fetch_204_empty_on_a_data_url_is_an_empty_table():
    result = fetch(
        "https://api.census.gov/data/2024/acs/acs1?get=NAME,B01003_001E&for=county:301&in=state:48",
        timeout=5,
        max_bytes=1000,
        opener_factory=_open(b"", status=204, content_type="application/json"),
    )
    assert result.kind == "empty_table"
    assert result.status == 204
    assert result.body == b""
    assert result.path_class == "data"
    assert plugin.http_client.as_failure(result) is None


def test_fetch_204_empty_on_a_document_is_not_a_data_row():
    result = fetch(
        "https://api.census.gov/data/2026/acs/acs5.json",
        timeout=5,
        max_bytes=1000,
        opener_factory=_open(b"", status=204),
    )
    assert result.kind == "http_status"
    assert result.path_class == "other"
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "http_status"
    assert "Retry later" not in failure["next_step"]
    assert "no document" in failure["next_step"]


def test_fetch_204_with_a_body_drops_the_body():
    result = fetch(
        "https://api.census.gov/data/2024/acs/acs5?get=NAME&for=state:48",
        timeout=5,
        max_bytes=1000,
        opener_factory=_open(b'{"population": 1}', status=204),
    )
    assert result.kind == "http_status"
    assert result.body == b""
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "unexpected_body"
    assert "population" not in failure["error"]
    assert "population" not in failure["next_step"]
    assert "Retry later" not in failure["next_step"]


def test_fetch_201_is_not_a_retry():
    result = fetch(
        "https://api.census.gov/data/2024/acs/acs5?get=NAME&for=state:48",
        timeout=5,
        max_bytes=1000,
        opener_factory=_open(b"", status=201),
    )
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "http_status"
    assert "201" in failure["error"]
    assert "Retry later" not in failure["next_step"]


def test_fetch_400_unknown_variable_names_the_id_and_hides_the_body():
    import io

    page = b"error: unknown variable 'B99999_001E'"

    def behavior(_req, _timeout):
        raise urllib.error.HTTPError(
            "https://api.census.gov/data/2024/acs/acs5",
            400,
            "bad",
            hdrs={"Content-Type": "text/plain"},
            fp=io.BytesIO(page),
        )

    result = fetch(
        "https://api.census.gov/data/2024/acs/acs5?get=NAME,B99999_001E&for=state:48",
        timeout=5,
        max_bytes=1000,
        opener_factory=lambda: _Opener(behavior),
    )
    assert result.body == b""
    assert result.shape == "unknown_variable"
    failure = plugin.http_client.as_failure(result)
    assert failure["error_type"] == "http_status"
    assert "B99999_001E" not in failure["error"]
    assert "unknown variable" not in failure["error"]
    assert "key" not in failure["next_step"]
    assert "Retry later" not in failure["next_step"]
    assert "unlisted" in failure["next_step"]
