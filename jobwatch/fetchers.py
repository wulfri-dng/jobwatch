"""One fetcher per kind of job source. Each returns a list of Job dicts.

Job dict keys: key, title, company, location, url, posted, source
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (compatible; JobWatch/1.0; personal job alerts; "
      "+https://github.com) Python-requests")

ATS_HOST_PATTERNS = {
    "greenhouse": r"boards(?:-api)?(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)",
    "lever": r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)",
    "ashby": r"jobs\.ashbyhq\.com/([\w.-]+)",
    "workable": r"apply\.workable\.com/([\w-]+)",
    "smartrecruiters": r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)",
    "recruitee": r"([\w-]+)\.recruitee\.com",
    "bamboohr": r"([\w-]+)\.bamboohr\.com",
    "workday": r"([\w-]+\.wd\d+\.myworkdayjobs\.com/[\w-]+)",
}


class FetchError(Exception):
    pass


def make_session(timeout: int) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en"})
    s.request_timeout = timeout  # type: ignore[attr-defined]
    return s


def _get(s, url, **kw):
    r = s.get(url, timeout=s.request_timeout, **kw)
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code} for {url}")
    return r


def _post(s, url, payload):
    r = s.post(url, json=payload, timeout=s.request_timeout,
               headers={"Content-Type": "application/json", "Accept": "application/json"})
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code} for {url}")
    return r


def _job(key, title, company, location, url, posted, source):
    return {
        "key": key,
        "title": " ".join(str(title or "").split()),
        "company": company,
        "location": " ".join(str(location or "").split()),
        "url": url,
        "posted": str(posted or ""),
        "source": source,
    }


def _slug(feed_url, pattern):
    m = re.search(pattern, feed_url or "")
    if not m:
        raise FetchError(f"can't read company id from {feed_url}")
    return m.group(1)


# ── Hosted applicant tracking systems ───────────────────────────────────────

def fetch_greenhouse(s, src, cfg):
    url = src["feed_url"]
    try:
        data = _get(s, url).json()
    except FetchError:
        if ".eu." in url:
            raise
        data = _get(s, url.replace("boards-api.greenhouse.io", "boards-api.eu.greenhouse.io")).json()
    return [_job(f"greenhouse:{j['id']}", j.get("title"), src["company"],
                 (j.get("location") or {}).get("name"), j.get("absolute_url"),
                 j.get("updated_at"), src["company"]) for j in data.get("jobs", [])]


def fetch_lever(s, src, cfg):
    url = src["feed_url"]
    try:
        data = _get(s, url).json()
    except FetchError:
        if "api.eu.lever.co" in url:
            raise
        data = _get(s, url.replace("api.lever.co", "api.eu.lever.co")).json()
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location")]
        loc = ", ".join(l for l in locs if l)
        if j.get("workplaceType") == "remote":
            loc = (loc + " (Remote)").strip()
        jobs.append(_job(f"lever:{j['id']}", j.get("text"), src["company"], loc,
                         j.get("hostedUrl"), j.get("createdAt"), src["company"]))
    return jobs


def fetch_ashby(s, src, cfg):
    data = _get(s, src["feed_url"]).json()
    jobs = []
    for j in data.get("jobs", []):
        loc = j.get("location") or ""
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        extra = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressCountry")] if x)
        if extra and extra.lower() not in loc.lower():
            loc = f"{loc}, {extra}" if loc else extra
        if j.get("isRemote"):
            loc += " (Remote)"
        jobs.append(_job(f"ashby:{j.get('id') or j.get('jobUrl')}", j.get("title"), src["company"],
                         loc, j.get("jobUrl"), j.get("publishedAt"), src["company"]))
    return jobs


def fetch_workable(s, src, cfg):
    slug = _slug(src["feed_url"], r"accounts/([\w-]+)")
    jobs = []
    try:
        data = _get(s, f"https://apply.workable.com/api/v1/widget/accounts/{slug}").json()
        for j in data.get("jobs", []):
            locs = j.get("locations") or [{"city": j.get("city"), "country": j.get("country")}]
            loc = "; ".join(", ".join(x for x in [l.get("city"), l.get("country")] if x) for l in locs)
            if j.get("telecommuting"):
                loc += " (Remote)"
            jobs.append(_job(f"workable:{slug}:{j.get('shortcode')}", j.get("title"), src["company"], loc,
                             j.get("url") or f"https://apply.workable.com/{slug}/j/{j.get('shortcode')}/",
                             j.get("published_on"), src["company"]))
        return jobs
    except FetchError:
        pass
    token = None
    for _ in range(10):
        payload = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            payload["token"] = token
        data = _post(s, f"https://apply.workable.com/api/v3/accounts/{slug}/jobs", payload).json()
        for j in data.get("results", []):
            l = j.get("location") or {}
            loc = ", ".join(x for x in [l.get("city"), l.get("country")] if x)
            if j.get("remote"):
                loc += " (Remote)"
            jobs.append(_job(f"workable:{slug}:{j.get('shortcode')}", j.get("title"), src["company"], loc,
                             f"https://apply.workable.com/{slug}/j/{j.get('shortcode')}/",
                             j.get("published"), src["company"]))
        token = data.get("nextPage")
        if not token:
            break
    return jobs


def fetch_smartrecruiters(s, src, cfg):
    slug = _slug(src["feed_url"], r"companies/([\w-]+)")
    jobs, offset = [], 0
    while offset < 1000:
        data = _get(s, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                    params={"limit": 100, "offset": offset}).json()
        for j in data.get("content", []):
            l = j.get("location") or {}
            country = {"lk": "Sri Lanka"}.get((l.get("country") or "").lower(), l.get("country"))
            loc = ", ".join(x for x in [l.get("city"), country] if x)
            if l.get("remote"):
                loc += " (Remote)"
            jobs.append(_job(f"smartrecruiters:{j['id']}", j.get("name"), src["company"], loc,
                             f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                             j.get("releasedDate"), src["company"]))
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return jobs


def fetch_workday(s, src, cfg):
    url = src["feed_url"]
    m = re.match(r"(https://[^/]+)/wday/cxs/[^/]+/([^/]+)/jobs", url or "")
    if not m:
        raise FetchError(f"not a Workday jobs endpoint: {url}")
    base, site = m.groups()
    seen, jobs = set(), []
    for term in cfg["fetching"].get("workday_search_terms") or [""]:
        for offset in range(0, 100, 20):
            data = _post(s, url, {"appliedFacets": {}, "limit": 20, "offset": offset,
                                  "searchText": term}).json()
            posts = data.get("jobPostings", [])
            for j in posts:
                path = j.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                jobs.append(_job(f"workday:{base}{path}", j.get("title"), src["company"],
                                 j.get("locationsText"), f"{base}/{site}{path}",
                                 j.get("postedOn"), src["company"]))
            if len(posts) < 20:
                break
    return jobs


def fetch_recruitee(s, src, cfg):
    data = _get(s, src["feed_url"]).json()
    jobs = []
    for j in data.get("offers", []):
        loc = j.get("location") or ", ".join(x for x in [j.get("city"), j.get("country")] if x)
        if j.get("remote"):
            loc += " (Remote)"
        jobs.append(_job(f"recruitee:{j['id']}", j.get("title"), src["company"], loc,
                         j.get("careers_url"), j.get("published_at"), src["company"]))
    return jobs


def fetch_bamboohr(s, src, cfg):
    url = src["feed_url"]
    host = urlparse(url).netloc
    data = _get(s, url, headers={"Accept": "application/json"}).json()
    jobs = []
    for j in data.get("result", []):
        l = j.get("location") or {}
        loc = ", ".join(x for x in [l.get("city"), l.get("state"), l.get("country")] if x)
        if j.get("isRemote"):
            loc += " (Remote)"
        jobs.append(_job(f"bamboohr:{host}:{j['id']}", j.get("jobOpeningName"), src["company"], loc,
                         f"https://{host}/careers/{j['id']}", "", src["company"]))
    return jobs


# ── Web pages: company careers pages and job boards ────────────────────────

JOB_PATH_HINT = re.compile(r"/(jobs?|careers?|vacanc|openings?|positions?|apply|role|opportunit)", re.I)
SKIP_TEXT = re.compile(r"^(apply( now)?|view( all)?( jobs)?|read more|learn more|see more|more|details|"
                       r"careers?|jobs?|home|about( us)?|contact( us)?|login|sign in|next|previous)$", re.I)


def _jsonld_jobs(soup, page_url, src):
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if not isinstance(it, dict) or it.get("@type") != "JobPosting":
                continue
            loc = it.get("jobLocation") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
            locs = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressCountry")
                                          if isinstance(addr.get("addressCountry"), str) else None] if x)
            org = it.get("hiringOrganization") or {}
            url = it.get("url") or page_url
            out.append(_job(f"web:{url}#{it.get('title')}", it.get("title"),
                            org.get("name") if isinstance(org, dict) and org.get("name") else src["company"],
                            locs, url, it.get("datePosted"), src.get("label") or src["company"]))
    return out


def _company_from_title(title):
    m = re.search(r"\s+at\s+(.+)$", title, re.I)
    if m and len(m.group(1)) < 80:
        return title[:m.start()].strip(), m.group(1).strip().title()
    return title, None


def parse_html_jobs(html, page_url, src):
    """Pull job-looking links out of a careers page or job board page."""
    soup = BeautifulSoup(html, "html.parser")
    jobs = _jsonld_jobs(soup, page_url, src)
    seen = {j["url"] for j in jobs}
    page_host = urlparse(page_url).netloc
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(page_url, href).split("#")[0]
        if url.rstrip("/") == page_url.rstrip("/") or url in seen:
            continue
        heading = a.find(["h1", "h2", "h3", "h4", "h5", "strong"])
        text = " ".join((heading.get_text(" ") if heading else a.get_text(" ")).split())
        if not (4 <= len(text) <= 140) or SKIP_TEXT.match(text):
            continue
        host = urlparse(url).netloc
        if host != page_host and not any(re.search(p, url) for p in ATS_HOST_PATTERNS.values()):
            continue
        if not JOB_PATH_HINT.search(urlparse(url).path) and host == page_host:
            # Many boards use /job/<id>; otherwise require a job-ish path to avoid nav links.
            if not re.search(r"/\d{3,}", url):
                continue
        seen.add(url)
        title, company = _company_from_title(text)
        jobs.append(_job(f"web:{url}", title, company or src["company"], "", url, "",
                         src.get("label") or src["company"]))
    return jobs


def find_ats_links(html):
    """Spot links to a hosted job board on a careers page (used for self-healing hints)."""
    found = {}
    for name, pat in ATS_HOST_PATTERNS.items():
        m = re.search(pat, html)
        if m:
            found[name] = m.group(0)
    return found


def fetch_html(s, src, cfg):
    url = src.get("page_url")
    r = _get(s, url)
    jobs = parse_html_jobs(r.text, r.url, src)
    src["_ats_hint"] = find_ats_links(r.text)
    if not jobs and cfg["fetching"].get("use_browser"):
        jobs = fetch_with_browser(src, cfg)
    return jobs


def fetch_with_browser(src, cfg):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise FetchError("browser not installed (set use_browser and USE_BROWSER)") from e
    url = src.get("page_url")
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(user_agent=UA)
        page.goto(url, timeout=cfg["fetching"]["timeout_seconds"] * 1000, wait_until="networkidle")
        html = page.content()
        final = page.url
        b.close()
    return parse_html_jobs(html, final, src)


def fetch_js(s, src, cfg):
    if cfg["fetching"].get("use_browser"):
        return fetch_with_browser(src, cfg)
    return fetch_html(s, src, cfg)


def fetch_rss(s, src, cfg):
    r = _get(s, src["page_url"])
    soup = BeautifulSoup(r.content, "xml")
    jobs = []
    for it in soup.find_all(["item", "entry"]):
        title = (it.find("title").get_text(strip=True) if it.find("title") else "")
        link_tag = it.find("link")
        link = (link_tag.get("href") or link_tag.get_text(strip=True)) if link_tag else ""
        date = it.find(["pubDate", "published", "updated"])
        title, company = _company_from_title(title)
        jobs.append(_job(f"web:{link}", title, company or src["company"], "", link,
                         date.get_text(strip=True) if date else "", src.get("label") or src["company"]))
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workable": fetch_workable,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "recruitee": fetch_recruitee,
    "bamboohr": fetch_bamboohr,
    "html": fetch_html,
    "js_rendered": fetch_js,
    "rss": fetch_rss,
}
