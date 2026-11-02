"""Job Watch: checks every source, finds new matching jobs, and alerts you.

python -m jobwatch                 normal hourly run
python -m jobwatch --dry-run       fetch and match, but send nothing and save nothing
python -m jobwatch --check-sources test every source and print a status table
python -m jobwatch --test-alert    send a test message to your configured channels
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .core import Matcher, State, fuzzy_key, load_config, load_sources
from .fetchers import FETCHERS, make_session
from .notify import email_enabled, notify, telegram_enabled


def fetch_all(sources, cfg, log):
    session = make_session(cfg["fetching"]["timeout_seconds"])
    results = {}

    def run(src):
        fn = FETCHERS.get(src["kind"])
        if not fn:
            return src, None, f"no fetcher for type {src['kind']}"
        try:
            return src, fn(session, src, cfg), None
        except Exception as ex:
            return src, None, f"{type(ex).__name__}: {ex}"

    with ThreadPoolExecutor(max_workers=cfg["fetching"]["max_workers"]) as pool:
        for fut in as_completed([pool.submit(run, s) for s in sources]):
            src, jobs, err = fut.result()
            results[src["id"]] = (src, jobs, err)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobwatch")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-sources", action="store_true")
    ap.add_argument("--test-alert", action="store_true")
    args = ap.parse_args(argv)
    log = print

    cfg = load_config()
    tz = ZoneInfo(cfg["alerts"].get("timezone") or "Asia/Colombo")
    now = datetime.now(timezone.utc)
    local = now.astimezone(tz)
    today = now.date().isoformat()

    if args.test_alert:
        log(f"Telegram configured: {telegram_enabled()} · Email configured: {email_enabled()}")
        ok = notify("Job Watch test", text=f"Alerts are working. Sent {local:%d %b %Y, %H:%M} Sri Lanka time.", log=log)
        return 0 if ok else 1

    sources = load_sources(cfg)
    matcher = Matcher(cfg)
    log(f"Checking {len(sources)} sources at {local:%Y-%m-%d %H:%M} ({tz.key})")
    results = fetch_all(sources, cfg, log)

    if args.check_sources:
        bad = 0
        log(f"\n{'STATUS':7} {'JOBS':>5} {'MATCH':>5}  SOURCE")
        for sid, (src, jobs, err) in sorted(results.items(), key=lambda x: (x[1][2] is None, x[0].lower())):
            if err:
                bad += 1
                log(f"{'FAIL':7} {'-':>5} {'-':>5}  {sid} [{src['kind']}] {err[:110]}")
            else:
                m = sum(1 for j in jobs if matcher(j, src))
                flag = "EMPTY" if not jobs else "ok"
                log(f"{flag:7} {len(jobs):>5} {m:>5}  {sid} [{src['kind']}]")
                hint = src.get("_ats_hint")
                if hint and not jobs:
                    log(f"{'':20}page links to a job board: {hint}")
        log(f"\n{len(results) - bad} working, {bad} failing")
        return 0

    state = State()
    matched, run_keys, run_fuzzy = [], set(), set()
    for sid, (src, jobs, err) in results.items():
        if not args.dry_run:
            h = state.record(sid, err is None, len(jobs or []), err, src.get("_ats_hint"), now.isoformat())
            src["_health"] = h
        if err:
            log(f"  ✗ {sid}: {err[:140]}")
            continue
        for j in jobs:
            if not matcher(j, src):
                continue
            fk = fuzzy_key(j)
            if j["key"] in run_keys or (fk and fk in run_fuzzy):
                continue
            run_keys.add(j["key"])
            if fk:
                run_fuzzy.add(fk)
            matched.append(j)

    new = [j for j in matched if state.is_new(j)]
    ok_count = sum(1 for _, _, e in results.values() if e is None)
    log(f"{ok_count}/{len(results)} sources worked · {len(matched)} matching jobs · {len(new)} new")

    if args.dry_run:
        for j in new[:100]:
            log(f"  NEW  {j['title']} | {j.get('company')} | {j.get('location')} | {j['url']}")
        return 0

    sent_ok = True
    if state.first_run:
        if cfg["alerts"].get("first_run_digest") and matched:
            limit = cfg["alerts"].get("first_run_digest_limit", 60)
            extra = f" (showing {limit})" if len(matched) > limit else ""
            sent_ok = notify(f"Job Watch is live: {len(matched)} matching jobs open now{extra}",
                             jobs=matched[:limit], log=log)
        else:
            notify("Job Watch is live", text=f"Watching {len(sources)} sources. You'll get new matches here.", log=log)
    elif new:
        sent_ok = notify(f"{len(new)} new job{'s' if len(new) != 1 else ''} for you", jobs=new, log=log)

    channels_on = telegram_enabled() or email_enabled()
    if sent_ok or not channels_on:
        for j in (matched if state.first_run else new):
            state.mark(j, today)
    else:
        log("  alerts failed, will retry these jobs next run")

    # Source health: alert once when a source crosses the failure threshold, plus a daily report.
    threshold = cfg["alerts"].get("failure_threshold", 3)
    newly_broken = [sid for sid, (src, _, err) in results.items()
                    if err and src.get("_health", {}).get("failures") == threshold]
    if newly_broken:
        notify("Job Watch: sources stopped working",
               text="These sources failed " + str(threshold) + " runs in a row:\n- " + "\n- ".join(newly_broken)
               + "\n\nThey'll keep being retried. Run 'Check sources' in GitHub Actions for details.", log=log)

    report_hour = cfg["alerts"].get("health_report_hour")
    meta = state.health.setdefault("_meta", {})
    if report_hour is not None and local.hour == int(report_hour) and meta.get("last_report") != local.date().isoformat():
        broken = sorted(k for k, v in state.health.items() if not k.startswith("_") and v.get("failures", 0) >= threshold)
        hints = sorted(f"{k}: {v['ats_hint']}" for k, v in state.health.items()
                       if not k.startswith("_") and v.get("ats_hint") and v.get("last_count", 0) == 0)
        lines = [f"{ok_count} of {len(results)} sources working.",
                 f"Jobs alerted in the last 7 days: {sum(1 for k, v in state.seen.items() if '|' not in k and v >= _days_ago(7))}."]
        if broken:
            lines += ["", "Not working:"] + [f"- {b}" for b in broken]
        if hints:
            lines += ["", "These careers pages link to a job board that could be read directly:"] + [f"- {h}" for h in hints]
        notify("Job Watch daily check", text="\n".join(lines), log=log)
        meta["last_report"] = local.date().isoformat()

    state.prune()
    state.save()
    return 0


def _days_ago(n):
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()


if __name__ == "__main__":
    sys.exit(main())
