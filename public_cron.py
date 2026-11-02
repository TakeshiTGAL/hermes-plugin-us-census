"""Talk to cron through the public hermes cron command.

The plugin does not import the cron job store. Schedule delivers locally.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .safe_write import WriteRefused, write_bytes
from .vintage import hermes_home

CRON_TIMEOUT_SECONDS = 60
JOB_NAME = "us-census-vintage"
SCRIPT_NAME = "us-census-vintage.py"
SCHEDULE = "0 8 * * *"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CREATED = re.compile(r"Created job:\s*(\S+)")
_ID_LINE = re.compile(r"^  (\S+) \[")
_NAME = re.compile(r"^\s+Name:\s*(.*)$")

_SCRIPT = """# US Census vintage watch. Empty stdout stays quiet.
import importlib.util
import sys
from pathlib import Path

PLUGIN = Path(PLUGIN_PATH)
spec = importlib.util.spec_from_file_location(
    "us_census_plugin",
    str(PLUGIN / "__init__.py"),
    submodule_search_locations=[str(PLUGIN)],
)
module = importlib.util.module_from_spec(spec)
sys.modules["us_census_plugin"] = module
spec.loader.exec_module(module)
raise SystemExit(module.vintage.script_main())
"""


class Cmd:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def plain(text: str) -> str:
    return _ANSI.sub("", text or "")


def first_line(done: Cmd) -> str:
    text = plain(done.stderr or done.stdout).strip()
    line = text.splitlines()[0].strip() if text else ""
    return line[:180]


def hermes_binary() -> str | None:
    sibling = os.path.join(os.path.dirname(sys.executable), "hermes")
    if os.path.isfile(sibling) and os.access(sibling, os.X_OK):
        return sibling
    return shutil.which("hermes")


def default_run(args: list) -> Cmd:
    """Run hermes cron. copy() keeps PYTHONPATH for the child."""
    try:
        home = str(hermes_home())
    except (OSError, RuntimeError):
        return Cmd(127, "", "The Hermes home could not be resolved, so hermes cron was not started.")
    if not home:
        return Cmd(127, "", "The Hermes home could not be resolved, so hermes cron was not started.")
    binary = hermes_binary()
    if not binary:
        return Cmd(127, "", "The hermes command was not found, so hermes cron was not started.")
    env = os.environ.copy()
    env["HERMES_HOME"] = home
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    try:
        done = subprocess.run(
            [binary, "cron", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=CRON_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return Cmd(124, "", "hermes cron timed out, so the command was not left running.")
    except OSError:
        return Cmd(127, "", "Could not run hermes.")
    return Cmd(int(done.returncode), done.stdout or "", done.stderr or "")


def parse_created_id(stdout: str):
    match = _CREATED.search(plain(stdout))
    return match.group(1) if match else None


def parse_listed_jobs(stdout: str) -> list:
    jobs = []
    current = None
    for line in plain(stdout).splitlines():
        head = _ID_LINE.match(line)
        if head:
            if current is not None:
                jobs.append(current)
            current = {"id": head.group(1), "name": ""}
            continue
        if current is None:
            continue
        name = _NAME.match(line)
        if name:
            current["name"] = name.group(1).strip()
    if current is not None:
        jobs.append(current)
    return jobs


def script_text(plugin_dir: Path) -> str:
    return _SCRIPT.replace("PLUGIN_PATH", repr(str(plugin_dir)))


def write_script(home: Path, plugin_dir: Path) -> None:
    write_bytes(home / "scripts", SCRIPT_NAME, script_text(plugin_dir).encode("utf-8"), create=True)


class Store:
    def __init__(self, run=None) -> None:
        self._run = run or default_run

    def load_jobs(self) -> list:
        done = self._run(["list", "--all"])
        if done.returncode != 0:
            raise RuntimeError(first_line(done) or "hermes cron list failed")
        return parse_listed_jobs(done.stdout)

    def add_job(self, *, schedule: str, name: str, deliver: str, script: str) -> dict:
        if deliver != "local":
            raise RuntimeError("This schedule delivers locally and does not post to chat.")
        args = ["create", schedule, "--name", name, "--deliver", deliver, "--no-agent", "--script", script]
        done = self._run(args)
        if done.returncode != 0:
            raise RuntimeError(first_line(done) or "hermes cron schedule failed")
        job_id = parse_created_id(done.stdout)
        if not job_id:
            raise RuntimeError("hermes cron schedule finished without a job id")
        return {"id": job_id, "name": name, "schedule": schedule, "deliver": deliver, "script": script}

    def edit_job(self, job_id: str, *, schedule: str, deliver: str, script: str) -> bool:
        if deliver != "local":
            raise RuntimeError("This schedule delivers locally and does not post to chat.")
        args = ["edit", str(job_id), "--schedule", schedule, "--deliver", deliver, "--no-agent", "--script", script]
        done = self._run(args)
        return done.returncode == 0

    def remove_job(self, job_id: str) -> bool:
        done = self._run(["remove", str(job_id)])
        return done.returncode == 0


def schedule(store: Store, home: Path, plugin_dir: Path, deliver: str):
    if deliver != "local":
        return 1, "This schedule delivers locally and does not post to chat."
    try:
        jobs = store.load_jobs()
    except RuntimeError as exc:
        return 1, str(exc) or "hermes cron list failed"
    named = [job for job in jobs if job.get("name") == JOB_NAME]
    if len(named) > 1:
        return 1, "More than one cron job is named " + JOB_NAME + ", so nothing was changed."
    try:
        write_script(home, plugin_dir)
    except WriteRefused as exc:
        if exc.kind == "symlink":
            return 1, (
                "The vintage script was not written. "
                "A symlink in the Hermes home path was refused. "
                "Point HERMES_HOME at a real directory."
            )
        return 1, "The vintage script was not written."
    except OSError:
        return 1, "The vintage script was not written."
    if len(named) == 1:
        ok = store.edit_job(named[0]["id"], schedule=SCHEDULE, deliver=deliver, script=SCRIPT_NAME)
        if not ok:
            return 1, "The existing cron job was not updated."
        return 0, "Updated " + JOB_NAME + " (" + named[0]["id"] + "). Deliver is local. The job fires only when a gateway serves this profile or a fresh ticker heartbeat is present. Empty stdout on a zero exit sends nothing. A non-zero exit marks the run failed."
    try:
        created = store.add_job(schedule=SCHEDULE, name=JOB_NAME, deliver=deliver, script=SCRIPT_NAME)
    except RuntimeError as exc:
        return 1, str(exc) or "The cron job was not created."
    return 0, "Scheduled " + JOB_NAME + " (" + created["id"] + "). Deliver is local. The job fires only when a gateway serves this profile or a fresh ticker heartbeat is present. Empty stdout on a zero exit sends nothing. A non-zero exit marks the run failed."


def unschedule(store: Store, home: Path):
    try:
        jobs = store.load_jobs()
    except RuntimeError as exc:
        return 1, str(exc) or "hermes cron list failed"
    named = [job for job in jobs if job.get("name") == JOB_NAME]
    if len(named) > 1:
        return 1, "More than one cron job is named " + JOB_NAME + ", so nothing was removed."
    if len(named) == 1 and not store.remove_job(named[0]["id"]):
        return 1, "The cron job was not removed."
    script = home / "scripts" / SCRIPT_NAME
    removed_script = False
    if script.exists() and not script.is_symlink():
        try:
            script.unlink()
            removed_script = True
        except OSError:
            if named:
                return 1, "The cron job was removed and the script file remains."
            return 1, "No cron job named " + JOB_NAME + " is listed. The script file remains."
    if not named and removed_script:
        return 0, "No cron job named " + JOB_NAME + " is listed. Removed the script." + _vintage_clause(home)
    if not named:
        return 0, "No cron job named " + JOB_NAME + " is listed."
    return 0, "Removed " + JOB_NAME + " and the script." + _vintage_clause(home)


def _vintage_clause(home: Path) -> str:
    """Say the vintage file remains only when that file is already on disk."""
    path = home / "plugin-data" / "us-census" / "vintage.json"
    try:
        stored = path.is_file() and not path.is_symlink()
    except OSError:
        return " The vintage file was not checked."
    if stored:
        return " The vintage file remains until you delete it."
    return " No vintage file is stored."


def status(store: Store, home: Path):
    try:
        jobs = store.load_jobs()
    except RuntimeError as exc:
        return 1, str(exc) or "hermes cron list failed, so the job list was not read as empty."
    named = [job for job in jobs if job.get("name") == JOB_NAME]
    from .vintage import load_state
    state, problem = load_state(home / "plugin-data" / "us-census")
    lines = []
    if not named:
        lines.append("No cron job named " + JOB_NAME + " is listed.")
    else:
        for job in named:
            lines.append("job " + job.get("id", "") + " name " + JOB_NAME)
    if problem:
        lines.append(problem)
    elif state is None:
        lines.append("No vintage file is stored.")
    else:
        for dataset, year in state.items():
            lines.append(dataset + " vintage " + str(year))
    return (1 if problem else 0), "\n".join(lines)
