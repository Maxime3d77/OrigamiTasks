#!/usr/bin/env python3
"""Real QCOW2 backing chains on *throwaway loop block devices*.

Requires root (CI runner sudo), losetup and qemu-utils. This is NOT a
Proxmox LVM or production storage test: loop devices only emulate the
block-device semantics. No existing LV, VM, SAN or real user data is opened.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


def run(*args, timeout=40):
    out = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError("command failed: " + repr(args) + "\n" + out.stderr[-1500:])
    return out.stdout.strip()


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run on an isolated lab/CI runner with sudo.")
    for exe in ("losetup", "qemu-img", "qemu-io"):
        if not shutil.which(exe):
            raise SystemExit("Missing " + exe)
    devices=[]
    with tempfile.TemporaryDirectory(prefix="origami-qcow2-block-lab-") as work:
        root=Path(work)
        try:
            for i in range(3):
                image=root/f"test-lv-{i}.img"
                with image.open("wb") as stream:
                    stream.truncate(128*1024*1024)
                loop=run("losetup","--find","--show",str(image))
                if not loop.startswith("/dev/"):
                    raise RuntimeError("Untrusted losetup path")
                resolved=Path(loop).resolve(strict=True)
                if not stat.S_ISBLK(resolved.stat().st_mode):
                    raise RuntimeError("Allocated loop is not a block device")
                devices.append(loop)
            base, overlay, standalone=devices
            run("qemu-img","create","-f","qcow2",base,"64M")
            run("qemu-io","-f","qcow2","-c","write -P 0x52 0 64k",base)
            run("qemu-img","create","-f","qcow2","-F","qcow2",
                "-b",base,overlay)
            run("qemu-io","-f","qcow2","-c","write -P 0x31 128k 64k",overlay)
            run("qemu-img","check","-f","qcow2",base)
            run("qemu-img","check","-f","qcow2",overlay)
            run("qemu-io","-f","qcow2","-c","read -P 0x52 0 64k",overlay)
            run("qemu-io","-f","qcow2","-c","read -P 0x31 128k 64k",overlay)
            run("qemu-io","-f","qcow2","-c","read -P 0x00 128k 64k",base)
            chain=json.loads(run("qemu-img","info","-f","qcow2",
                                 "--backing-chain","--output=json",overlay))
            assert len(chain)==2,chain
            assert chain[0]["filename"]==overlay and chain[1]["filename"]==base
            # One-time full flatten into an independent QCOW2 block image.
            # This verifies the PVE LVM consolidation primitive on actual
            # block devices; it must not be used on production without guards.
            run("qemu-img","create","-f","qcow2",standalone,"64M")
            run("qemu-img","convert","-f","qcow2","-O","qcow2","-n",
                overlay,standalone)
            solo=json.loads(run("qemu-img","info","-f","qcow2",
                                "--backing-chain","--output=json",standalone))
            assert len(solo)==1 and solo[0]["filename"]==standalone,solo
            run("qemu-img","check","-f","qcow2",standalone)
            run("qemu-img","compare","-f","qcow2","-F","qcow2",
                overlay,standalone)
            # Existing backing remains intact and still needed by overlay.
            assert len(json.loads(run("qemu-img","info","-f","qcow2",
                                      "--backing-chain","--output=json",overlay)))==2
            print("PASS: real QCOW2 block-chain + independent verified flatten")
        finally:
            # Disconnect only the loop devices allocated in this process.
            for dev in reversed(devices):
                subprocess.run(["losetup","--detach",dev],
                               check=False,capture_output=True,timeout=20)


if __name__=="__main__":
    main()
