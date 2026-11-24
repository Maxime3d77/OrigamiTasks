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
            for i in range(4):
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
            base, overlay, buffer_a, buffer_b=devices
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
            # New receiver protocol: two persistent and independent target
            # volumes, reused alternately. Flatten a legacy backing chain
            # into A, then copy A->B, then safely reset/reuse inactive A.
            run("qemu-img","create","-f","qcow2",buffer_a,"64M")
            run("qemu-img","convert","-f","qcow2","-O","qcow2",
                "-n",overlay,buffer_a)
            run("qemu-io","-f","qcow2","-c","read -P 0x52 0 64k",buffer_a)
            run("qemu-io","-f","qcow2","-c","read -P 0x31 128k 64k",buffer_a)
            a_chain=json.loads(run("qemu-img","info","-f","qcow2",
                                   "--backing-chain","--output=json",buffer_a))
            assert len(a_chain)==1,a_chain
            run("qemu-io","-f","qcow2","-c","write -P 0xFA 256k 64k",buffer_a)
            run("qemu-img","create","-f","qcow2",buffer_b,"64M")
            run("qemu-img","convert","-f","qcow2","-O","qcow2",
                "-n",buffer_a,buffer_b)
            run("qemu-io","-f","qcow2","-c","write -P 0xB2 384k 64k",buffer_b)
            # A has data absent from B; the new image must not leak it.
            run("qemu-io","-f","qcow2","-c","write -P 0xD1 768k 64k",buffer_a)
            # Reusing A wipes its previous QCOW2 allocations BEFORE convert;
            # a zero region must not expose stale clusters.
            run("qemu-img","create","-f","qcow2",buffer_a,"64M")
            run("qemu-img","convert","-f","qcow2","-O","qcow2",
                "-n",buffer_b,buffer_a)
            run("qemu-io","-f","qcow2","-c","read -P 0x52 0 64k",buffer_a)
            run("qemu-io","-f","qcow2","-c","read -P 0x31 128k 64k",buffer_a)
            run("qemu-io","-f","qcow2","-c","read -P 0xFA 256k 64k",buffer_a)
            run("qemu-io","-f","qcow2","-c","read -P 0xB2 384k 64k",buffer_a)
            run("qemu-io","-f","qcow2","-c","read -P 0x00 768k 64k",buffer_a)
            for volume in (buffer_a,buffer_b):
                run("qemu-img","check","-f","qcow2",volume)
                verified=json.loads(run("qemu-img","info","-f","qcow2",
                                        "--backing-chain","--output=json",volume))
                assert len(verified)==1,verified
            print("PASS: real block-device QCOW2 chain migration and two-buffer reuse")
        finally:
            # Disconnect only the loop devices allocated in this process.
            for dev in reversed(devices):
                subprocess.run(["losetup","--detach",dev],
                               check=False,capture_output=True,timeout=20)


if __name__=="__main__":
    main()
