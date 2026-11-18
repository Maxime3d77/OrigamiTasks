"""Run-finished HTML notifications. Secrets are never included in templates."""
import html
import json
import re
from email.message import EmailMessage

from . import connections, db, executor

MAX_DETAIL = 900


def _text(value, maximum=250):
    return re.sub(r"[\r\n\x00-\x1f]+", " ", str(value or "")).strip()[:maximum]


def _redact(value, secrets):
    data=str(value or "")[:5000]
    for item in sorted(secrets, key=len, reverse=True):
        if len(item)>=4:
            data=data.replace(item, "[SECRET]")
    data=re.sub(r"(?i)((?:password|passwd|secret|token|authorization|api[_-]?key)\s*[:=]\s*)[^\s,;]+",
                r"\1[SECRET]", data)
    return data[:MAX_DETAIL]


def _secrets():
    """Collect encrypted vault secret values only to redact error logs."""
    values=set()
    with db.session() as con:
        rows=con.execute("SELECT encrypted_payload FROM credentials").fetchall()
    from . import security
    for row in rows:
        try:
            credential=security.decrypt(row["encrypted_payload"])
            for name in ("password","token","private_key","passphrase"):
                secret=credential.get(name)
                if isinstance(secret,str) and len(secret)>=4:
                    values.add(secret)
        except Exception:
            continue
    return values


def send(run_id, run, status, error):
    """Send at most one notification per completed run (SMTP selected in vault).

    The status of the workflow is immutable even if mail delivery fails.
    """
    notice=json.loads(run.get("notification_json") or "{}")
    if not notice.get("enabled"):
        return "disabled", ""
    if not notice.get("on_success") and status=="success":
        return "skipped", ""
    if not notice.get("on_failure") and status!="success":
        return "skipped", ""
    profile=executor.credential(notice["credential_id"])
    if not profile.get("host") or not profile.get("from_address"):
        raise ValueError("Profil SMTP : serveur ou adresse expéditeur absent")
    recipients=[x.strip() for x in str(notice["recipients"]).split(",") if x.strip()]
    with db.session() as con:
        steps=[dict(row) for row in con.execute(
            "SELECT name,type,status,attempt,exit_code,output,started_at,finished_at "
            "FROM step_runs WHERE run_id=? ORDER BY id",(run_id,)
        ).fetchall()]
    secrets=_secrets()
    success=(status=="success")
    label="SUCCÈS" if success else "ÉCHEC"
    job=_text(run.get("job_name"),120)
    subject="[OrigamiTasks] ["+label+"] "+job+" — exécution #"+str(run_id)
    now=db.now()
    details=[]
    for step in steps[:80]:
        output=_redact(step.get("output"),secrets) if step.get("status")=="failed" else ""
        details.append({
            "name":_text(step["name"],150),"type":_text(step["type"],60),
            "status":_text(step["status"],30),"attempt":step["attempt"],
            "exit_code":step["exit_code"],"output":output,
        })
    if len(steps)>80:
        details.append({"name":"Autres étapes non affichées","type":"","status":"info",
                        "attempt":"—","exit_code":None,"output":""})
    summary=_redact(error,secrets)
    plain=[
        "OrigamiTasks — "+label,
        "Workflow : "+job, "Exécution : #"+str(run_id),
        "Déclencheur : "+_text(run.get("trigger")),
        "Demandé par : "+_text(run.get("requested_by")),
        "Statut final : "+_text(status), "Terminé le (UTC) : "+now,
        "Étapes : "+str(len(steps)),
    ]
    if summary:
        plain.append("Raison : "+summary)
    for item in details:
        row=item["status"]+" | "+item["name"]+" ("+item["type"]+")"
        row+=" | tentative "+str(item["attempt"])+" | code "+str(item["exit_code"])
        plain.append(row)
        if item["output"]:
            plain.append("Détail (extrait) : "+item["output"])
    plain.append("Pour consulter le journal complet, ouvrir Exécutions → #"+str(run_id)+" dans OrigamiTasks.")
    color="#16a34a" if success else "#dc2626"
    rows=[]
    for item in details:
        codes=(str(item["exit_code"]) if item["exit_code"] is not None else "—")
        info=("Tentative "+str(item["attempt"])+" · code "+codes)
        excerpt=("<div style='margin-top:8px;padding:8px;background:#f4f4f5;"
                 "white-space:pre-wrap;word-break:break-word;font-family:monospace;font-size:12px'>"
                 +html.escape(item["output"])+"</div>") if item["output"] else ""
        rows.append("<tr><td style='padding:12px;border-bottom:1px solid #e4e4e7'><b>"
                    +html.escape(item["name"])+"</b><br><small>"+html.escape(item["type"])
                    +"</small>"+excerpt+"</td><td style='padding:12px;border-bottom:1px solid #e4e4e7'>"
                    +html.escape(item["status"])+"</td><td style='padding:12px;border-bottom:1px solid #e4e4e7'>"
                    +html.escape(info)+"</td></tr>")
    html_body="<html><body style='font-family:Arial,Helvetica,sans-serif;margin:0;background:#f4f4f5;color:#18181b'>"
    html_body+="<table role='presentation' width='100%' cellspacing='0' cellpadding='0'><tr><td align='center' style='padding:28px'>"
    html_body+="<table role='presentation' width='650' style='max-width:100%;background:white;border-collapse:collapse;border-radius:10px;overflow:hidden'>"
    html_body+="<tr><td style='background:#18181b;color:white;padding:24px'><b style='font-size:22px'>OrigamiTasks</b>"
    html_body+="<div style='margin-top:10px;font-size:16px'>Rapport d’exécution · <strong style='color:"+color+"'>"+label+"</strong></div></td></tr>"
    html_body+="<tr><td style='padding:24px'><h2 style='margin-top:0'>"+html.escape(job)+"</h2>"
    html_body+="<p><b>Exécution :</b> #"+str(run_id)+"<br><b>Statut :</b> "+html.escape(status)
    html_body+="<br><b>Déclencheur :</b> "+html.escape(_text(run.get("trigger")))
    html_body+="<br><b>Demandeur :</b> "+html.escape(_text(run.get("requested_by")))
    html_body+="<br><b>Terminé (UTC) :</b> "+html.escape(now)+"</p>"
    if summary:
        html_body+="<p style='padding:12px;background:#fef2f2;border-left:4px solid #dc2626'><b>Cause déclarée :</b> "+html.escape(summary)+"</p>"
    html_body+="<h3>Journal des étapes ("+str(len(steps))+")</h3>"
    html_body+="<table width='100%' cellspacing='0' style='border-collapse:collapse;font-size:13px'><thead><tr style='background:#f4f4f5'><th align='left' style='padding:10px'>Étape / diagnostic</th><th align='left'>État</th><th align='left'>Détails</th></tr></thead><tbody>"
    html_body+="".join(rows)+"</tbody></table><p style='font-size:12px;color:#71717a'>Journal complet : OrigamiTasks → Exécutions → #"+str(run_id)+". Les secrets connus du coffre sont masqués dans les extraits.</p>"
    html_body+="</td></tr></table></td></tr></table></body></html>"
    message=EmailMessage()
    message["From"]=_text(profile["from_address"],254)
    message["To"]=", ".join(recipients)
    message["Subject"]=subject
    message.set_content("\n".join(plain))
    message.add_alternative(html_body,subtype="html")
    smtp=connections.open_smtp(profile,timeout=12)
    try:
        smtp.send_message(message)
    finally:
        try:
            smtp.quit()
        except Exception:
            smtp.close()
    return "sent", ""
