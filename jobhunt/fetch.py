"""Fetch jobs from public ATS APIs. No auth, no scraping, no ToS risk."""
from __future__ import annotations

import html
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from typing import Any, Iterable

import requests

UA = {"User-Agent": "jobhunt/1.0 (personal job search agent)"}
TIMEOUT = 20

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", text, flags=re.I)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text)
    return text.strip()


@dataclass
class Job:
    job_id: str          # stable global id for dedupe: "<ats>:<slug>:<id>"
    ats: str
    company: str
    title: str
    location: str
    url: str
    description: str
    posted_at: str | None = None
    salary: str | None = None
    locations: list[str] = field(default_factory=list)
    is_remote: bool = False
    workplace_type: str | None = None
    # filled in later by the pipeline
    score: float | None = None
    reason: str | None = None
    draft: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Adapters. Each takes the raw JSON body and returns list[Job].
# Keeping parse separate from HTTP is what makes offline testing possible.
# --------------------------------------------------------------------------

def _extract_remote_flag(texts: list[str]) -> bool:
    hay = " ".join(texts).lower()
    return any(h in hay for h in ("remote", "anywhere", "work from home", "wfh", "telecommuting", "distributed"))


def parse_greenhouse(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or {}).get("jobs", []):
        loc_main = (j.get("location") or {}).get("name") or ""
        offices = [o.get("name") for o in (j.get("offices") or []) if isinstance(o, dict) and o.get("name")]
        secondaries = [s.get("name") for s in (j.get("secondary_locations") or []) if isinstance(s, dict) and s.get("name")]
        
        all_locs = [loc for loc in ([loc_main] + offices + secondaries) if loc]
        unique_locs = list(dict.fromkeys(all_locs))
        primary_loc = loc_main or (unique_locs[0] if unique_locs else "")
        is_remote = _extract_remote_flag(unique_locs + [(j.get("title") or "")])

        out.append(Job(
            job_id=f"greenhouse:{slug}:{j.get('id')}",
            ats="greenhouse",
            company=company,
            title=(j.get("title") or "").strip(),
            location=primary_loc.strip(),
            locations=unique_locs,
            is_remote=is_remote,
            workplace_type="remote" if is_remote else None,
            url=j.get("absolute_url") or "",
            description=strip_html(j.get("content")),
            posted_at=j.get("updated_at") or j.get("first_published"),
        ))
    return out


def parse_lever(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or []):
        cats = j.get("categories") or {}
        primary_loc = (cats.get("location") or "").strip()
        all_locs = [primary_loc]
        raw_all = cats.get("allLocations") or []
        if isinstance(raw_all, list):
            for item in raw_all:
                if isinstance(item, str) and item:
                    all_locs.append(item.strip())
                elif isinstance(item, dict) and item.get("name"):
                    all_locs.append(item["name"].strip())
        
        unique_locs = list(dict.fromkeys([l for l in all_locs if l]))
        workplace_type = (j.get("workplaceType") or "").lower() or None
        is_remote = workplace_type == "remote" or _extract_remote_flag(unique_locs + [(j.get("text") or "")])

        # Lever splits the JD across descriptionPlain + a `lists` array.
        chunks = [j.get("descriptionPlain") or strip_html(j.get("description"))]
        for lst in (j.get("lists") or []):
            chunks.append(str(lst.get("text") or ""))
            chunks.append(strip_html(lst.get("content")))
        chunks.append(j.get("additionalPlain") or strip_html(j.get("additional")))
        ts = j.get("createdAt")
        posted = None
        if isinstance(ts, (int, float)):
            posted = time.strftime("%Y-%m-%d", time.gmtime(ts / 1000))

        out.append(Job(
            job_id=f"lever:{slug}:{j.get('id')}",
            ats="lever",
            company=company,
            title=(j.get("text") or "").strip(),
            location=primary_loc,
            locations=unique_locs,
            is_remote=is_remote,
            workplace_type=workplace_type or ("remote" if is_remote else None),
            url=j.get("hostedUrl") or j.get("applyUrl") or "",
            description="\n\n".join(c for c in chunks if c).strip(),
            posted_at=posted,
            salary=cats.get("commitment"),
        ))
    return out


