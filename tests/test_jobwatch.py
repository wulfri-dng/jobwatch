"""Offline tests: sample payloads in the shape each job platform returns."""
import json

import pytest

from jobwatch import core, fetchers
from jobwatch.core import Matcher, State, fuzzy_key, load_config, load_sources

CFG = load_config()


class Resp:
    def __init__(self, payload, status=200, url="https://example.com"):
        self.status_code = status
        self.url = url
        if isinstance(payload, (dict, list)):
            self._json = payload
            self.text = json.dumps(payload)
        else:
            self._json = None
            self.text = payload
        self.content = self.text.encode()

    def json(self):
        return self._json


class FakeSession:
    request_timeout = 5

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _find(self, url):
        self.calls.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp(url) if callable(resp) else resp
        return Resp({}, 404, url)

    def get(self, url, **kw):
        return self._find(url)

    def post(self, url, **kw):
        return self._find(url)


SRC = {"company": "Acme", "label": "Acme", "sl_only": False}


def test_greenhouse_with_eu_fallback():
    s = FakeSession({"https://boards-api.eu.greenhouse.io": Resp({"jobs": [
        {"id": 1, "title": "Senior Software Engineer", "location": {"name": "Colombo, Sri Lanka"},
         "absolute_url": "https://x/1", "updated_at": "2026-11-01"}]})})
    jobs = fetchers.fetch_greenhouse(s, {**SRC, "feed_url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs"}, CFG)
    assert jobs[0]["title"] == "Senior Software Engineer" and jobs[0]["location"] == "Colombo, Sri Lanka"
    assert len(s.calls) == 2


def test_lever():
    s = FakeSession({"https://api.lever.co": Resp([
        {"id": "abc", "text": "Full Stack Engineer", "categories": {"location": "Colombo"},
         "hostedUrl": "https://jobs.lever.co/acme/abc", "createdAt": 1}])})
    jobs = fetchers.fetch_lever(s, {**SRC, "feed_url": "https://api.lever.co/v0/postings/acme?mode=json"}, CFG)
    assert jobs[0]["key"] == "lever:abc" and jobs[0]["location"] == "Colombo"


def test_workable_widget():
    s = FakeSession({"https://apply.workable.com/api/v1/widget/accounts/acme": Resp({"jobs": [
        {"title": "React Developer", "shortcode": "XY1", "city": "Colombo", "country": "Sri Lanka",
         "url": "https://apply.workable.com/j/XY1"}]})})
    jobs = fetchers.fetch_workable(s, {**SRC, "feed_url": "https://apply.workable.com/api/v3/accounts/acme/jobs"}, CFG)
    assert jobs[0]["location"] == "Colombo, Sri Lanka"


def test_smartrecruiters_country_code():
    s = FakeSession({"https://api.smartrecruiters.com": Resp({"totalFound": 1, "content": [
        {"id": "9", "name": "Software Engineer - Full Stack", "location": {"city": "Colombo", "country": "lk"}}]})})
    jobs = fetchers.fetch_smartrecruiters(s, {**SRC, "feed_url": "https://api.smartrecruiters.com/v1/companies/ifs1/postings"}, CFG)
    assert jobs[0]["location"] == "Colombo, Sri Lanka"
    assert jobs[0]["url"] == "https://jobs.smartrecruiters.com/ifs1/9"


def test_workday_builds_job_url():
    s = FakeSession({"https://lseg.wd3.myworkdayjobs.com": Resp({"jobPostings": [
        {"title": "Senior Software Engineer", "externalPath": "/job/Colombo/SSE_R1", "locationsText": "Colombo"}]})})
    src = {**SRC, "feed_url": "https://lseg.wd3.myworkdayjobs.com/wday/cxs/lseg/Careers/jobs"}
    jobs = fetchers.fetch_workday(s, src, CFG)
    assert len(jobs) == 1  # same job from both search terms is kept once
    assert jobs[0]["url"] == "https://lseg.wd3.myworkdayjobs.com/Careers/job/Colombo/SSE_R1"


HTML = """
<html><body>
<nav><a href="/">Home</a><a href="/about">About us</a><a href="/careers">Careers</a></nav>
<a href="/job/12801/senior-react-developer-at-tecciance/"><h3>Senior React Developer at tecciance</h3></a>
<a href="/job/12802/qa-engineer-at-foo/">QA Engineer at Foo</a>
<a href="https://jobs.lever.co/acme/123">Full Stack Engineer</a>
<a href="https://twitter.com/itpro">Follow us</a>
<a href="/job/12801/senior-react-developer-at-tecciance/">Apply now</a>
</body></html>
"""


