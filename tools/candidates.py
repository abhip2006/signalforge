"""Mass candidate ATS board tokens. Startup-first per user priority —
YC, a16z, Sequoia, Accel, Benchmark, Greylock, Founders Fund, Forbes
Cloud 100, Forbes AI 50, plus well-known tech. Probed by tools/probe_boards.py.
"""
from __future__ import annotations


# ──────────────── Greenhouse ────────────────
GREENHOUSE = [
    # Foundation-model & AI research
    "anthropic", "openai", "perplexity", "cohere", "mistralai", "huggingface",
    "runwayml", "elevenlabs", "character", "inflection", "adept", "replicate",
    "together", "fireworks", "modal", "anyscale", "deepmind", "scale",
    "scaleai", "aleph-alpha", "stabilityai", "stability-ai", "writer",
    "jasper", "tome", "glean", "speak", "hume", "cartesia", "pika",
    "luma", "lumaai", "midjourney", "characterai", "suno", "crusoe",
    "covariant", "cresta", "decagon", "rilla", "intercom-ai",
    "normalcomputing", "poolside", "cognition", "groq", "codeium",
    "continue", "windsurf", "zed", "warp", "raycast", "arc-browser",
    # Devtools / infra / platforms
    "vercel", "linear", "supabase", "render", "fly", "planetscale", "neon",
    "pulumi", "hashicorp", "gitlab", "gitlabhq", "jfrog", "circleci",
    "launchdarkly", "chargebee", "postman", "airbyte", "dagster", "prefect",
    "temporal", "meilisearch", "retool", "appsmith", "freshworks",
    "stackoverflow", "jetbrains", "dbtlabs", "dbt-labs", "databricks",
    "confluent", "mongodb", "cockroachlabs", "timescale", "snowflake",
    "elastic", "chronosphere", "grafanalabs", "honeycomb", "sentry",
    "splitio", "split-io", "fastly", "launchdarkly-inc", "wandb",
    "weights-biases", "vault", "terraform", "ansible",
    "algolia", "kong", "docker", "tailscale", "ngrok",
    "buildkite", "spacelift", "env0", "terragrunt-inc", "aqua-security",
    # Dev productivity / workflow
    "asana", "monday", "clickup", "trello", "notion", "coda",
    "airtable", "smartsheet", "productboard", "miro", "mural",
    "figma", "framer", "canva", "sketch", "invision",
    # Security
    "okta", "zscaler", "cloudflare", "rubrik", "abnormalsecurity",
    "snyk", "chainguard", "nucleussecurity", "semgrep", "legitsecurity",
    "traceable", "anchore", "tenable", "nordsecurity", "sumologic",
    "bugcrowd", "hackerone", "netskope", "drata", "vanta",
    "hyperproof", "sailpoint", "duosecurity", "tessian",
    "beyondidentity", "forgerock", "material", "orca",
    "cyera", "cyberark", "prismacloud", "lacework", "wiz-inc",
    "sysdig", "axonius", "varonis", "netwrix", "arctic-wolf",
    "expel", "deepinstinct", "snyk-io", "datalocal", "dhound",
    "ironscales", "keepersecurity", "darktrace", "sonatype",
    # Fintech / banking
    "brex", "mercury", "ramp", "rippling", "deel", "gusto", "carta",
    "stripe", "plaid", "chime", "klarna", "wise", "mesh",
    "modern-treasury", "unit", "bond", "bondfinancial", "column",
    "wealthsimple", "robinhood", "m1finance", "affirm", "petalcard",
    "rho", "pilot", "puzzle", "routable", "upgrade", "teampay",
    "nium", "dowjones", "stripe-inc", "circlepay", "ripple",
    "roofstock", "bilt", "alpaca", "betterment", "ellevest",
    # Consumer / scale SaaS
    "notion-hq", "figma-inc", "canva-inc", "airbnb", "pinterest",
    "coinbase", "instacart", "doordash", "lyft", "uber", "slack",
    "atlassian", "shopify", "spotify", "twilio", "zoom", "dropbox",
    "reddit", "duolingo", "etsy", "nextdoor", "strava", "discord",
    "patreon", "quora", "roblox", "eventbrite", "grubhub", "yelp",
    "zillow", "redfin", "thumbtack", "taskrabbit", "opendoor",
    "hubspot", "wistia", "squarespace", "webflow", "ghost",
    "substack", "beehiiv", "typeform", "kajabi", "gumroad",
    # Enterprise SaaS
    "workday", "samsara", "clio", "aircall", "intercom", "asana-inc",
    "mondaycom", "jira", "notion-hq-com", "lucidchart", "lucid",
    "smartsheet-inc", "productboard-inc", "amplitude", "mixpanel",
    "heap", "fullstory", "pendo", "segment", "zendesk", "drift",
    "qualified", "outreach", "salesloft", "gong", "chorus",
    "clari", "6sense", "demandbase", "zoominfo",
    # AI chip / semis / compute
    "lightmatter", "graphcore", "tenstorrent", "rain-ai", "rainai",
    "liquidai", "mythic", "sambanova", "cerebras", "groq-inc",
    "rebellions", "modulor", "d-matrix", "tachyum", "hailo",
    "etched", "lepton-ai", "vast-ai", "coreweave",
    # Health / bio
    "benchling", "ginkgo", "recursion", "tempus", "veeva", "roivant",
    "relate", "headway", "hims", "ro", "curology", "onemedical",
    "forward", "calibrate", "noom", "talkspace", "lyrahealth",
    "spring-health", "lyra", "grouphealth", "carrot-fertility",
    "cerebral", "maven-clinic", "progyny", "komodohealth",
    "flatiron-health", "cohere-health", "truepill", "amwell",
    # Logistics / industrial / mobility
    "flexport", "convoy", "samsara-inc", "nuro", "cruise", "zipline",
    "boom", "joby-aviation", "archer-aviation", "skydio",
    "bright-machines", "formic", "veoride", "bird", "lime",
    "getaround", "turo", "flyin", "kodiak-robotics", "waymo",
    # Climate / energy
    "climeworks", "orca", "stripe-climate", "watershed", "persefoni",
    "sightly", "clear-ag", "indigo-ag", "plenty", "bowery",
    # Crypto / web3
    "gemini", "kraken", "blockchain", "okx", "binance-us",
    "alchemy", "chainalysis", "fireblocks", "circlepay-io",
    "consensys", "chainguard-io", "alchemy-inc", "biconomy",
    "amberdata", "dapperlabs",
    # YC recent batches + famous founders
    "replit", "whatnot", "openphone", "vouch", "mutiny", "whop",
    "seamai", "taro", "spicy", "bolt", "missionlane",
    "hopper", "hopper-inc", "faire", "grammarly",
    "duolingo-inc", "docsend", "notion-io",
    # Analytics / BI
    "looker", "tableau", "mode", "hex", "preset",
    "thoughtspot", "sigma", "preset-inc",
    # E-commerce tools
    "shopify-inc", "bigcommerce", "mailchimp", "klaviyo",
    "attentive", "postscript", "yotpo", "recharge-payments",
    "rebuy", "zonos",
    # Education
    "coursera", "udemy", "masterclass", "skillshare",
    "brilliant", "khanacademy", "outschool", "chegg",
    "class-dojo", "newsela", "articulate",
    # Gaming / media
    "unity", "epic-games", "niantic", "supercell", "king",
    "zynga", "scopely", "machine-zone", "activision",
    # Dev productivity / IDEs
    "jetbrains-inc", "gitpod", "codesandbox", "coder",
    "tabnine", "greptile", "aider", "kodem",
    # Collaboration
    "miro-inc", "figjam", "notion-inc", "loom-inc",
    # Sales / CRM / GTM
    "lattice", "15five", "cultureamp", "lever-co",
    "greenhouse", "bamboohr", "workable-io", "recruitee",
    "ashby", "lever-labs", "gem",
    # Automation / iPaaS
    "zapier", "make", "ifttt", "airbyte-inc",
    "pipedream", "n8n", "parabola",
    # API / platform
    "algolia-inc", "mux", "livekit", "agora", "daily",
    "twilio-inc", "sendgrid", "mandrill", "postmark",
    # Web infra
    "vercel-inc", "netlify", "cloudflare-inc", "fastly-inc",
    "akamai", "stackpath", "bunny-net",
    # Data infra
    "starburst", "dremio", "firebolt", "rockset", "pinecone",
    "weaviate", "qdrant", "chroma", "zilliz", "couchbase",
    "arangodb", "redis", "edgedb", "clickhouse", "materialize",
    # MLOps / AI infra
    "arize", "weights-and-biases", "pachyderm", "tecton",
    "lightning-ai", "labelbox", "superb-ai", "hugging-face",
    "comet-ml", "paperspace", "runpod", "deci-ai",
    # Newer AI startups
    "harvey", "harvey-ai", "cursor", "vapi-ai", "deepgram",
    "langchain-inc", "langfuse", "mintlify", "convex-dev",
    "baseten-co", "modal-labs", "anyscale-inc", "together-ai",
    "fireworks-ai", "replicate-com", "humanloop",
    # Ops / ITSM
    "pagerduty", "opsgenie", "statuspage", "incident-io",
    "rootly", "firehydrant", "runreveal", "transposit",
    # Legal tech
    "ironclad", "evisort", "contractpodai", "lexion",
    "spotdraft", "legit-ai",
    # HR + payroll
    "rippling-inc", "justworks", "trinet", "gusto-hq",
    "deel-co", "oyster", "remote-com", "multiplier",
    # PLG + usage-based
    "amplitude-inc", "heap-io", "june-so", "mixpanel-inc",
    "segment-io", "statsig",
    # Consumer + D2C
    "shopify-hq", "glossier", "warby-parker", "allbirds",
    "mejuri", "casper-sleep", "bark-box", "bombas",
    # Insurance tech
    "lemonade", "next-insurance", "hippo-insurance", "root-insurance",
    "pie-insurance", "coalition", "vouch-insurance", "boost-insurance",
    # Real estate / proptech
    "roofstock-inc", "opendoor-labs", "bilt-rewards", "divvy-homes",
    "flyhomes", "orchard-inc", "homelight",
    # Freight / supply
    "flock-freight", "convoy-inc", "uber-freight", "ryder",
    # Vertical SaaS
    "toast", "sevenrooms", "teamworks", "procore",
    "buildertrend", "servicetitan", "housecallpro",
]

