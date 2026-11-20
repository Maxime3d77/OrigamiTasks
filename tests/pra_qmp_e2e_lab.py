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
from pra_qmp_incremental import QMP, Disk, backup


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
def qemu_source(root, disk):
    qmp_socket = root / "test-qmp.sock"
    args = [
        "qemu-system-x86_64", "-nodefaults", "-machine", "none", "-S",
        "-display", "none", "-monitor", "none", "-accel", "tcg",
        "-qmp", f"unix:{qmp_socket},server=on,wait=off",
        "-blockdev", f"driver=file,node-name=src-file,filename={disk}",
        "-blockdev", "driver=qcow2,node-name=drive-scsi0,file=src-file",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait(lambda: qmp_socket.exists() or proc.poll() is not None)
        if proc.poll() is not None:
            raise RuntimeError("QEMU failed to start: " +
                               proc.stderr.read().decode(errors="replace")[-2000:])
        with QMP(str(qmp_socket)) as qmp:
            names = [item.get("node-name") for item in qmp.execute("query-named-block-nodes")]
            if "drive-scsi0" not in names:
                raise RuntimeError("QMP graph missing explicit source node: " + repr(names))
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
        src, seed, delta = [root / name for name in
                            ("source.qcow2", "seed.qcow2", "delta.qcow2")]
        run("qemu-img", "create", "-f", "qcow2", str(src), str(size))
        run("qemu-io", "-f", "qcow2", "-c", "write -P 0x17 0 64k", str(src))
        with qemu_source(root, src) as qmp:
            fullbitmap = "origami_106_scsi0"
            run("qemu-img", "create", "-f", "qcow2", str(seed), str(size))
            with nbd(seed) as nbd_port:
                result = backup(qmp, [
                    Disk(source="drive-scsi0", bitmap=fullbitmap,
                         host="127.0.0.1", port=nbd_port)
                ], "full", timeout=35)
                assert result["qmp_jobs_completed"] is True
                assert not result["standby_vm_started"]

            run("qemu-img", "check", "-f", "qcow2", str(seed))
            run("qemu-io", "-f", "qcow2", "-c", "read -P 0x17 0 64k", str(seed))
            # Test writes through the running QEMU process to ensure the
            # dirty bitmap sees actual source changes.
            response = qmp.execute("human-monitor-command", {
                "command-line": 'qemu-io drive-scsi0 "write -P 0x38 128k 64k"'
            })
            if isinstance(response, str) and "error" in response.lower():
                raise AssertionError("QEMU failed simulated guest write: " + response)

            run("qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
                "-b", str(seed), str(delta))
            with nbd(delta) as nbd_port:
                result = backup(qmp, [
                    Disk(source="drive-scsi0", bitmap=fullbitmap,
                         host="127.0.0.1", port=nbd_port)
                ], "incremental", timeout=35)
                assert result["qmp_jobs_completed"] is True

        run("qemu-img", "check", "-f", "qcow2", str(seed))
        run("qemu-img", "check", "-f", "qcow2", str(delta))
        run("qemu-io", "-f", "qcow2", "-c", "read -P 0x17 0 64k", str(delta))
        run("qemu-io", "-f", "qcow2", "-c", "read -P 0x38 128k 64k", str(delta))
        # Historical generation is immutable.
        run("qemu-io", "-f", "qcow2", "-c", "read -P 0x00 128k 64k", str(seed))
        info = json.loads(run("qemu-img", "info", "--backing-chain", "--output=json", str(delta)))
        assert len(info) == 2, info
        assert Path(info[1]["filename"]) == seed
        print("PASS: real QMP full+incremental over loopback NBD, QCOW2 chain, immutable base")
        print("NOTE: QEMU process is a synthetic fixture; no PVE, guest OS, fencing or failover tested")


if __name__ == "__main__":
    main()
