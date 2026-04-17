"""Parallel-probe Greenhouse/Ashby/Lever tokens and write the live ones to
`data/live_boards.json` for the Streamlit app to load at startup.

Run once offline when expanding the pool:
    uv run python tools/probe_boards.py
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

# Hand-curated candidate list across YC, a16z, notable tech, AI-native,
# enterprise-sec, semiconductors, fintech, and consumer. Not every token
# will be live; we probe and keep the survivors.
GH_CANDIDATES = [
    # AI native / foundation models
    "anthropic", "openai", "perplexity", "glean", "cohere", "mistralai",
    "scaleai", "huggingface", "runwayml", "elevenlabs", "character", "inflection",
    "adept", "replicate", "together", "fireworks", "modal", "anyscale",
    # devtools / infra
    "vercel", "linear", "supabase", "render", "fly", "planetscale", "neon",
    "pulumi", "hashicorp", "gitlab", "gitlabhq", "jfrog", "circleci",
    "launchdarkly", "chargebee", "postman", "airbyte", "dagster", "prefect",
    "temporal", "meilisearch", "retool", "appsmith", "freshworks",
    # security
    "okta", "zscaler", "cloudflare", "rubrik", "abnormalsecurity",
    "snyk", "chainguard", "nucleussecurity", "semgrep", "legitsecurity",
    "traceable", "anchore",
    # observability / data
    "datadog", "newrelic", "elastic", "chronosphere", "honeycomb", "grafana",
    "grafanalabs", "sentry",
    # fintech
    "brex", "mercury", "ramp", "rippling", "deel", "gusto", "carta",
    "stripe", "plaid", "chime", "klarna", "wise", "mesh",
    # consumer / scale SaaS
    "notion", "figma", "canva", "airbnb", "pinterest", "coinbase",
    "instacart", "doordash", "lyft", "uber", "slack", "atlassian",
    "shopify", "spotify", "twilio", "zoom", "dropbox", "reddit",
    "duolingo", "etsy", "openai-startup-fund",
    # ai chip / semis
    "lightmatter", "graphcore", "tenstorrent", "rain-ai", "liquid-ai",
    # health / bio
    "benchling", "ginkgo", "recursion", "tempus", "veeva", "roivant",
    # logistics + industrial
    "flexport", "convoy", "benchmark",
    # GTM / sales
    "clay-labs", "outreach", "apollo-io", "zendesk", "drift", "qualified",
    "lucid-software", "lucid", "airtable", "notion-hq", "productboard",
    # more YC / startup
    "cruise", "zipline", "boom", "vivo", "replit", "whatnot", "openphone",
    "vouch", "mutiny", "whop", "seamai", "taro", "spicy",
    "bolt", "appsmith", "dagster-labs", "propertyguru",
]

ASHBY_CANDIDATES = [
    "notion", "ramp", "clay", "unify", "attio", "retool",
    "cal", "resend", "trigger", "dub", "mercury",
    "snyk", "wiz", "1password", "semgrep", "plaid", "loom",
    "etched", "astera",
    # YC / a16z adjacent
    "linear", "character-ai", "character", "windsurf", "cursor",
    "stackblitz", "jasper", "harvey", "hightouch", "census",
    "material-security", "prismatic", "baseten", "runwayml", "nuro",
    "stytch", "pomelo", "sourcegraph", "launchdarkly", "posthog", "supabase",
    "docker", "figma", "grafana-labs", "dub-co", "vapi", "pinata",
    "truora", "abstract", "cortex", "honeycomb", "replo",
    "replicate", "resend-co", "langchain", "langfuse", "convex",
    "deepgram", "gradient-ai", "mintlify", "cyrus", "appsmith",
    "supabase-inc", "flightcontrol", "default-com", "default",
    "persana", "11x", "artisan",
]

LEVER_CANDIDATES = [
    "netflix", "spotify", "pinterest", "palantir", "yelp", "segment",
    "etsy", "shopify", "quora", "affirm", "scribd", "eventbrite",
    "expensify", "khanacademy", "crunchbase", "blendlabs", "atlassian",
    "box", "flatiron", "harvest", "homeadvisor", "lattice", "mixpanel",
    "olx", "pento", "plannerhive", "podium", "ripple", "strava",
    "teachable", "toptal", "webflow", "workrise", "wonderschool",
]


async def probe(client: httpx.AsyncClient, url: str) -> int:
    try:
        r = await client.get(url, timeout=6.0)
        return r.status_code
    except Exception:  # noqa: BLE001
        return 0


async def main() -> None:
    out = {"greenhouse": [], "ashby": [], "lever": []}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        sem = asyncio.Semaphore(25)

        async def one(service: str, tok: str) -> None:
            urls = {
                "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs",
                "ashby":      f"https://api.ashbyhq.com/posting-api/job-board/{tok}",
                "lever":      f"https://api.lever.co/v0/postings/{tok}?mode=json",
            }
            async with sem:
                code = await probe(client, urls[service])
            if code == 200:
                out[service].append(tok)
                print(f"  [ok] {service:10s} {tok}")

        tasks = []
        for tok in sorted(set(GH_CANDIDATES)):
            tasks.append(one("greenhouse", tok))
        for tok in sorted(set(ASHBY_CANDIDATES)):
            tasks.append(one("ashby", tok))
        for tok in sorted(set(LEVER_CANDIDATES)):
            tasks.append(one("lever", tok))
        await asyncio.gather(*tasks)

    print(f"\nliving tokens: gh={len(out['greenhouse'])} "
          f"ashby={len(out['ashby'])} lever={len(out['lever'])}")
    out_path = Path("signalforge/resources/live_boards.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