# ──────────────── Ashby (YC-heavy, newer startups) ────────────────
ASHBY = [
    # existing hits
    "notion", "ramp", "clay", "unify", "attio", "retool",
    "cal", "resend", "trigger", "dub", "mercury", "loom",
    "snyk", "wiz", "1password", "semgrep", "plaid",
    # AI + recent YC
    "character-ai", "character", "windsurf", "cursor",
    "stackblitz", "jasper", "harvey", "hightouch", "census",
    "material-security", "prismatic", "baseten", "runwayml", "nuro",
    "stytch", "pomelo", "sourcegraph", "launchdarkly", "posthog",
    "docker", "figma", "grafana-labs", "dub-co", "vapi", "pinata",
    "truora", "abstract", "cortex", "honeycomb", "replo",
    "replicate", "resend-co", "langchain", "langfuse", "convex",
    "deepgram", "gradient-ai", "mintlify", "cyrus", "appsmith",
    "supabase-inc", "flightcontrol", "default-com", "default",
    "persana", "11x", "artisan", "etched", "astera",
    # Newer hits
    "magic", "cognition", "poolside", "anysphere", "imbue",
    "suno", "superhuman", "11xai", "artisan-co", "clay-labs",
    "tabnine", "continue-dev", "gitpod-io", "codeium",
    "warp", "raycast", "arc", "linear-inc", "copilot",
    "vapi-ai", "bland", "wispr-flow", "granola", "notta",
    "otter-ai", "supernormal", "glean-ai", "speak",
    "modal", "anyscale", "together", "fireworks",
    "retool-com", "postman-inc", "coda-io", "airtable-inc",
    "linear-app", "vercel-inc", "neon", "planetscale",
    "supabase", "convex-dev",
    "clerk", "supertokens", "auth0", "ory", "workos",
    "descope", "corbado", "passkey",
    "loops", "customer-io", "onesignal", "braze",
    "iterable", "klaviyo", "mailmodo", "apolloio",
    "brex-inc", "mercury-com", "ramp-co", "deel-co",
    "rippling-co", "gusto-hq", "pave", "figure",
    "opendoor-labs", "unlock", "hopper", "notion-hq", "fathom",
    # More modern YC
    "every", "inngest", "replit-inc", "neondb", "convex",
    "mintlify-com", "orb", "meter", "rentspree", "dagger",
    "jellyfish", "sweep", "relevance-ai",
    "promptfoo", "weave", "wasp", "buildkit",
    "hume-ai", "embla", "embra", "spotdraft-ai",
    "descript", "decagon-ai", "rilla-ai", "moveworks",
    "sweepai", "marblism", "perplexity-ai", "pinecone-io",
    "temporal-io", "prisma", "supabase-io",
    # Ops + incident
    "rootly", "incident", "firehydrant", "runreveal",
    "dailydot", "statuspage-io", "pagerduty-labs",
    # Legal
    "ironclad-app", "evisort-com", "lexion-io", "spotdraft-ai-co",
    # Marketing tech
    "apollo-io", "lemlist", "instantly", "smartlead",
    "hyperline", "attentive-mobile",
    # Finance back-office
    "mosaic-fp", "finaloop", "puzzle-io",
]

