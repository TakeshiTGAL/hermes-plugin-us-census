"""ACS vintage probe. The first run stores the listed year and prints nothing."""

from __future__ import annotations

import datetime
import json
import os
import stat
import sys
from pathlib import Path

from .acs import descriptor_url, parse_descriptor
from .hosts import DATASETS, YEAR_MIN
from .http_client import HttpResult, fetch as live_fetch
from .safe_write import WriteRefused, write_bytes

WINDOW = 6
STATE_NAME = "vintage.json"
STATE_CAP = 100_000

# One row per probe status. discover and the stored-plus-one check share this table.
# use keeps the year. continue tries an older year. stop reports failure.
WALK_RESULTS = (
    ("available", "use"),
    ("unavailable", "continue"),
    ("other_product", "continue"),
    ("missing", "continue"),
    ("error", "stop"),
)


def walk_action(status: str) -> str:
    """Return the WALK_RESULTS action. An unknown status reports failure."""
    for name, action in WALK_RESULTS:
        if name == status:
            return action
    return "stop"


def calendar_year(now=None) -> int:
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return int(moment.year)


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        chosen = os.environ.get("HERMES_HOME", "").strip()
        if chosen:
            return Path(chosen)
        return Path.home() / ".hermes"


def data_root() -> Path:
    return hermes_home() / "plugin-data" / "us-census"


def probe_year(fetch, dataset: str, year: int) -> str:
    """Return a WALK_RESULTS status for this descriptor. An error does not try an older year."""
    result = fetch(descriptor_url(dataset, year), cap="descriptor")
    if isinstance(result, HttpResult) and result.kind == "http_status" and result.status == 404:
        return "missing"
    if not isinstance(result, HttpResult) or result.kind != "ok" or result.truncated:
        return "error"
    return parse_descriptor(result.body, year, dataset)


def listed_status(fetch, dataset: str, year: int) -> str:
    """Return the descriptor status. A sibling product row is not the detailed-table year."""
    return probe_year(fetch, dataset, year)


def discover(fetch, dataset: str, year: int):
    """Return (year or None, failed). A failed probe stops the walk and is not an empty window."""
    for offset in range(WINDOW):
        candidate = year - offset
        if candidate < YEAR_MIN:
            break
        action = walk_action(listed_status(fetch, dataset, candidate))
        if action == "stop":
            return None, True
        if action == "use":
            return candidate, False
    return None, False


def run_watch(fetch, state, year: int):
    """Return exit code, stdout text, and a new state or None when the file stays as it is."""
    if state is None:
        found = {}
        for dataset in DATASETS:
            listed, failed = discover(fetch, dataset, year)
            if failed or listed is None:
                if failed:
                    return 1, "The ACS vintage probe failed, so no year was stored.\n", None
                return 1, "No ACS vintage in the probe window was listed, so nothing was stored.\n", None
            found[dataset] = listed
        return 0, "", found
    lines = []
    updated = dict(state)
    changed = False
    for dataset in DATASETS:
        stored = state.get(dataset)
        if not isinstance(stored, int):
            return 1, "The stored vintage file could not be read, so no year was checked.\n", None
        action = walk_action(listed_status(fetch, dataset, stored + 1))
        if action == "stop":
            return 1, "The ACS vintage probe failed, so the stored year was left as it is.\n", None
        if action == "use":
            updated[dataset] = stored + 1
            changed = True
            lines.append(
                "ACS " + dataset + " vintage " + str(stored + 1) + " is listed. The stored vintage was " + str(stored) + "."
            )
    if not changed:
        return 0, "", None
    return 0, "\n".join(lines) + "\n", updated


def load_state(root: Path):
    """Return (state, problem). A missing file is a baseline, not an error."""
    path = root / STATE_NAME
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "The vintage file could not be read, so no year was checked."
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None, "The vintage file is not a regular file, so it was not read."
    if info.st_size > STATE_CAP:
        return None, "The vintage file is larger than the cap, so it was not read."
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "The stored vintage file could not be read, so no year was checked."
    if not isinstance(data, dict):
        return None, "The stored vintage file could not be read, so no year was checked."
    cleaned = {}
    for dataset in DATASETS:
        value = data.get(dataset)
        if isinstance(value, bool) or not isinstance(value, int):
            return None, "The stored vintage file could not be read, so no year was checked."
        cleaned[dataset] = value
    return cleaned, None


def save_state(root: Path, state: dict) -> None:
    payload = json.dumps({dataset: state[dataset] for dataset in DATASETS}, sort_keys=True).encode("utf-8")
    write_bytes(root, STATE_NAME, payload, create=True)


def script_main() -> int:
    """Zero with empty stdout is a quiet morning. Any other outcome exits non-zero.

    ``--no-agent`` treats a non-zero exit as a failed run and keeps stderr in that
    record. Deliver local does not post the failure to chat. A zero exit with empty
    stdout stays silent.
    """
    root = data_root()
    state, problem = load_state(root)
    if problem:
        sys.stderr.write(problem + "\n")
        return 1

    def fetch(url, cap="descriptor"):
        from .hosts import CAP_DESCRIPTOR
        return live_fetch(url, timeout=20, max_bytes=CAP_DESCRIPTOR)

    code, text, new_state = run_watch(fetch, state, calendar_year())
    if new_state is not None:
        try:
            save_state(root, new_state)
        except (WriteRefused, OSError):
            sys.stderr.write("The vintage file was not written.\n")
            if text:
                sys.stderr.write(text)
            return 1
    if code != 0:
        if text:
            sys.stderr.write(text)
        return code
    if text:
        sys.stdout.write(text)
    return 0
