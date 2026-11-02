"""Rows 1 through 15 of the build table. Each test name is the row it locks."""

from __future__ import annotations

import re

from tests.support import ROOT, World, austin_row, make_service, ok_body, place_routes, plugin

_SENTENCE_END = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<!\betc)[.!?](?=\s|$)")
_JP = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def _first_sentence(text):
    match = _SENTENCE_END.search(text.strip())
    return text.strip()[: match.end()] if match else text.strip()


def _public_texts():
    paths = [ROOT / "README.md", ROOT / "plugin.yaml", ROOT / "NOTICE", ROOT / "LICENSE"]
    paths.extend((ROOT).rglob("*.py"))
    paths.extend((ROOT / "skills").rglob("*.md"))
    texts = []
    for path in paths:
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        if path.is_file():
            texts.append((path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")))
    return texts


def _fingerprint(root):
    rows = []
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        rows.append((path.relative_to(root).as_posix(), path.read_bytes()))
    return rows


def test_row1_truncated_catalog_and_zero_matches_are_not_a_full_negative():
    world = World()
    world.add("/data.json", plugin.http_client.HttpResult("ok", 200, b'{"dataset":[]}', "application/json", True))
    result = make_service(world).datasets({})
    assert result["complete"] is False
    assert result["ok"] is False
    assert "total" not in result
    empty = World()
    place_routes(empty, [])
    missed = make_service(empty).place({"place": "NoSuchPlaceXYZ, TX", "year": 2024})
    assert missed["estimates_read"] is False
    assert missed["match_count"] == 0
    assert missed["sentence"] == "No geography matched that name, so no estimate was read."
    assert "figures" not in missed


def test_row2_a_timeout_phrase_stays_a_timeout():
    kind = plugin.http_client._transport_kind
    assert kind(__import__("urllib.error", fromlist=["URLError"]).URLError("connection refused, Query timed out after 1s")) == "timeout"
    failure = plugin.http_client.as_failure(plugin.http_client.HttpResult("timeout"))
    assert failure["error_type"] == "timeout"
    assert failure["error_type"] != "connection_failed"


def test_row4_allowlist_refuses_before_http():
    world = World()
    svc = make_service(world)
    refused = [
        svc.get({"state_fips": "48", "variables": ["B01003_001E"], "url": "https://example.com"}),
        svc.get({"state_fips": "*", "variables": ["B01003_001E"], "year": 2024}),
        svc.datasets({"dataset": "pep/charage"}),
        svc.variables({"query": "B01003_001E", "dataset": "dec/sf1", "year": 2024}),
    ]
    assert all(item["error_type"] == "bad_args" and item["ok"] is False for item in refused)
    assert world.calls == []


def test_row5_read_tools_leave_the_disk_fingerprint(tmp_path, monkeypatch):
    vintage = __import__("importlib").import_module("us_census_plugin.vintage")
    monkeypatch.setattr(vintage, "hermes_home", lambda: tmp_path)
    before = _fingerprint(ROOT)
    home_before = _fingerprint(tmp_path)
    original = plugin.tools.SERVICE
    plugin.tools.SERVICE = make_service(World())
    try:
        for name in (
            "census_datasets",
            "census_geographies",
            "census_variables",
            "census_place",
            "census_get",
            "census_compare",
        ):
            raw = plugin.tools.HANDLERS[name]({})
            assert "census-test-key-should-not-leak" not in raw
    finally:
        plugin.tools.SERVICE = original
    world = World()
    place_routes(world, [austin_row()])
    world.add("/data.json", ok_body({"dataset": []}))
    svc = make_service(world)
    svc.datasets({"query": ""})
    svc.place({"place": "Austin, TX", "year": 2024})
    assert _fingerprint(ROOT) == before
    assert _fingerprint(tmp_path) == home_before


def test_row6_skill_writes_the_target_before_the_cron_cli():
    skill = (ROOT / "skills" / "us-census" / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Before you run `hermes us-census schedule`, write the target" in skill
    assert "us-census-vintage.py" in skill
    assert "no approval card" in skill
    assert "Before you run `hermes us-census schedule`, write the target" in readme
    assert "There is no approval card." in readme
    assert "while the plugin is still enabled" in readme
    assert "while the plugin is still enabled" in skill
    assert "hermes cron remove" in readme
    assert "hermes cron remove" in skill
    assert "until you unschedule" not in readme
    assert "until you unschedule" not in skill
    assert "20 seconds" in readme
    assert "45 seconds" in readme
    assert "12 requests" in readme
    assert "invalid_key" in skill
    assert "fresh ticker heartbeat" in readme
    assert "fresh ticker heartbeat" in skill
    assert "Creating the job does not start a gateway." in readme
    assert "Creating the job does not start a gateway." in skill
    assert "HTTP 204" in readme
    assert "HTTP 204" in skill


def test_row7_readme_has_both_thirty_second_scenes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert 'export HERMES_HOME="$(mktemp -d)"' in readme
    assert 'export HOME="$(mktemp -d)/user"' in readme
    assert "unset HERMES_HOME" in readme
    assert readme.count("hermes plugins install --no-enable") == 2
    assert readme.count("hermes plugins enable us-census") == 2
    assert readme.count('HANDLERS["census_variables"]') == 2
    assert "B01003_001E" in readme


def test_attribution_and_guest_disclosure_are_public():
    sentence = "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau."
    for name in ("README.md", "NOTICE", "plugin.yaml"):
        assert sentence in (ROOT / name).read_text(encoding="utf-8")
    skill = (ROOT / "skills" / "us-census" / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert sentence in skill
    assert "keeps no user list" in readme
    assert "guest in a room" in readme
    assert "exits non-zero" in readme
    assert "exits non-zero" in skill
    schemas = __import__("importlib").import_module("us_census_plugin.schemas")
    for schema in schemas.ALL_SCHEMAS:
        assert sentence in schema["description"]


def test_row8_ordinary_requests_are_in_the_skill():
    skill = (ROOT / "skills" / "us-census" / "SKILL.md").read_text(encoding="utf-8")
    for phrase in (
        "ACS population and margin of error",
        "census median household income",
        "FIPS for a US place",
    ):
        assert phrase in skill


def test_row9_failure_kinds_stay_distinct():
    kinds = {
        "timeout": "timeout",
        "connection_failed": "connection_failed",
        "redirect": "redirect",
        "host_refused": "host_refused",
        "html": "unexpected_body",
        "http_status": "http_status",
    }
    seen = set()
    for kind, expected in kinds.items():
        world = World()
        world.add("/data.json", plugin.http_client.HttpResult(kind, status=503, body=b""))
        result = make_service(world).datasets({})
        assert result["error_type"] == expected
        assert result["next_step"]
        assert result["estimates_read"] is False
        seen.add(result["error_type"])
    assert len(seen) == len(kinds)


def test_row10_skill_and_first_sentences_fit():
    text = plugin.SKILL_DESCRIPTION
    assert len(text) <= 223
    assert "tool_search" in text
    assert "Before web_search" in text
    assert ": " not in text
    front = (ROOT / "skills" / "us-census" / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    assert "description: " + text in front or text in front
    schemas = __import__("importlib").import_module("us_census_plugin.schemas")
    for schema in schemas.ALL_SCHEMAS:
        sentence = _first_sentence(schema["description"])
        assert len(sentence) <= 60
        assert not sentence.lower().startswith("read-only")
        assert len(schema["description"]) <= 1024


def test_row11_manifest_has_no_requires_env():
    text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert "requires_env" not in text
    assert 'requires_hermes: ">=0.21.4"' in text
    assert "python_dependencies: []" in text


def test_row12_tools_do_not_write_and_chat_deliver_is_refused(tmp_path):
    assert "register_hook" not in (ROOT / "__init__.py").read_text(encoding="utf-8")
    public_cron = __import__("importlib").import_module("us_census_plugin.public_cron")
    code, text = public_cron.schedule(
        public_cron.Store(run=lambda args: public_cron.Cmd(0, "", "")),
        tmp_path,
        ROOT,
        "origin",
    )
    assert code == 1
    assert "locally" in text
    assert not (tmp_path / "scripts").exists()


def test_row13_public_files_have_no_japanese():
    hits = []
    for name, text in _public_texts():
        for line in text.splitlines():
            if _JP.search(line):
                hits.append(name)
                break
    assert hits == []


def test_row14_package_load_registers_six_tools_offline():
    class Ctx:
        def __init__(self):
            self.tools = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs["name"])

        def register_cli_command(self, **_kwargs):
            return None

        def register_skill(self, name, path, description=""):
            assert name == "us-census"
            assert path.is_file()
            assert len(description) <= 223

    ctx = Ctx()
    plugin.register(ctx)
    assert ctx.tools == [
        "census_datasets",
        "census_geographies",
        "census_variables",
        "census_place",
        "census_get",
        "census_compare",
    ]
    assert set(plugin.tools.HANDLERS) == set(ctx.tools)


def test_row15_no_unpinned_downloader():
    blob = "\n".join(text for _name, text in _public_texts())
    for token in ("uvx ", "npx ", "pip install", "subprocess.run([\"pip\"", "curl "):
        assert token not in blob
    assert "python_dependencies: []" in (ROOT / "plugin.yaml").read_text(encoding="utf-8")
