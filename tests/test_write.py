"""safe_write refuses links, escape, and a swapped destination."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from tests.support import plugin

write_bytes = plugin.safe_write.write_bytes
WriteRefused = plugin.safe_write.WriteRefused
HARD_LINK_CUT = plugin.safe_write.HARD_LINK_CUT


def test_tmp_symlink_home_is_refused():
    root = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        assert str(root).startswith("/tmp/")
        with pytest.raises(WriteRefused) as caught:
            write_bytes(root, "vintage.json", b"{}", create=True)
        assert caught.value.kind == "symlink"
        assert not (root / "vintage.json").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_var_temp_directory_can_hold_a_regular_file():
    root = Path(tempfile.mkdtemp())
    outside = Path(tempfile.mkdtemp())
    try:
        assert str(root).startswith("/var/")
        assert write_bytes(root, "vintage.json", b"{}", create=True) == ""
        assert (root / "vintage.json").read_bytes() == b"{}"
        assert not (root / "vintage.json").is_symlink()
        link = root / "escape"
        link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(WriteRefused) as caught:
            write_bytes(link, "vintage.json", b"{}", create=True)
        assert caught.value.kind == "symlink"
        assert list(outside.iterdir()) == []
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_row3_symlink_parent_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(WriteRefused) as caught:
        write_bytes(link, "vintage.json", b"{}", create=True)
    assert caught.value.kind == "symlink"
    assert list(outside.rglob("*")) == []


def test_row3_symlink_file_is_not_followed(tmp_path):
    target = tmp_path / "target.json"
    target.write_bytes(b"ORIGINAL")
    dest = tmp_path / "vintage.json"
    dest.symlink_to(target)
    with pytest.raises(WriteRefused) as caught:
        write_bytes(tmp_path, "vintage.json", b"{}", create=True)
    assert caught.value.kind == "symlink"
    assert target.read_bytes() == b"ORIGINAL"
    assert dest.is_symlink()


def test_row3_dotdot_is_refused(tmp_path):
    with pytest.raises(WriteRefused) as caught:
        write_bytes(tmp_path, "../escape.json", b"{}", create=True)
    assert caught.value.kind == "outside"
    assert not (tmp_path.parent / "escape.json").exists()


def test_row3_hard_link_can_be_cut_without_changing_the_other_name(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"ORIGINAL-HARD")
    dest = tmp_path / "vintage.json"
    os.link(outside, dest)
    note = write_bytes(tmp_path, "vintage.json", b"NEW-BYTES", create=True)
    assert note == HARD_LINK_CUT
    assert outside.read_bytes() == b"ORIGINAL-HARD"
    assert dest.read_bytes() == b"NEW-BYTES"
    assert outside.stat().st_ino != dest.stat().st_ino


def test_row3_hard_link_can_be_refused(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"ORIGINAL-HARD")
    dest = tmp_path / "vintage.json"
    os.link(outside, dest)
    with pytest.raises(WriteRefused) as caught:
        write_bytes(tmp_path, "vintage.json", b"NEW-BYTES", create=True, exclusive=True, cut_hardlink=False)
    assert caught.value.kind == "hardlink"
    assert outside.read_bytes() == b"ORIGINAL-HARD"


def test_row3_two_writers_leave_one_regular_file(tmp_path):
    payloads = (b'{"acs/acs5":2024}', b'{"acs/acs5":2023}')
    errors = []

    def write(payload):
        try:
            write_bytes(tmp_path, "vintage.json", payload, create=True)
        except Exception as exc:  # noqa: BLE001 — the assertion is the type name only
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=write, args=(payload,)) for payload in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    body = (tmp_path / "vintage.json").read_bytes()
    assert body in payloads
    assert (tmp_path / "vintage.json").stat().st_nlink == 1
    assert list(tmp_path.glob(".rewrite.*")) == []


def test_row3_swapping_the_name_for_a_symlink_does_not_write_outside(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"KEEP")
    folder = tmp_path / "box"
    folder.mkdir()
    dest = folder / "vintage.json"
    dest.symlink_to(outside)
    with pytest.raises(WriteRefused):
        write_bytes(folder, "vintage.json", b"CHANGED", create=True)
    assert outside.read_bytes() == b"KEEP"
