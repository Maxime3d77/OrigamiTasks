#!/usr/bin/env python3
"""Real QEMU/QMP/NBD integration test on *temporary local images*.

This test intentionally runs on a disposable GitHub Actions runner or lab
machine, NOT on an existing production Proxmox node. No VMID, PVE API, SAN,
or physical storage is accessed. Full and incremental copies exercise the
actual agents/linux/pra_qmp_incremental.py data plane, then verify the COW
disk images using qemu-img/qemu-io.

Requires: qemu-system-x86_64, qemu-img, qemu-io, qemu-nbd.
"""
import contextlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "agents" / "linux"))
from pra_qmp_incremental import QMP, Disk, ReplicationError, backup
from pra_checkpoint import CheckpointError, CheckpointStore


def run(*args, timeout=120):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise AssertionError("Command failed: " + " ".join(map(str, args)) +
                             "\n" + p.stderr[-3000:] + "\n" + p.stdout[-3000:])
    return p.stdout


def wait(predicate, *, timeout=20):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.10)
    raise RuntimeError("QEMU fixture did not become ready")


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def qemu_source(root, disks):
    qmp_socket = root / "test-qmp.sock"
    args = [
        "qemu-system-x86_64", "-nodefaults", "-machine", "none", "-S",
        "-display", "none", "-monitor", "none", "-accel", "tcg",
        "-qmp", f"unix:{qmp_socket},server=on,wait=off",
    ]
    for slot, disk in sorted(disks.items()):
        args.extend(["-blockdev", f"driver=file,node-name=file-{slot},filename={disk}",
                     "-blockdev", f"driver=qcow2,node-name=drive-{slot},file=file-{slot}"])
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait(lambda: qmp_socket.exists() or proc.poll() is not None)
        if proc.poll() is not None:
            raise RuntimeError("QEMU failed to start: " +
                               proc.stderr.read().decode(errors="replace")[-2000:])
        with QMP(str(qmp_socket)) as qmp:
            names = [item.get("node-name") for item in qmp.execute("query-named-block-nodes")]
            expected = {"drive-" + slot for slot in disks}
            if not expected.issubset(set(names)):
                raise RuntimeError("QMP graph missing source nodes: " + repr(names))
            yield qmp
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()
        qmp_socket.unlink(missing_ok=True)

