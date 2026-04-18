"""Pull the full YC companies list from yc-oss/api (5,690+ companies),
probe each candidate slug against Greenhouse / Ashby / Lever public APIs,
merge the live hits into signalforge/resources/live_boards.json.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx


YC_DATASET = "https://yc-oss.github.io/api/companies/all.json"


def _slug_candidates(name: str, slug: str, website: str) -> list[str]:
    """Generate ATS slug candidates from YC's name / slug / website fields."""
    out: set[str] = set()

    def _norm(s: str) -> str:
        s = (s or "").lower().strip()
        s = re.sub(r"[^a-z0-9-]+", "", s)
        return s

    if slug:
        out.add(slug.lower())
        out.add(slug.lower().replace("-", ""))

    if name:
        base = _norm(name.replace(" ", "-"))
        if base:
            out.add(base)
            out.add(base.replace("-", ""))
            out.add(base + "hq")
            out.add(base + "inc")
            out.add(base + "co")

    if website:
        try:
            host = urlparse(website).netloc or urlparse(website).path
        except Exception:  # noqa: BLE001
            host = ""
        host = host.removeprefix("www.").split("/")[0]
        core = host.split(".")[0] if host else ""
        if core and re.match(r"^[a-z0-9-]+$", core):
            out.add(core)
            out.add(core.replace("-", ""))

    # Keep only sensible ATS slugs (3-40 chars, alphanum + hyphen).
    return [s for s in out if s and 3 <= len(s) <= 40 and re.match(r"^[a-z0-9-]+$", s)]


async def probe(client: httpx.AsyncClient, service: str, tok: str) -> bool:
    urls = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs",
        "ashby":      f"https://api.ashbyhq.com/posting-api/job-board/{tok}",
        "lever":      f"https://api.lever.co/v0/postings/{tok}?mode=json",
    }
    try:
        r = await client.get(urls[service], timeout=5.0)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def main() -> None:
    print(f"Fetching YC companies → {YC_DATASET}")
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.get(YC_DATASET)
        r.raise_for_status()
        companies = r.json()
    print(f"loaded {len(companies)} YC companies")

    all_candidates: set[str] = set()
    for co in companies:
        for s in _slug_candidates(co.get("name", ""), co.get("slug", ""),
                                  co.get("website", "")):
            all_candidates.add(s)
    print(f"unique candidate slugs: {len(all_candidates)}")

    # Only try each slug once, and since a slug typically lives on only one
    # ATS service, break out on the first hit (saves ~2/3 of probes).
    live: dict[str, set[str]] = {"greenhouse": set(), "ashby": set(), "lever": set()}
    sem = asyncio.Semaphore(60)
    hits = 0

    async with httpx.AsyncClient() as client:
        async def one(tok: str) -> None:
            nonlocal hits
            async with sem:
                for svc in ("ashby", "greenhouse", "lever"):
                    if await probe(client, svc, tok):
                        live[svc].add(tok)
                        hits += 1
                        if hits % 25 == 0:
                            print(f"  hits so far: {hits}  "
                                  f"(gh={len(live['greenhouse'])} "
                                  f"ashby={len(live['ashby'])} lever={len(live['lever'])})")
                        return

        await asyncio.gather(*(one(t) for t in sorted(all_candidates)))

    final = {k: sorted(v) for k, v in live.items()}
    total = sum(len(v) for v in final.values())
    print(f"\n=== YC probe result: {total} live ATS boards ===")
    print(f"  greenhouse={len(final['greenhouse'])}  "
          f"ashby={len(final['ashby'])}  lever={len(final['lever'])}")

    # Save dedicated artifact
    yc_path = Path("signalforge/resources/yc_boards.json")
    yc_path.parent.mkdir(parents=True, exist_ok=True)
    yc_path.write_text(json.dumps(final, indent=2, sort_keys=True))

    # Merge into master live_boards.json
    master = Path("signalforge/resources/live_boards.json")
    existing: dict[str, list[str]] = {"greenhouse": [], "ashby": [], "lever": []}
    if master.exists():
        existing = json.loads(master.read_text())
    for k in ("greenhouse", "ashby", "lever"):
        existing[k] = sorted(set(existing.get(k, [])) | set(final.get(k, [])))
    master.write_text(json.dumps(existing, indent=2, sort_keys=True))
    merged_total = sum(len(v) for v in existing.values())
    print(f"MERGED master now has {merged_total} live ATS boards")


if __name__ == "__main__":
    asyncio.run(main())
