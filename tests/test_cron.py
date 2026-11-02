"""Model-free vintage cron. Empty stdout stays quiet. Deliver stays local."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from tests.support import ROOT, descriptor, http_status, ok_body, plugin

_load = __import__("importlib").import_module
public_cron = _load("us_census_plugin.public_cron")
vintage = _load("us_census_plugin.vintage")
cli = _load("us_census_plugin.cli")
HttpResult = plugin.http_client.HttpResult
JOB = public_cron.JOB_NAME


def _listed(jobs):
    lines = []
    for job in jobs:
        lines.append("  " + job["id"] + " [local]")
        lines.append("    Name: " + job["name"])
    return public_cron.Cmd(0, "\n".join(lines) + "\n", "")


class Runner:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        head = args[0] if args else ""
        if head == "list":
            return _listed(self.jobs)
        if head == "create":
            return public_cron.Cmd(0, "Created job: job-new\n", "")
        if head == "edit":
            return public_cron.Cmd(0, "Updated\n", "")
        if head == "remove":
            return public_cron.Cmd(0, "Removed\n", "")
        return public_cron.Cmd(1, "", "unexpected")


def _store(jobs):
    runner = Runner(jobs)
    return public_cron.Store(run=runner), runner


def _fetch_log(replies):
    seen = []

    def fetch(url, cap="descriptor", timeout=20):
        seen.append(url)
        for needle, result in replies:
            if needle in url:
                return result
        return http_status(404)

    return fetch, seen


def test_baseline_is_quiet_and_probes_both_products():
    fetch, seen = _fetch_log([
        ("/data/2024/acs/acs5.json", ok_body(descriptor(2024, dataset="acs/acs5"))),
        ("/data/2024/acs/acs1.json", ok_body(descriptor(2024, dataset="acs/acs1"))),
    ])
    code, text, state = vintage.run_watch(fetch, None, 2026)
    assert code == 0
    assert text == ""
    assert state == {"acs/acs5": 2024, "acs/acs1": 2024}
    assert any("/acs/acs5" in url for url in seen)
    assert any("/acs/acs1" in url for url in seen)
    assert all("key=" not in url for url in seen)


def test_stored_plus_one_404_is_empty_stdout():
    fetch, seen = _fetch_log([])
    state = {"acs/acs5": 2024, "acs/acs1": 2024}
    code, text, new_state = vintage.run_watch(fetch, state, 2026)
    assert code == 0
    assert text == ""
    assert new_state is None
    assert len(seen) == 2
    assert all("/data/2025/" in url for url in seen)


def test_probe_500_does_not_walk_down_or_store():
    fetch, seen = _fetch_log([
        ("/data/2025/", HttpResult("http_status", status=500, body=b"")),
        ("/data/2024/", ok_body(descriptor(2024))),
    ])
    state = {"acs/acs5": 2024, "acs/acs1": 2024}
    code, text, new_state = vintage.run_watch(fetch, state, 2026)
    assert code == 1
    assert "left as it is" in text
    assert new_state is None
    assert len(seen) == 1
    assert "/data/2025/acs/acs5.json" in seen[0]


def test_truncated_descriptor_is_an_error_and_not_an_older_year():
    fetch, seen = _fetch_log([
        ("/data/2025/acs/acs5.json", HttpResult("ok", 200, b'{"dataset":[]}', "application/json", True)),
        ("/data/2024/", ok_body(descriptor(2024))),
    ])
    state = {"acs/acs5": 2024, "acs/acs1": 2024}
    code, _text, new_state = vintage.run_watch(fetch, state, 2026)
    assert code == 1
    assert new_state is None
    assert len(seen) == 1


def test_missing_state_file_is_the_baseline(tmp_path):
    state, problem = vintage.load_state(tmp_path)
    assert state is None
    assert problem is None


def test_corrupt_or_bool_state_is_not_a_quiet_baseline(tmp_path):
    root = tmp_path / "plugin-data" / "us-census"
    root.mkdir(parents=True)
    path = root / "vintage.json"
    path.write_text("{", encoding="utf-8")
    state, problem = vintage.load_state(root)
    assert state is None
    assert problem
    path.write_text(json.dumps({"acs/acs5": True, "acs/acs1": 2024}), encoding="utf-8")
    state, problem = vintage.load_state(root)
    assert state is None
    assert "could not be read" in problem


def test_symlink_state_is_not_read(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"acs/acs5": 2024, "acs/acs1": 2024}), encoding="utf-8")
    (root / "vintage.json").symlink_to(outside)
    state, problem = vintage.load_state(root)
    assert state is None
    assert "regular file" in problem
    try:
        vintage.save_state(root, {"acs/acs5": 2024, "acs/acs1": 2025})
    except plugin.safe_write.WriteRefused:
        pass
    else:
        raise AssertionError("symlink write")
    assert json.loads(outside.read_text(encoding="utf-8"))["acs/acs1"] == 2024


def test_script_main_baseline_then_404_stays_quiet(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vintage, "data_root", lambda: tmp_path)
    monkeypatch.setattr(vintage, "calendar_year", lambda now=None: 2026)

    def fetch(url, timeout=20, max_bytes=1):
        if url.endswith("/2024/acs/acs5.json"):
            return ok_body(descriptor(2024, dataset="acs/acs5"))
        if url.endswith("/2024/acs/acs1.json"):
            return ok_body(descriptor(2024, dataset="acs/acs1"))
        return http_status(404)

    monkeypatch.setattr(vintage, "live_fetch", fetch)
    assert vintage.script_main() == 0
    assert capsys.readouterr().out == ""
    stored = json.loads((tmp_path / "vintage.json").read_text(encoding="utf-8"))
    assert stored == {"acs/acs1": 2024, "acs/acs5": 2024}
    assert vintage.script_main() == 0
    assert capsys.readouterr().out == ""
    assert json.loads((tmp_path / "vintage.json").read_text(encoding="utf-8")) == stored


def test_script_main_500_keeps_the_file(monkeypatch, tmp_path, capsys):
    root_file = tmp_path / "vintage.json"
    vintage.save_state(tmp_path, {"acs/acs5": 2024, "acs/acs1": 2024})
    before = root_file.read_bytes()
    monkeypatch.setattr(vintage, "data_root", lambda: tmp_path)
    monkeypatch.setattr(vintage, "calendar_year", lambda now=None: 2026)

    def fetch(url, timeout=20, max_bytes=1):
        return HttpResult("http_status", status=500, body=b"")

    monkeypatch.setattr(vintage, "live_fetch", fetch)
    assert vintage.script_main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "left as it is" in captured.err
    assert root_file.read_bytes() == before


def test_script_main_new_vintage_prints_one_stdout_line(monkeypatch, tmp_path, capsys):
    vintage.save_state(tmp_path, {"acs/acs5": 2024, "acs/acs1": 2024})
    monkeypatch.setattr(vintage, "data_root", lambda: tmp_path)
    monkeypatch.setattr(vintage, "calendar_year", lambda now=None: 2026)

    def fetch(url, timeout=20, max_bytes=1):
        if url.endswith("/2025/acs/acs5.json"):
            return ok_body(descriptor(2025))
        return http_status(404)

    monkeypatch.setattr(vintage, "live_fetch", fetch)
    assert vintage.script_main() == 0
    captured = capsys.readouterr()
    assert captured.out == "ACS acs/acs5 vintage 2025 is listed. The stored vintage was 2024.\n"
    assert captured.err == ""
    stored = json.loads((tmp_path / "vintage.json").read_text(encoding="utf-8"))
    assert stored == {"acs/acs1": 2024, "acs/acs5": 2025}


def test_script_main_write_refusal_leaves_stdout_empty(monkeypatch, tmp_path, capsys):
    vintage.save_state(tmp_path, {"acs/acs5": 2024, "acs/acs1": 2024})
    before = (tmp_path / "vintage.json").read_bytes()
    monkeypatch.setattr(vintage, "data_root", lambda: tmp_path)
    monkeypatch.setattr(vintage, "calendar_year", lambda now=None: 2026)

    def fetch(url, timeout=20, max_bytes=1):
        if url.endswith("/2025/acs/acs5.json"):
            return ok_body(descriptor(2025))
        return http_status(404)

    def refuse(_root, _state):
        raise plugin.safe_write.WriteRefused("symlink", "vintage.json")

    monkeypatch.setattr(vintage, "live_fetch", fetch)
    monkeypatch.setattr(vintage, "save_state", refuse)
    assert vintage.script_main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "was not written" in captured.err
    assert "vintage 2025 is listed" in captured.err
    assert (tmp_path / "vintage.json").read_bytes() == before


def test_script_main_corrupt_file_exits_nonzero(monkeypatch, tmp_path, capsys):
    (tmp_path / "vintage.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(vintage, "data_root", lambda: tmp_path)

    def fetch(*_args, **_kwargs):
        raise AssertionError("corrupt file fetch")

    monkeypatch.setattr(vintage, "live_fetch", fetch)
    assert vintage.script_main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not be read" in captured.err


def test_status_prints_the_file_and_does_not_fetch(monkeypatch, tmp_path):
    def fetch(*_args, **_kwargs):
        raise AssertionError("status fetch")

    monkeypatch.setattr(vintage, "live_fetch", fetch)
    store, runner = _store([{"id": "job-a", "name": JOB}])
    code, text = public_cron.status(store, tmp_path)
    assert code == 0
    assert "job job-a" in text
    assert "No vintage file" in text
    assert runner.calls == [["list", "--all"]]
    vintage.save_state(tmp_path / "plugin-data" / "us-census", {"acs/acs5": 2024, "acs/acs1": 2025})
    code, text = public_cron.status(store, tmp_path)
    assert code == 0
    assert "acs/acs5 vintage 2024" in text
    assert "acs/acs1 vintage 2025" in text


def test_status_corrupt_file_is_nonzero(tmp_path):
    root = tmp_path / "plugin-data" / "us-census"
    root.mkdir(parents=True)
    (root / "vintage.json").write_text("not-json", encoding="utf-8")
    store, _runner = _store([])
    code, text = public_cron.status(store, tmp_path)
    assert code == 1
    assert "could not be read" in text


def test_other_deliver_is_refused_before_list(tmp_path):
    store, runner = _store([])
    code, text = public_cron.schedule(store, tmp_path, tmp_path, "slack")
    assert code == 1
    assert "locally" in text
    assert runner.calls == []
    assert not (tmp_path / "scripts").exists()


def test_two_jobs_change_nothing_and_skip_the_script(tmp_path):
    store, runner = _store([
        {"id": "a", "name": JOB},
        {"id": "b", "name": JOB},
    ])
    code, text = public_cron.schedule(store, tmp_path, ROOT, "local")
    assert code == 1
    assert "More than one" in text
    assert runner.calls == [["list", "--all"]]
    assert not (tmp_path / "scripts" / public_cron.SCRIPT_NAME).exists()


def test_one_job_is_edited_and_the_script_stays_keyless(tmp_path):
    store, runner = _store([{"id": "job-a", "name": JOB}])
    code, text = public_cron.schedule(store, tmp_path, ROOT, "local")
    assert code == 0
    assert "Updated" in text
    assert "fresh ticker heartbeat" in text
    assert "0 8 * * *" not in text
    assert runner.calls[0] == ["list", "--all"]
    assert runner.calls[1][0] == "edit"
    assert "create" not in [item[0] for item in runner.calls]
    script = (tmp_path / "scripts" / public_cron.SCRIPT_NAME).read_text(encoding="utf-8")
    assert "cron.jobs" not in script
    assert "CENSUS_API_KEY" not in script
    assert "import cron" not in script
    assert public_cron.SCHEDULE == "0 8 * * *"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills" / "us-census" / "SKILL.md").read_text(encoding="utf-8")
    assert "0 8 * * *" not in readme
    assert "0 8 * * *" not in skill


def test_unschedule_after_the_job_is_gone_removes_the_script_and_says_so(tmp_path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / public_cron.SCRIPT_NAME
    script.write_text("print('stay')\n", encoding="utf-8")
    store, runner = _store([])
    code, text = public_cron.unschedule(store, tmp_path)
    assert code == 0
    assert text == (
        "No cron job named " + JOB + " is listed. Removed the script. "
        "No vintage file is stored."
    )
    assert not script.exists()
    assert runner.calls == [["list", "--all"]]
    again, quiet = public_cron.unschedule(store, tmp_path)
    assert again == 0
    assert quiet == "No cron job named " + JOB + " is listed."


def test_unschedule_names_a_stored_vintage_file(tmp_path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / public_cron.SCRIPT_NAME
    script.write_text("print('stay')\n", encoding="utf-8")
    vintage_dir = tmp_path / "plugin-data" / "us-census"
    vintage_dir.mkdir(parents=True)
    stored = vintage_dir / "vintage.json"
    stored.write_text("{}\n", encoding="utf-8")
    store, runner = _store([{"id": "job-a", "name": JOB}])
    code, text = public_cron.unschedule(store, tmp_path)
    assert code == 0
    assert text == (
        "Removed " + JOB + " and the script. "
        "The vintage file remains until you delete it."
    )
    assert stored.is_file()
    assert not script.exists()
    assert ["remove", "job-a"] in runner.calls
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    store, runner = _store([])
    code, text = public_cron.schedule(store, link, ROOT, "local")
    assert code == 1
    assert "The vintage script was not written." in text
    assert "symlink" in text
    assert runner.calls == [["list", "--all"]]
    assert list(outside.iterdir()) == []


def test_schedule_under_tmp_names_the_symlink_refusal():
    root = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        store, runner = _store([])
        code, text = public_cron.schedule(store, root, ROOT, "local")
        assert code == 1
        assert "The vintage script was not written." in text
        assert "symlink" in text
        assert "real directory" in text
        assert not (root / "scripts" / public_cron.SCRIPT_NAME).exists()
        assert runner.calls == [["list", "--all"]]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_job_is_created_once(tmp_path):
    store, runner = _store([])
    code, text = public_cron.schedule(store, tmp_path, ROOT, "local")
    assert code == 0
    assert "Scheduled" in text
    assert runner.calls[1][0] == "create"
    assert "local" in runner.calls[1]
    assert "--no-agent" in runner.calls[1]
    assert runner.calls[1].count("create") == 1


def test_cli_has_no_profile_flag_and_refuses_deliver(monkeypatch, tmp_path, capsys):
    source = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert "--profile" not in source
    assert '"-p"' not in source
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    try:
        parser.parse_args(["schedule", "-p", "work"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("profile flag")
    runner = Runner([])
    monkeypatch.setattr(cli, "hermes_home", lambda: tmp_path)
    monkeypatch.setattr(cli, "Store", lambda: public_cron.Store(run=runner))
    code = cli.handle(argparse.Namespace(command="schedule", deliver="origin"))
    assert code == 1
    assert runner.calls == []
    assert "locally" in capsys.readouterr().out


def test_generated_watch_and_vintage_module_do_not_read_the_key():
    script = public_cron.script_text(ROOT)
    vintage = (ROOT / "vintage.py").read_text(encoding="utf-8")
    cron = (ROOT / "public_cron.py").read_text(encoding="utf-8")
    assert "cron.jobs" not in script
    assert "cron.jobs" not in vintage
    assert "cron.jobs" not in cron
    assert "CENSUS_API_KEY" not in script
    assert "CENSUS_API_KEY" not in vintage
    assert "create_job(" not in cron


def _doc(year, parts, available=True, link=None):
    row = {
        "c_vintage": year,
        "c_isAvailable": available,
        "c_dataset": parts,
        "title": "ACS",
    }
    if link:
        row["c_variablesLink"] = link
    return {"dataset": [row]}


def test_descriptor_identity_covers_every_product_shape():
    acs = plugin.acs
    link = acs.variables_link_for("acs/acs1", 2024)
    rows = (
        ("exact_available", _doc(2024, ["acs", "acs1"]), 2024, "acs/acs1", "available"),
        ("exact_with_link", _doc(2024, ["acs", "acs1"], link=link), 2024, "acs/acs1", "available"),
        ("unavailable", _doc(2024, ["acs", "acs5"], available=False), 2024, "acs/acs5", "unavailable"),
        ("cprofile", _doc(2025, ["acs", "acs1", "cprofile"]), 2025, "acs/acs1", "other_product"),
        ("subject", _doc(2024, ["acs", "acs5", "subject"]), 2024, "acs/acs5", "other_product"),
        ("profile", _doc(2024, ["acs", "acs1", "profile"]), 2024, "acs/acs1", "other_product"),
        ("spp", _doc(2024, ["acs", "acs5", "spp"]), 2024, "acs/acs5", "other_product"),
        ("link_leaves_the_path", _doc(2024, ["acs", "acs1"], link=link.replace("/variables.json", "/cprofile/variables.json")), 2024, "acs/acs1", "other_product"),
        ("vintage_mismatch", _doc(2023, ["acs", "acs1"]), 2024, "acs/acs1", "error"),
        ("missing_dataset", {"dataset": [{"c_vintage": 2024, "c_isAvailable": True}]}, 2024, "acs/acs1", "error"),
        ("available_null", {"dataset": [{"c_vintage": 2024, "c_isAvailable": None, "c_dataset": ["acs", "acs1"]}]}, 2024, "acs/acs1", "error"),
        ("broken_json", None, 2024, "acs/acs1", "error"),
    )
    seen = set()
    for name, payload, year, dataset, status in rows:
        if payload is None:
            body = b"{"
        else:
            body = json.dumps(payload).encode("utf-8")
        got = acs.parse_descriptor(body, year, dataset)
        assert got == status, name
        seen.add(got)
    assert seen == set(acs.DESCRIPTOR_RESULTS)


def test_walk_reaction_covers_every_descriptor_status():
    """Each WALK_RESULTS row. A comparison profile is not the detailed-table year."""
    actions = dict(vintage.WALK_RESULTS)
    assert actions == {
        "available": "use",
        "unavailable": "continue",
        "other_product": "continue",
        "missing": "continue",
        "error": "stop",
    }
    assert set(actions) == set(plugin.acs.DESCRIPTOR_RESULTS) | {"missing"}
    assert vintage.walk_action("unlisted") == "stop"

    fetch, seen = _fetch_log([
        ("/data/2024/acs/acs5.json", ok_body(descriptor(2024))),
    ])
    year, failed = vintage.discover(fetch, "acs/acs5", 2024)
    assert (year, failed) == (2024, False)
    assert not any("examples.json" in url for url in seen)

    fetch, seen = _fetch_log([
        ("/data/2025/acs/acs5.json", ok_body(descriptor(2025, available=False))),
        ("/data/2024/acs/acs5.json", ok_body(descriptor(2024))),
    ])
    year, failed = vintage.discover(fetch, "acs/acs5", 2025)
    assert (year, failed) == (2024, False)
    assert len(seen) == 2

    cprofile = _doc(
        2025,
        ["acs", "acs1", "cprofile"],
        link="https://api.census.gov/data/2025/acs/acs1/cprofile/variables.json",
    )
    fetch, seen = _fetch_log([
        ("/data/2025/acs/acs1.json", ok_body(cprofile)),
        ("/data/2025/acs/acs1/examples.json", ok_body({"get": ["NAME", "B01001_001E"]})),
        ("/data/2024/acs/acs1.json", ok_body(descriptor(2024, dataset="acs/acs1"))),
    ])
    year, failed = vintage.discover(fetch, "acs/acs1", 2025)
    assert (year, failed) == (2024, False)
    assert not any("examples.json" in url for url in seen)

    fetch, seen = _fetch_log([
        ("/data/2025/acs/acs1.json", ok_body(cprofile)),
        ("/data/2025/acs/acs1/examples.json", ok_body({"get": ["NAME", "B01001_001E"]})),
    ])
    year, failed = vintage.discover(fetch, "acs/acs1", 2025)
    assert (year, failed) == (None, False)
    assert len(seen) == vintage.WINDOW
    assert not any("examples.json" in url for url in seen)

    fetch, seen = _fetch_log([
        ("/data/2024/acs/acs5.json", ok_body(descriptor(2024))),
    ])
    year, failed = vintage.discover(fetch, "acs/acs5", 2025)
    assert (year, failed) == (2024, False)
    assert "/data/2025/acs/acs5.json" in seen[0]

    fetch, seen = _fetch_log([
        ("/data/2025/acs/acs5.json", HttpResult("http_status", status=500, body=b"")),
        ("/data/2024/acs/acs5.json", ok_body(descriptor(2024))),
    ])
    year, failed = vintage.discover(fetch, "acs/acs5", 2025)
    assert (year, failed) == (None, True)
    assert all("/2024/" not in url for url in seen)

    fetch, seen = _fetch_log([
        ("/data/2025/acs/acs5.json", HttpResult("http_status", status=204, body=b"", shape="empty", path_class="other")),
        ("/data/2024/acs/acs5.json", ok_body(descriptor(2024))),
    ])
    year, failed = vintage.discover(fetch, "acs/acs5", 2025)
    assert (year, failed) == (None, True)
    assert all("/2024/" not in url for url in seen)

    fetch, seen = _fetch_log([
        ("/data/2025/acs/acs5.json", http_status(404)),
        ("/data/2025/acs/acs1.json", ok_body(cprofile)),
        ("/data/2025/acs/acs1/examples.json", ok_body({"get": ["NAME", "B01001_001E"]})),
    ])
    code, text, new_state = vintage.run_watch(
        fetch, {"acs/acs5": 2024, "acs/acs1": 2024}, 2026,
    )
    assert code == 0
    assert text == ""
    assert new_state is None
    assert not any("examples.json" in url for url in seen)


def _family(year, dataset, extra, exact_first=False, exact_available=True, link=None):
    exact_link = link if link is not None else plugin.acs.variables_link_for(dataset, year)
    sibling = {
        "c_vintage": year,
        "c_isAvailable": True,
        "c_dataset": dataset.split("/") + [extra],
        "c_variablesLink": "https://api.census.gov/data/" + str(year) + "/" + dataset + "/" + extra + "/variables.json",
        "title": extra,
    }
    exact = {
        "c_vintage": year,
        "c_isAvailable": exact_available,
        "c_dataset": dataset.split("/"),
        "c_variablesLink": exact_link,
        "title": "ACS Detailed Tables",
    }
    rows = [exact, sibling] if exact_first else [sibling, exact]
    return {
        "@id": "https://api.census.gov/data/" + str(year) + "/" + dataset + "/" + extra + ".json",
        "dataset": rows,
    }


def test_document_rules_cover_every_row():
    """Every DOCUMENT_RULES row. The detailed-tables row decides, wherever it sits."""
    acs = plugin.acs
    link = acs.variables_link_for("acs/acs5", 2024)
    cprofile_first = _family(2024, "acs/acs5", "cprofile")
    exact_first = _family(2024, "acs/acs5", "cprofile", exact_first=True)
    cases = {
        "bad_row": (
            {"dataset": [
                {"c_vintage": 2024, "c_isAvailable": True, "c_dataset": ["acs", "acs5", "cprofile"]},
                {"c_vintage": 2023, "c_isAvailable": True, "c_dataset": ["acs", "acs5"], "c_variablesLink": link},
            ]},
            2024,
            "acs/acs5",
            "error",
        ),
        "mixed": (
            {"dataset": [
                {"c_vintage": 2024, "c_isAvailable": True, "c_dataset": ["acs", "acs5"], "c_variablesLink": link},
                {"c_vintage": 2024, "c_isAvailable": False, "c_dataset": ["acs", "acs5"], "c_variablesLink": link},
            ]},
            2024,
            "acs/acs5",
            "error",
        ),
        "available": (cprofile_first, 2024, "acs/acs5", "available"),
        "unavailable": (_family(2024, "acs/acs5", "cprofile", exact_available=False), 2024, "acs/acs5", "unavailable"),
        "bad_link": (
            _family(2024, "acs/acs5", "subject", link=link.replace("/variables.json", "/cprofile/variables.json")),
            2024,
            "acs/acs5",
            "other_product",
        ),
        "sibling": (
            {"@id": "https://api.census.gov/data/2025/acs/acs1/cprofile.json", "dataset": [
                {"c_vintage": 2025, "c_isAvailable": True, "c_dataset": ["acs", "acs1", "cprofile"]},
                {"c_vintage": 2025, "c_isAvailable": True, "c_dataset": ["acs", "acs1", "profile"]},
                {"c_vintage": 2025, "c_isAvailable": True, "c_dataset": ["acs", "acs1", "spp"]},
                {"c_vintage": 2025, "c_isAvailable": True, "c_dataset": ["acs", "acs1", "subject"]},
                {"c_vintage": 2025, "c_isAvailable": True, "c_dataset": ["acs", "acs1", "pums"]},
            ]},
            2025,
            "acs/acs1",
            "other_product",
        ),
        "none": ({"dataset": ["nope", 3]}, 2024, "acs/acs5", "error"),
    }
    assert set(cases) == {name for name, _status in acs.DOCUMENT_RULES}
    for name, (payload, year, dataset, status) in cases.items():
        got = acs.parse_descriptor(json.dumps(payload).encode("utf-8"), year, dataset)
        assert got == status, name
    assert acs.parse_descriptor(json.dumps(exact_first).encode("utf-8"), 2024, "acs/acs5") == "available"
    assert acs.parse_descriptor(json.dumps(_family(2023, "acs/acs5", "pums")).encode("utf-8"), 2023, "acs/acs5") == "available"
    dirty = {"dataset": [
        "nope",
        {"c_vintage": 2024, "c_isAvailable": True, "c_dataset": ["acs", "acs5"], "c_variablesLink": link},
    ]}
    assert acs.parse_descriptor(json.dumps(dirty).encode("utf-8"), 2024, "acs/acs5") == "available"


def test_family_document_stores_the_detailed_row():
    """A comparison profile in front of the detailed tables is still that vintage."""
    fetch, seen = _fetch_log([
        ("/data/2026/acs/acs5.json", http_status(404)),
        ("/data/2025/acs/acs5.json", http_status(404)),
        ("/data/2024/acs/acs5.json", ok_body(_family(2024, "acs/acs5", "cprofile"))),
        ("/data/2023/acs/acs5.json", ok_body(descriptor(2023))),
        ("/data/2026/acs/acs1.json", http_status(404)),
        ("/data/2025/acs/acs1.json", ok_body(_family(2025, "acs/acs1", "cprofile"))),
        ("/data/2024/acs/acs1.json", ok_body(descriptor(2024, dataset="acs/acs1"))),
    ])
    code, text, state = vintage.run_watch(fetch, None, 2026)
    assert code == 0
    assert text == ""
    assert state == {"acs/acs5": 2024, "acs/acs1": 2025}
    assert not any("/2023/" in url for url in seen)
    assert not any("examples.json" in url for url in seen)
    code, text, new_state = vintage.run_watch(fetch, state, 2026)
    assert code == 0
    assert text == ""
    assert new_state is None
