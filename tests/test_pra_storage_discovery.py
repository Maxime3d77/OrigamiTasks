"""Regression: PVE storage discovery uses pvesh, never nonexistent 'pvesm config'."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HOST_MODULES = Path(__file__).resolve().parents[1] / "agents" / "linux"
sys.path.insert(0, str(HOST_MODULES))
path = HOST_MODULES / "pra_host_worker.py"
spec = importlib.util.spec_from_file_location("pra_host_worker", path)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = {"type": "dir", "path": str(tmp_path), "content": "images,iso"}
    invoked = []
    def fake(*args, **kwargs):
        invoked.append(args)
        assert args == ("pvesh", "get", "/storage/remote-store", "--output-format", "json")
        return json.dumps(data)
    monkeypatch.setattr(worker, "cmd", fake)
    return data, invoked, tmp_path


def test_pvesh_discovers_dir_by_id_and_creates_only_vm_checkpoint_folder(store):
    data, invoked, path = store
    result = worker._dir_path("remote-store", 134)
    assert result == path / "images" / "134"
    assert result.is_dir()
    assert invoked == [("pvesh", "get", "/storage/remote-store", "--output-format", "json")]


@pytest.mark.parametrize("changes", [
    {"type": "lvm"},
    {"content": "iso,backup"},
    {"disable": 1},
    {"path": "relative/path"},
])
def test_invalid_destination_rejected_before_any_directory_is_created(store, changes):
    data, invoked, path = store
    data.update(changes)
    with pytest.raises(worker.HostError):
        worker._dir_path("remote-store", 134)
    assert not (path / "images").exists()


def test_explicit_mount_guard_rejects_unmounted_storage(store, monkeypatch):
    data, _, path = store
    data["is_mountpoint"] = 1
    monkeypatch.setattr(worker.os.path, "ismount", lambda _: False)
    with pytest.raises(worker.HostError, match="not mounted"):
        worker._dir_path("remote-store", 134)
    assert not (path / "images").exists()


def test_zero_string_mount_guard_does_not_trigger_mount_check(store, monkeypatch):
    data, _, path = store
    data["is_mountpoint"] = "0"
    monkeypatch.setattr(worker.os.path, "ismount",
                        lambda _: (_ for _ in ()).throw(AssertionError("unnecessary check")))
    assert worker._dir_path("remote-store", 134).is_dir()


def test_invalid_json_refused(store, monkeypatch):
    _, _, path = store
    monkeypatch.setattr(worker, "cmd", lambda *args: "not-json")
    with pytest.raises(worker.HostError, match="Malformed"):
        worker._dir_path("remote-store", 134)
    assert not (path / "images").exists()


def test_storage_identifier_is_never_interpolated_as_shell(store):
    with pytest.raises(worker.HostError, match="storage name"):
        worker._dir_path("remote-store; id", 134)
