"""Telegram and email alerts. Each channel turns on when its secrets are set."""
from __future__ import annotations

import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

TG_LIMIT = 3800  # Telegram allows 4096 characters per message


def telegram_enabled():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def email_enabled():
    return bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD") and os.getenv("EMAIL_TO"))


def _tg_send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text[:200]}")


def _job_line_tg(j):
    e = html.escape
    where = " · ".join(x for x in [j.get("company"), j.get("location")] if x)
    via = f" · via {e(j['source'])}" if j.get("source") and j["source"] != j.get("company") else ""
    return (f"<b>{e(j['title'])}</b>\n{e(where)}\n"
            f'<a href="{e(j["url"] or "", quote=True)}">Open job</a>{via}')


def send_telegram(header, jobs=None, text=None):
    chunks, cur = [], f"<b>{html.escape(header)}</b>"
    for line in ([_job_line_tg(j) for j in jobs] if jobs else [html.escape(text or "")]):
        if len(cur) + len(line) + 2 > TG_LIMIT:
            chunks.append(cur)
            cur = line
        else:
            cur += "\n\n" + line
    chunks.append(cur)
    for c in chunks:
        _tg_send(c)


def send_email(subject, jobs=None, text=None):
    e = html.escape
    if jobs:
        rows = "".join(
            f'<tr><td style="padding:10px 0;border-bottom:1px solid #e3e8e5">'
            f'<a href="{e(j["url"] or "", quote=True)}" style="font-weight:600;color:#1d6b55;font-size:15px;'
            f'text-decoration:none">{e(j["title"])}</a><br>'
            f'<span style="color:#55635c;font-size:13px">'
            f'{e(" · ".join(x for x in [j.get("company"), j.get("location"), "via " + j["source"] if j.get("source") else None] if x))}'
            f"</span></td></tr>" for j in jobs)
        body = f'<table style="width:100%;max-width:640px;border-collapse:collapse;font-family:sans-serif">{rows}</table>'
    else:
        body = f'<pre style="font-family:sans-serif;white-space:pre-wrap">{e(text or "")}</pre>'
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["EMAIL_TO"]
    plain = "\n\n".join(f"{j['title']} — {j.get('company') or ''} {j.get('location') or ''}\n{j['url']}"
                        for j in jobs) if jobs else (text or "")
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(f'<div style="font-family:sans-serif"><h2 style="font-size:18px">{e(subject)}</h2>{body}</div>',
                        "html", "utf-8"))
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        server.sendmail(os.environ["SMTP_USER"], [a.strip() for a in os.environ["EMAIL_TO"].split(",")],
                        msg.as_string())


def notify(header, jobs=None, text=None, log=print):
    """Send to every configured channel. Returns True if at least one channel worked."""
    sent = False
    channels = [("Telegram", telegram_enabled, send_telegram), ("Email", email_enabled, send_email)]
    for name, enabled, fn in channels:
        if not enabled():
            continue
        try:
            fn(header, jobs=jobs, text=text)
            sent = True
            log(f"  sent via {name}")
        except Exception as ex:  # one channel failing shouldn't stop the other
            log(f"  {name} failed: {ex}")
    if not any(enabled() for _, enabled, _ in channels):
        log("  no alert channel configured, printing instead:")
        for j in jobs or []:
            log(f"   - {j['title']} | {j.get('company')} | {j.get('location')} | {j['url']}")
        if text:
            log(text)
    return sent
