#!/usr/bin/env python3
"""Read-only live QMP capability report for a RUNNING Proxmox guest.

Never changes bitmaps, block nodes or guest state. This is a compatibility
probe, not an incremental backup provider or an authorisation to restore.
"""
import json
import os
import re
import socket
import subprocess
import sys

def report(vmid):
    result={"vmid":vmid,"source_running":False,"qmp_available":False,
            "disks":[],"qmp_commands":[],"hot_incremental_ready":False,
            "reason":"not_checked","source_stopped":False}
    cfg="/etc/pve/nodes/"+socket.gethostname().split(".")[0]+"/qemu-server/"+str(vmid)+".conf"
    if not os.path.isfile(cfg):
        result["reason"]="vm_not_owned_by_this_node"
        return result
    state=subprocess.run(["qm","status",str(vmid)],capture_output=True,text=True,timeout=12)
    if state.returncode or state.stdout.strip()!="status: running":
        result["reason"]="source_not_running"
        result["source_stopped"]=state.stdout.strip()=="status: stopped"
        return result
    result["source_running"]=True
    sock="/var/run/qemu-server/"+str(vmid)+".qmp"
    if not os.path.exists(sock):
        result["reason"]="qmp_socket_unavailable"
        return result
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
        conn.settimeout(8)
        conn.connect(sock)
        file=conn.makefile("rwb",buffering=0)
        def recv(response_id=None):
            for _ in range(200):
                line=file.readline()
                if not line: raise RuntimeError("qmp_eof")
                if len(line)>1_000_000: raise RuntimeError("qmp_oversized_message")
                item=json.loads(line)
                if response_id is None and "QMP" in item: return item
                if item.get("id")==response_id:
                    if "error" in item: raise RuntimeError("qmp_error")
                    return item.get("return")
            raise RuntimeError("qmp_response_missing")
        def execute(cmd,idx):
            file.write((json.dumps({"execute":cmd,"id":idx},separators=(",",":"))+"\n").encode())
            return recv(idx)
        recv()
        execute("qmp_capabilities",1)
        result["qmp_available"]=True
        blocks=execute("query-block",2)
        result["disks"]=[{"device":str(x.get("device",""))[:100],
                          "inserted":bool(x.get("inserted")),
                          "dirty_bitmaps":[{
                              "name":str(b.get("name",""))[:100],
                              "recording":bool(b.get("recording")),
                              "busy":bool(b.get("busy")),
                              "persistent":bool(b.get("persistent")),
                              "inconsistent":bool(b.get("inconsistent"))
                          } for b in (x.get("inserted") or {}).get("dirty-bitmaps",[]) if isinstance(b,dict)]}
                         for x in blocks if isinstance(x,dict)]
        # Query only command metadata. In particular DO NOT execute
        # block-dirty-bitmap-add or blockdev-backup against a production VM.
        commands=execute("query-commands",3)
        supported={x.get("name") for x in commands if isinstance(x,dict)}
        required=("block-dirty-bitmap-add","blockdev-backup","transaction")
        result["qmp_commands"]=[name for name in required if name in supported]
        result["reason"]=("qmp_has_primitives_but_provider_missing"
                          if all(name in supported for name in required)
                          else "qmp_incremental_capabilities_missing")
        # Capability presence alone CANNOT prove coherent chain/restore.
        result["hot_incremental_ready"]=False
    return result

def main():
    if len(sys.argv)!=2 or not re.fullmatch(r"[1-9][0-9]{1,8}",sys.argv[1]):
        raise SystemExit(2)
    try:
        if os.geteuid()!=0:
            raise RuntimeError("root_required")
        print(json.dumps(report(int(sys.argv[1])),sort_keys=True))
    except (OSError,ValueError,RuntimeError,TimeoutError,subprocess.SubprocessError) as exc:
        print(json.dumps({"vmid":int(sys.argv[1]),"source_running":None,
                          "qmp_available":False,"hot_incremental_ready":False,
                          "reason":str(exc)[:100],"disks":[],"qmp_commands":[]}),flush=True)
        raise SystemExit(1)

if __name__=="__main__":
    main()
