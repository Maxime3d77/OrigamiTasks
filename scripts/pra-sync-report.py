#!/usr/bin/env python3
"""Report offline PRA synchronization to OrigamiTasks via enrolled agent token.

No new environment variable and no private SSH key extraction. The server
validates authenticated agent identity, site assignment and current PVE owner.
"""
import json
import ssl
import sys
from pathlib import Path
from urllib.request import Request,build_opener,HTTPSHandler,HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError("Redirect refused")


def main():
    if len(sys.argv)!=12:
        raise ValueError("Usage: report.py PROTECTION SOURCE_VMID TARGET_VMID RUN_UUID STATUS TOTAL DONE SCANNED TRANSFERRED ELAPSED ERROR")
    protection,sourcevmid,targetvmid,job,status,total,done,scanned,transferred,elapsed,error=sys.argv[1:]
    config=Path("/opt/origamitasks-agent/config.json")
    cfg=json.loads(config.read_text())
    server=cfg["server"].rstrip("/")
    if not server.startswith("https://"):
        raise ValueError("An HTTPS OrigamiTasks server is required")
    payload={
        "protected_vm_id":int(protection),"run_uuid":job,"status":status,
        "disks_total":int(total),"disks_done":int(done),
        "scanned_bytes":int(scanned),"transferred_bytes":int(transferred),
        "elapsed_seconds":int(elapsed),
        "error":error[:200]
    }
    req=Request(server+"/api/agent/pra/sync/report",
        data=json.dumps(payload).encode(),method="POST",headers={
            "Content-Type":"application/json",
            "Authorization":"Bearer "+cfg["token"],
            "X-Origami-Agent-ID":str(cfg["agent_id"])
        })
    context=ssl.create_default_context(cafile=cfg.get("ca_cert") or None)
    opener=build_opener(HTTPSHandler(context=context),NoRedirect())
    with opener.open(req,timeout=25) as response:
        data=json.load(response)
    if data.get("status")!=status:
        raise RuntimeError("Status mismatch")


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        # Avoid exposing any secret from connection exceptions.
        print("PRA tracking report failed: "+type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