# ──────────────── Lever (older mid-sized + Accel-era) ────────────────
LEVER = [
    "netflix", "spotify", "pinterest", "palantir", "yelp", "segment",
    "etsy", "shopify", "quora", "affirm", "scribd", "eventbrite",
    "expensify", "khanacademy", "crunchbase", "blendlabs", "atlassian",
    "box", "flatiron", "harvest", "homeadvisor", "lattice", "mixpanel",
    "olx", "pento", "podium", "ripple", "strava",
    "teachable", "toptal", "webflow", "workrise", "wonderschool",
    "benchling", "gocardless", "getcrunchbase", "alchemy",
    "algolia", "appsmith", "auctane", "avantstay",
    "biconomy", "blend", "bolt", "bombas", "brainly", "built",
    "caremessage", "casper", "chainlink-labs", "clever", "clio",
    "cloudreach", "codesignal", "collibra", "copperco", "credit-karma",
    "crossover", "demandbase", "disco", "dynatrace-inc",
    "egnyte", "ethos", "evergreen", "evernote", "eversana",
    "faire", "fast", "feedvisor", "feedzai", "flexe", "flywheel",
    "formlabs", "foxy-ai", "fireblocks-inc", "foursquare",
    "gainsight", "getyourguide", "glossgenius", "gorgias",
    "hustle", "iterable-inc", "jam-city", "kapitus", "keywords-studios",
    "krisp-ai", "lending-club", "levellee", "livongo", "looker-inc",
    "magicleap", "mailgun", "masterbranch", "mparticle", "neuralink",
    "oneplus", "overstock", "peloton", "petco", "pharmapacks",
    "policygenius", "prophet", "quizlet", "quidsi", "ramphealth",
    "redcarpet", "restream", "roche", "shipstation", "signal-ai",
    "sprout-social", "stacks-io", "streamlabs", "talko", "thumbtack-inc",
    "tiger-global", "tuft-needle", "unacademy", "velocity-global",
    "vivid-seats", "warby-parker-inc", "wealthfront", "wish", "zocdoc",
]