def test_html_page_extracts_job_links_only():
    jobs = fetchers.parse_html_jobs(HTML, "https://itpro.lk/jobs/software-engineering/",
                                    {"company": None, "label": "itpro.lk"})
    titles = [j["title"] for j in jobs]
    assert "Senior React Developer" in titles
    assert any(j["company"] == "Tecciance" for j in jobs)
    assert "Full Stack Engineer" in titles
    assert not any(t in titles for t in ["Home", "About us", "Careers", "Follow us", "Apply now"])


def test_jsonld_jobposting():
    html = """<script type="application/ld+json">{"@type":"JobPosting","title":"JavaScript Developer",
      "url":"https://acme.lk/jobs/7","hiringOrganization":{"name":"Acme"},
      "jobLocation":{"address":{"addressLocality":"Colombo","addressCountry":"LK"}}}</script>"""
    jobs = fetchers.parse_html_jobs(html, "https://acme.lk/careers", {"company": "Acme"})
    assert jobs[0]["title"] == "JavaScript Developer" and "Colombo" in jobs[0]["location"]


def test_rss_feed():
    xml = """<?xml version="1.0"?><rss><channel>
      <item><title>Senior Full Stack Engineer at Orysys</title><link>https://itpro.lk/job/1/x/</link>
      <pubDate>Mon, 02 Nov 2026 08:00:00 +0530</pubDate></item></channel></rss>"""
    s = FakeSession({"https://itpro.lk/rss": Resp(xml)})
    jobs = fetchers.fetch_rss(s, {"company": None, "label": "itpro.lk", "page_url": "https://itpro.lk/rss/"}, CFG)
    assert jobs[0]["title"] == "Senior Full Stack Engineer" and jobs[0]["company"] == "Orysys"


def test_find_ats_links():
    assert "lever" in fetchers.find_ats_links('<iframe src="https://jobs.lever.co/acme"></iframe>')


@pytest.mark.parametrize("title,ok", [
    ("Software Engineer", True), ("Senior Software Engineer", True), ("Full Stack Engineer", True),
    ("Senior Fullstack Engineer", True), ("Full-Stack Developer", True), ("React Developer", True),
    ("Senior React.js Developer", True), ("JavaScript Developer", True), ("Senior JavaScript Developer", True),
    ("Associate Technical Lead - Full Stack", True), ("Senior Frontend Engineer", True),
    ("Intern Software Engineer", False), ("QA Engineer", False), ("Software Engineer in Test", False),
    ("Engineering Manager", False), ("Data Engineer", False), ("Business Analyst", False),
    ("Sales Executive", False), ("Trainee Software Engineer", False),
])
def test_title_matching(title, ok):
    assert Matcher(CFG).title_ok(title) is ok


def test_location_rules():
    m = Matcher(CFG)
    multinational = {"sl_only": False}
    assert m({"title": "Software Engineer", "location": "Colombo, Sri Lanka"}, multinational)
    assert not m({"title": "Software Engineer", "location": "London, UK"}, multinational)
    assert not m({"title": "Software Engineer", "location": "Remote"}, multinational)  # phase 2 is off
    assert m({"title": "Software Engineer", "location": ""}, {"sl_only": True})


def test_state_dedupes_same_job_across_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "STATE_DIR", tmp_path)
    st = State()
    assert st.first_run
    a = {"key": "lever:1", "title": "Senior React Developer", "company": "Orysys"}
    b = {"key": "web:https://itpro.lk/job/9", "title": "Senior React Developer", "company": "Orysys"}
    st.mark(a, "2026-11-02")
    assert not st.is_new(b)
    st.save()
    assert not State().first_run


def test_health_counts_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "STATE_DIR", tmp_path)
    st = State()
    for _ in range(3):
        h = st.record("X", False, 0, "HTTP 500")
    assert h["failures"] == 3
    h = st.record("X", True, 4)
    assert h["failures"] == 0 and "last_error" not in h


def test_sources_load():
    srcs = load_sources(CFG)
    kinds = {s["kind"] for s in srcs}
    assert {"smartrecruiters", "workable", "html", "rss"} <= kinds
    assert not any("itpro.lk/job/" in (s.get("page_url") or "") for s in srcs)
    assert all(s["kind"] in fetchers.FETCHERS for s in srcs)