def parse_ashby(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    for j in (body or {}).get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = j.get("compensation") or {}
        salary = None
        summary = comp.get("compensationTierSummary") or comp.get("summaryComponents")
        if isinstance(summary, str):
            salary = summary
            
        primary_loc = (j.get("location") or "").strip()
        secondaries = [s.get("location") for s in (j.get("secondaryLocations") or []) if isinstance(s, dict) and s.get("location")]
        all_locs = list(dict.fromkeys([l for l in ([primary_loc] + secondaries) if l]))
        is_remote = bool(j.get("isRemote")) or _extract_remote_flag(all_locs + [(j.get("title") or "")])

        out.append(Job(
            job_id=f"ashby:{slug}:{j.get('id')}",
            ats="ashby",
            company=company,
            title=(j.get("title") or "").strip(),
            location=primary_loc,
            locations=all_locs,
            is_remote=is_remote,
            workplace_type="remote" if is_remote else None,
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            description=(j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")) or "").strip(),
            posted_at=j.get("publishedAt"),
            salary=salary,
        ))
    return out


def parse_workable(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    jobs = (body or {}).get("jobs") or []
    if isinstance(body, list):
        jobs = body
    for j in jobs:
        loc_obj = j.get("location") or {}
        city = loc_obj.get("city") or loc_obj.get("region") or ""
        country = loc_obj.get("country") or ""
        is_telecommuting = bool(loc_obj.get("telecommuting") or j.get("telecommuting"))
        parts = [p for p in (city, country) if p]
        if is_telecommuting and "remote" not in " ".join(parts).lower():
            parts.append("Remote")
        loc_str = ", ".join(parts)
        shortcode = j.get("shortcode") or j.get("code") or str(j.get("id"))
        workplace_type = j.get("workplace_type") or ("remote" if is_telecommuting else None)
        is_remote = is_telecommuting or workplace_type == "remote" or _extract_remote_flag([loc_str, j.get("title") or ""])

        out.append(Job(
            job_id=f"workable:{slug}:{shortcode}",
            ats="workable",
            company=company,
            title=(j.get("title") or "").strip(),
            location=loc_str.strip(),
            locations=[loc_str.strip()] if loc_str.strip() else [],
            is_remote=is_remote,
            workplace_type=workplace_type,
            url=j.get("url") or f"https://apply.workable.com/j/{shortcode}",
            description=strip_html(j.get("description") or j.get("summary")),
            posted_at=j.get("published") or j.get("published_on") or j.get("created_at"),
            salary=j.get("type") or j.get("employment_type"),
        ))
    return out


def parse_smartrecruiters(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    items = (body or {}).get("content") or []
    if isinstance(body, list):
        items = body
    for j in items:
        loc_obj = j.get("location") or {}
        city = loc_obj.get("city") or loc_obj.get("region") or ""
        country = loc_obj.get("country") or ""
        is_remote_flag = bool(loc_obj.get("remote"))
        parts = [p for p in (city, country) if p]
        if is_remote_flag and "remote" not in " ".join(parts).lower():
            parts.append("Remote")
        loc_str = ", ".join(parts)
        jid = str(j.get("id") or "")
        emp_type = (j.get("typeOfEmployment") or {}).get("label") if isinstance(j.get("typeOfEmployment"), dict) else j.get("typeOfEmployment")
        is_remote = is_remote_flag or _extract_remote_flag([loc_str, j.get("name") or ""])

        out.append(Job(
            job_id=f"smartrecruiters:{slug}:{jid}",
            ats="smartrecruiters",
            company=company,
            title=(j.get("name") or j.get("title") or "").strip(),
            location=loc_str.strip(),
            locations=[loc_str.strip()] if loc_str.strip() else [],
            is_remote=is_remote,
            workplace_type="remote" if is_remote else None,
            url=f"https://jobs.smartrecruiters.com/{slug}/{jid}",
            description=strip_html(j.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text") or j.get("description")),
            posted_at=j.get("releasedDate") or j.get("createdOn"),
            salary=emp_type,
        ))
    return out


def parse_bamboohr(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    items = (body or {}).get("result") or (body if isinstance(body, list) else [])
    for j in items:
        loc = j.get("location") or {}
        if isinstance(loc, str):
            loc_str = loc
        else:
            city = loc.get("city") or loc.get("state") or ""
            country = loc.get("country") or ""
            parts = [p for p in (city, country) if p]
            loc_str = ", ".join(parts)
        is_remote_type = j.get("locationType") == "remote"
        if is_remote_type and "remote" not in loc_str.lower():
            loc_str = f"{loc_str}, Remote" if loc_str else "Remote"
        jid = str(j.get("id") or "")
        is_remote = is_remote_type or _extract_remote_flag([loc_str, j.get("jobOpeningName") or ""])

        out.append(Job(
            job_id=f"bamboohr:{slug}:{jid}",
            ats="bamboohr",
            company=company,
            title=(j.get("jobOpeningName") or j.get("title") or "").strip(),
            location=loc_str.strip(),
            locations=[loc_str.strip()] if loc_str.strip() else [],
            is_remote=is_remote,
            workplace_type="remote" if is_remote else None,
            url=f"https://{slug}.bamboohr.com/careers/{jid}",
            description=strip_html(j.get("description") or j.get("jobDescription")),
            posted_at=j.get("postedDate") or j.get("date"),
            salary=j.get("employmentType"),
        ))
    return out


def parse_workday(slug: str, company: str, body: Any) -> list[Job]:
    out = []
    items = (body or {}).get("jobPostings") or []
    for j in items:
        ext_path = j.get("externalPath") or ""
        raw_id = ext_path.split("/")[-1] if ext_path else str(j.get("bulletFields", [""])[0])
        jid = raw_id.replace("_", "-") or "job"
        url = f"https://{slug}.wd1.myworkdayjobs.com{ext_path}" if ext_path.startswith("/") else ext_path
        loc_raw = (j.get("locationsText") or "").strip()
        loc_parts = [p.strip() for p in loc_raw.replace(";", ",").split(",") if p.strip()]
        is_remote = _extract_remote_flag([loc_raw, j.get("title") or ""])

        out.append(Job(
            job_id=f"workday:{slug}:{jid}",
            ats="workday",
            company=company,
            title=(j.get("title") or "").strip(),
            location=loc_raw,
            locations=loc_parts,
            is_remote=is_remote,
            workplace_type="remote" if is_remote else None,
            url=url,
            description=strip_html(j.get("bulletFields", [""])[0] if isinstance(j.get("bulletFields"), list) else ""),
            posted_at=j.get("postedOn"),
        ))
    return out



ENDPOINTS = {
    "greenhouse":      ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", parse_greenhouse, "GET", None),
    "lever":           ("https://api.lever.co/v0/postings/{slug}?mode=json", parse_lever, "GET", None),
    "ashby":           ("https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", parse_ashby, "GET", None),
    "workable":        ("https://apply.workable.com/api/v1/widget/accounts/{slug}", parse_workable, "GET", None),
    "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{slug}/postings", parse_smartrecruiters, "GET", None),
    "bamboohr":        ("https://{slug}.bamboohr.com/careers/list", parse_bamboohr, "GET", None),
    "workday":         ("https://{slug}.wd1.myworkdayjobs.com/wday/cxs/{slug}/jobs", parse_workday, "POST", lambda: {"appliedFacets": {}, "limit": 50, "offset": 0}),
}


def fetch_board(ats: str, slug: str, company: str | None = None,
                session: requests.Session | None = None) -> list[Job]:
    """Hit one company's public board. Returns [] on any failure (never raises)."""
    if ats not in ENDPOINTS:
        raise ValueError(f"unknown ATS: {ats}")
    entry = ENDPOINTS[ats]
    url_tpl, parser = entry[0], entry[1]
    method = entry[2] if len(entry) > 2 else "GET"
    payload_fn = entry[3] if len(entry) > 3 else None

    sess = session or requests
    try:
        if method == "POST":
            payload = payload_fn() if payload_fn else {}
            r = sess.post(url_tpl.format(slug=slug), json=payload, headers=UA, timeout=TIMEOUT)
        else:
            r = sess.get(url_tpl.format(slug=slug), headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"  ! {ats}/{slug} -> HTTP {r.status_code}")
            return []
        return parser(slug, company or slug, r.json())
    except Exception as e:  # dead slug, rate limit, network blip
        print(f"  ! {ats}/{slug} -> {type(e).__name__}: {e}")
        return []


def fetch_all(companies: Iterable[dict], sleep: float = 0.0, max_workers: int = 10) -> list[Job]:
    jobs: list[Job] = []
    company_list = list(companies)
    if not company_list:
        return jobs

    def _worker(c: dict) -> tuple[dict, list[Job]]:
        sess = requests.Session()
        got = fetch_board(c["ats"], c["slug"], c.get("name"), session=sess)
        return c, got

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_worker, c) for c in company_list]
        for future in as_completed(futures):
            c, got = future.result()
            if got:
                print(f"  {c.get('name') or c['slug']:<28} {len(got):>4} jobs  ({c['ats']})")
            jobs.extend(got)
    return jobs

