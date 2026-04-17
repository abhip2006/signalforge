"""Lightweight board-token → real domain + company name map.

Greenhouse/Ashby/Lever's public APIs do not reliably expose a company's canonical
domain. Rather than spraying a third-party "autocomplete" API per row, we keep
a small curated registry here. Add entries as the ICP config evolves — or
override with `icp.yaml`:

    sources:
      greenhouse:
        boards:
          - {token: anthropic, domain: anthropic.com, name: Anthropic}

This module accepts either a plain string (legacy) or a dict form.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BoardEntry:
    token: str
    domain: str
    name: str


# Curated registry: feel free to extend.
KNOWN: dict[str, tuple[str, str]] = {
    # token → (domain, display_name)
    "anthropic": ("anthropic.com", "Anthropic"),
    "openai": ("openai.com", "OpenAI"),
    "scaleai": ("scale.com", "Scale AI"),
    "perplexity": ("perplexity.ai", "Perplexity"),
    "perplexityai": ("perplexity.ai", "Perplexity"),
    "glean": ("glean.com", "Glean"),
    "cohere": ("cohere.com", "Cohere"),
    "mistralai": ("mistral.ai", "Mistral AI"),
    "notion": ("notion.so", "Notion"),
    "ramp": ("ramp.com", "Ramp"),
    "clay": ("clay.com", "Clay"),
    "unify": ("unifygtm.com", "Unify"),
    "unifygtm": ("unifygtm.com", "Unify"),
    "default": ("default.com", "Default"),
    "common-room": ("commonroom.io", "Common Room"),
    "commonroom": ("commonroom.io", "Common Room"),
    "pocus": ("pocus.com", "Pocus"),
    "apollo": ("apollo.io", "Apollo"),
    "hubspot": ("hubspot.com", "HubSpot"),
    "retool": ("retool.com", "Retool"),
    "attio": ("attio.com", "Attio"),
    "rippling": ("rippling.com", "Rippling"),
    "11x": ("11x.ai", "11x"),
    "artisan": ("artisan.co", "Artisan"),
    "instrumentl": ("instrumentl.com", "Instrumentl"),
    "regie": ("regie.ai", "Regie.ai"),
    "smartlead": ("smartlead.ai", "Smartlead"),
    "instantly": ("instantly.ai", "Instantly"),
    "koala": ("getkoala.com", "Koala"),
    "vector": ("vector.co", "Vector"),
    "rb2b": ("rb2b.com", "RB2B"),
    "warmly": ("warmly.ai", "Warmly"),
    "anthropics": ("anthropic.com", "Anthropic"),  # github org
    "clay-labs": ("clay.com", "Clay"),
    "persana": ("persana.ai", "Persana"),
    # Added April 2026 — candidate-pool expansion
    "huggingface": ("huggingface.co", "Hugging Face"),
    "runwayml": ("runwayml.com", "Runway"),
    "elevenlabs": ("elevenlabs.io", "ElevenLabs"),
    "linear": ("linear.app", "Linear"),
    "vercel": ("vercel.com", "Vercel"),
    "supabase": ("supabase.com", "Supabase"),
    "render": ("render.com", "Render"),
    "fly": ("fly.io", "Fly.io"),
    "replicate": ("replicate.com", "Replicate"),
    "brex": ("brex.com", "Brex"),
    "mercury": ("mercury.com", "Mercury"),
    "deel": ("deel.com", "Deel"),
    "gusto": ("gusto.com", "Gusto"),
    "cal": ("cal.com", "Cal.com"),
    "resend": ("resend.com", "Resend"),
    "trigger": ("trigger.dev", "Trigger.dev"),
    "dub": ("dub.co", "Dub"),
    # GitHub orgs
    "cohere-ai": ("cohere.com", "Cohere"),
    # Added April 2026 pool expansion — enterprise security + scale SaaS
    "okta": ("okta.com", "Okta"),
    "zscaler": ("zscaler.com", "Zscaler"),
    "cloudflare": ("cloudflare.com", "Cloudflare"),
    "rubrik": ("rubrik.com", "Rubrik"),
    "abnormalsecurity": ("abnormal.ai", "Abnormal Security"),
    "datadog": ("datadoghq.com", "Datadog"),
    "newrelic": ("newrelic.com", "New Relic"),
    "gitlab": ("gitlab.com", "GitLab"),
    "jfrog": ("jfrog.com", "JFrog"),
    "snyk": ("snyk.io", "Snyk"),
    "wiz": ("wiz.io", "Wiz"),
    "1password": ("1password.com", "1Password"),
    "semgrep": ("semgrep.dev", "Semgrep"),
    "plaid": ("plaid.com", "Plaid"),
    "loom": ("loom.com", "Loom"),
    "stripe": ("stripe.com", "Stripe"),
    "airbnb": ("airbnb.com", "Airbnb"),
    "pinterest": ("pinterest.com", "Pinterest"),
    "coinbase": ("coinbase.com", "Coinbase"),
    "instacart": ("instacart.com", "Instacart"),
    # Semiconductors + AI-chip startups
    "lightmatter": ("lightmatter.co", "Lightmatter"),
    "graphcore": ("graphcore.ai", "Graphcore"),
    "tenstorrent": ("tenstorrent.com", "Tenstorrent"),
    "etched": ("etched.com", "Etched"),
    "astera": ("asteralabs.com", "Astera Labs"),
    "nvidia": ("nvidia.com", "NVIDIA"),
    "amd": ("amd.com", "AMD"),
    "intel": ("intel.com", "Intel"),
    "broadcom": ("broadcom.com", "Broadcom"),
    "qualcomm": ("qualcomm.com", "Qualcomm"),
    "tsmc": ("tsmc.com", "TSMC"),
    "asml": ("asml.com", "ASML"),
    "synopsys": ("synopsys.com", "Synopsys"),
    "cadence": ("cadence.com", "Cadence Design Systems"),
    "arm": ("arm.com", "Arm"),
    "micron": ("micron.com", "Micron Technology"),
    "marvell": ("marvell.com", "Marvell Technology"),
    "analog": ("analog.com", "Analog Devices"),
    "ti": ("ti.com", "Texas Instruments"),
    # Added for 1000+ pool via tools/probe_boards.py
    "chainguard": ("chainguard.dev", "Chainguard"),
    "airtable": ("airtable.com", "Airtable"),
    "carta": ("carta.com", "Carta"),
    "circleci": ("circleci.com", "CircleCI"),
    "chime": ("chime.com", "Chime"),
    "dropbox": ("dropbox.com", "Dropbox"),
    "elastic": ("elastic.co", "Elastic"),
    "duolingo": ("duolingo.com", "Duolingo"),
    "figma": ("figma.com", "Figma"),
    "flexport": ("flexport.com", "Flexport"),
    "grafanalabs": ("grafana.com", "Grafana Labs"),
    "honeycomb": ("honeycomb.io", "Honeycomb"),
    "launchdarkly": ("launchdarkly.com", "LaunchDarkly"),
    "mesh": ("meshtalent.com", "Mesh"),
    "lyft": ("lyft.com", "Lyft"),
    "mutiny": ("mutinyhq.com", "Mutiny"),
    "planetscale": ("planetscale.com", "PlanetScale"),
    "postman": ("postman.com", "Postman"),
    "reddit": ("reddit.com", "Reddit"),
    "twilio": ("twilio.com", "Twilio"),
    "temporal": ("temporal.io", "Temporal"),
    "whop": ("whop.com", "Whop"),
    "character": ("character.ai", "Character.AI"),
    "baseten": ("baseten.co", "Baseten"),
    "cursor": ("cursor.com", "Cursor"),
    "deepgram": ("deepgram.com", "Deepgram"),
    "hightouch": ("hightouch.com", "Hightouch"),
    "langchain": ("langchain.com", "LangChain"),
    "harvey": ("harvey.ai", "Harvey"),
    "posthog": ("posthog.com", "PostHog"),
    "stytch": ("stytch.com", "Stytch"),
    "vapi": ("vapi.ai", "Vapi"),
    "mintlify": ("mintlify.com", "Mintlify"),
    "replo": ("replo.app", "Replo"),
    "langfuse": ("langfuse.com", "Langfuse"),
    "docker": ("docker.com", "Docker"),
    "atlassian": ("atlassian.com", "Atlassian"),
    "spotify": ("spotify.com", "Spotify"),
    "toptal": ("toptal.com", "Toptal"),
    "palantir": ("palantir.com", "Palantir"),
}


def resolve_board(token_or_entry: Any, fallback_source: str = "unknown") -> BoardEntry:
    """Accept `str` token or `{token, domain, name}` dict. Always returns a BoardEntry."""
    if isinstance(token_or_entry, dict):
        token = str(token_or_entry.get("token") or token_or_entry.get("slug") or "")
        domain = str(token_or_entry.get("domain") or "")
        name = str(token_or_entry.get("name") or token_or_entry.get("display_name") or "")
    else:
        token = str(token_or_entry)
        domain = ""
        name = ""

    if not domain or not name:
        known = KNOWN.get(token.lower())
        if known:
            d, n = known
            domain = domain or d
            name = name or n

    if not domain:
        domain = f"{token}.unknown"
    if not name:
        name = token.replace("-", " ").replace("_", " ").title()
    return BoardEntry(token=token, domain=domain, name=name)


def resolve_list(items: list[Any]) -> list[BoardEntry]:
    return [resolve_board(item) for item in items]