@contextlib.contextmanager
def nbd(image):
    nbd_port = port()
    pid = image.parent / (image.stem + ".pid")
    run("qemu-nbd", "--format=qcow2", "--fork", "--persistent",
        "--bind=127.0.0.1", f"--port={nbd_port}", f"--pid-file={pid}", str(image))
    try:
        def ready():
            with socket.socket() as s:
                s.settimeout(.5)
                return s.connect_ex(("127.0.0.1", nbd_port)) == 0
        wait(ready)
        yield nbd_port
    finally:
        if pid.exists():
            value = int(pid.read_text().strip())
            if value > 1:
                os.kill(value, signal.SIGTERM)
                for _ in range(100):
                    try:
                        os.kill(value, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(.05)
        pid.unlink(missing_ok=True)


def main():
    for executable in ("qemu-system-x86_64", "qemu-img", "qemu-nbd", "qemu-io"):
        if shutil.which(executable) is None:
            raise RuntimeError("Missing required executable: " + executable)
    with tempfile.TemporaryDirectory(prefix="origamitasks-qmp-e2e-") as path:
        root = Path(path)
        size = 64 * 1024 * 1024
        slots = ("scsi0", "scsi1")
        sources = {slot: root / (slot + "-source.qcow2") for slot in slots}
        checkpoint_root = root / "target-images"
        checkpoint_root.mkdir()
        checkpoint = CheckpointStore(checkpoint_root, target_vmid=321)
        generation = "lab-vm-generation-1"
        sizes = {slot: size for slot in slots}
        bitmap = {slot: "origami_106_" + slot for slot in slots}
        for index, slot in enumerate(slots):
            run("qemu-img", "create", "-f", "qcow2", str(sources[slot]), str(size))
            run("qemu-io", "-f", "qcow2", "-c",
                f"write -P {0x17 + index} 0 64k", str(sources[slot]))

        with qemu_source(root, sources) as qmp:
            staged = checkpoint.begin("full", sizes, generation)
            assert checkpoint.current() is None
            seed_token = staged["token"]
            seeds = {slot: checkpoint_root / staged["disks"][slot]["file"]
                     for slot in slots}
            with contextlib.ExitStack() as stack:
                ports = {slot: stack.enter_context(nbd(seeds[slot])) for slot in slots}
                result = backup(qmp, [
                    Disk(source="drive-" + slot, bitmap=bitmap[slot],
                         host="127.0.0.1", port=ports[slot])
                    for slot in slots
                ], "full", timeout=35)
                assert result["qmp_jobs_completed"] is True
                assert result["disks"] == 2 and not result["standby_vm_started"]
            committed = checkpoint.commit(seed_token)
            assert committed["status"] == "committed"
            assert checkpoint.current()["token"] == seed_token
            assert not checkpoint.pending.exists()

            for index, slot in enumerate(slots):
                run("qemu-img", "check", "-f", "qcow2", str(seeds[slot]))
                run("qemu-io", "-f", "qcow2", "-c",
                    f"read -P {0x17 + index} 0 64k", str(seeds[slot]))
                response = qmp.execute("human-monitor-command", {
                    "command-line": f'qemu-io drive-{slot} "write -P {0x38 + index} 128k 64k"'
                })
                if isinstance(response, str) and "error" in response.lower():
                    raise AssertionError("QEMU failed simulated guest write: " + response)

            staged_delta = checkpoint.begin("incremental", sizes, generation)
            delta_token = staged_delta["token"]
            assert checkpoint.current()["token"] == seed_token
            deltas = {slot: checkpoint_root / staged_delta["disks"][slot]["file"]
                      for slot in slots}
            with contextlib.ExitStack() as stack:
                ports = {slot: stack.enter_context(nbd(deltas[slot])) for slot in slots}
                result = backup(qmp, [
                    Disk(source="drive-" + slot, bitmap=bitmap[slot],
                         host="127.0.0.1", port=ports[slot])
                    for slot in slots
                ], "incremental", timeout=35)
                assert result["qmp_jobs_completed"] is True
            checkpoint.commit(delta_token)
            assert checkpoint.current()["token"] == delta_token
            assert checkpoint.current()["previous_token"] == seed_token
            assert checkpoint.current()["depth"] == 2
            assert not checkpoint.pending.exists()

        for index, slot in enumerate(slots):
            seed, delta = seeds[slot], deltas[slot]
            run("qemu-img", "check", "-f", "qcow2", str(seed))
            run("qemu-img", "check", "-f", "qcow2", str(delta))
            run("qemu-io", "-f", "qcow2", "-c",
                f"read -P {0x17 + index} 0 64k", str(delta))
            run("qemu-io", "-f", "qcow2", "-c",
                f"read -P {0x38 + index} 128k 64k", str(delta))
            # The already-committed seed stays immutable.
            run("qemu-io", "-f", "qcow2", "-c",
                "read -P 0x00 128k 64k", str(seed))
            chain = json.loads(run("qemu-img", "info", "--backing-chain",
                                   "--output=json", str(delta)))
            assert len(chain) == 2 and Path(chain[1]["filename"]) == seed

        # The target checkpoint manifest is durable and old generations are
        # retained; QEMU image verification is not a guest boot validation.
        assert len(checkpoint.owned.read_text()) > 0
        try:
            checkpoint.garbage_collect()
        except CheckpointError as exc:
            assert "disabled" in str(exc)
        else:
            raise AssertionError("Unverified checkpoint GC unexpectedly enabled")

        # QEMU restart invalidates every deliberately non-persistent bitmap;
        # an incremental request MUST fail before opening any NBD target.
        with qemu_source(root, sources) as restarted:
            try:
                backup(restarted, [Disk(source="drive-scsi0", bitmap=bitmap["scsi0"],
                                        host="127.0.0.1", port=1)],
                       "incremental", timeout=5)
            except ReplicationError as exc:
                if "FULL reseed required" not in str(exc):
                    raise
            else:
                raise AssertionError("Incremental unexpectedly accepted after source restart")
        print("PASS: real 2-disk QMP full+incremental NBD with CheckpointStore atomic commit")
        print("PASS: bitmap loss after source restart correctly forces FULL reseed")
        print("NOTE: synthetic QEMU process, NOT Proxmox multi-node recovery/fencing")


if __name__ == "__main__":
    main()
