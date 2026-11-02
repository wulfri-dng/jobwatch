"""Loading sources, matching jobs to your search, and remembering what was already sent."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"

API_TYPES = {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "workday", "recruitee", "bamboohr"}
PAGE_TYPES = {"html", "teamtailor", "jazzhr", "manatal", "personio", "breezy", "zoho", "freshteam",
              "pinpoint", "rippling"}
NON_SL_BOARDS = {"builtin.com", "web3.career"}
# Single job pages are useless to poll; those companies are covered by the job-board feeds.
SINGLE_JOB_URL = re.compile(r"itpro\.lk/job/\d+|jobhub\.lk/job\?id=|rooster\.jobs/jobs?/\d+")


def load_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def load_sources(cfg):
    """Turn sources.json + aggregators in config.yaml into a flat list of things to poll."""
    out = []
    for s in json.loads((ROOT / "sources.json").read_text()):
        if not s.get("enabled", True):
            continue
        t = s.get("type")
        src = {"id": s["company"], "company": s["company"], "label": s["company"], "sl_only": True,
               "confidence": s.get("confidence")}
        if t in API_TYPES and s.get("feed_url"):
            src.update(kind=t, feed_url=s["feed_url"], sl_only=False)
        elif t in API_TYPES or t in PAGE_TYPES:
            if not s.get("careers_url"):
                continue
            src.update(kind="html", page_url=s["careers_url"])
        elif t == "js_rendered":
            src.update(kind="js_rendered", page_url=s["careers_url"])
        elif t == "jobboard_only":
            url = s.get("jobboard_url")
            if not url or SINGLE_JOB_URL.search(url):
                continue
            host = urlparse(url).netloc.replace("www.", "")
            src.update(kind="html", page_url=url, sl_only=host not in NON_SL_BOARDS)
        else:
            continue
        out.append(src)
    for a in cfg.get("aggregators") or []:
        out.append({"id": a["name"], "company": None, "label": a["name"], "kind": a["type"],
                    "page_url": a["url"], "sl_only": True, "aggregator": True})
    return out


class Matcher:
    def __init__(self, cfg):
        t = cfg["titles"]
        self.inc = [re.compile(p, re.I) for p in t["include"]]
        self.exc = [re.compile(p, re.I) for p in t.get("exclude") or []]
        loc = cfg["location"]
        self.sl = [x.lower() for x in loc["sri_lanka_terms"]]
        self.remote = [x.lower() for x in loc.get("remote_terms") or []]
        self.allow_remote = bool(loc.get("allow_remote_anywhere"))

    def title_ok(self, title):
        return any(p.search(title) for p in self.inc) and not any(p.search(title) for p in self.exc)

    def location_ok(self, job, sl_only_source):
        loc = (job.get("location") or "").lower()
        if sl_only_source:
            return True
        if not loc:
            return False
        if any(t in loc for t in self.sl):
            return True
        return self.allow_remote and any(t in loc for t in self.remote)

    def __call__(self, job, src):
        return bool(job.get("title")) and self.title_ok(job["title"]) and self.location_ok(job, src.get("sl_only"))


def fuzzy_key(job):
    company = re.sub(r"[^a-z0-9]", "", (job.get("company") or "").lower())
    title = re.sub(r"[^a-z0-9]", "", job["title"].lower())
    return f"{company}|{title}" if company else None


class State:
    """seen.json: jobs already alerted. health.json: how each source is doing."""

    def __init__(self):
        STATE_DIR.mkdir(exist_ok=True)
        self.seen_path = STATE_DIR / "seen.json"
        self.health_path = STATE_DIR / "health.json"
        self.seen = json.loads(self.seen_path.read_text()) if self.seen_path.exists() else {}
        self.health = json.loads(self.health_path.read_text()) if self.health_path.exists() else {}
        self.first_run = not self.seen_path.exists()

    def is_new(self, job):
        if job["key"] in self.seen:
            return False
        fk = fuzzy_key(job)
        return not (fk and fk in self.seen)

    def mark(self, job, today):
        self.seen[job["key"]] = today
        fk = fuzzy_key(job)
        if fk:
            self.seen[fk] = today

    def prune(self, days=120):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}

    def record(self, src_id, ok, count, error=None, hint=None, now=None):
        h = self.health.setdefault(src_id, {"failures": 0, "empty_runs": 0})
        if ok:
            h["failures"] = 0
            h["empty_runs"] = h["empty_runs"] + 1 if count == 0 else 0
            h["last_ok"] = now
            h["last_count"] = count
            h.pop("last_error", None)
        else:
            h["failures"] += 1
            h["last_error"] = (error or "")[:300]
        if hint:
            h["ats_hint"] = hint
        return h

    def save(self):
        self.seen_path.write_text(json.dumps(self.seen, indent=0, sort_keys=True))
        self.health_path.write_text(json.dumps(self.health, indent=1, sort_keys=True))
