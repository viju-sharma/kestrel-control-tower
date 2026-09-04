"""Paths and knobs. Everything here can be overridden with an env var so the
assessors can point the app at their copy of the database without editing code."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The client's operational database. We open it read-only and never write to it.
DB_PATH = Path(os.environ.get("KESTREL_DB", ROOT / "data" / "kestrel_ops.db"))

# Things we pull ourselves (freight invoices, competitor prices) live in a
# separate file so the source db stays untouched and the cache can be deleted.
CACHE_PATH = Path(os.environ.get("KESTREL_CACHE", ROOT / "data" / "cache.db"))

PARTNER_API_URL = os.environ.get("KESTREL_PARTNER_API", "http://localhost:8088")
PARTNER_API_KEY = os.environ.get("KESTREL_PARTNER_API_KEY", "kp_live_7f3a9c21")
BAZAAR_URL = os.environ.get("KESTREL_BAZAAR_URL", "http://localhost:8080")

# A delivery counts as on time if it is no later than this. The brief never
# says; 30 minutes is what we'd propose to the client as a starting point.
ON_TIME_TOLERANCE_MIN = int(os.environ.get("KESTREL_ONTIME_MIN", "30"))

# Kestrel's financial year starts in April.
FY_START_MONTH = 4

# Competitor listings older than this many days are ignored for "today's gap".
PRICE_FRESHNESS_DAYS = int(os.environ.get("KESTREL_PRICE_FRESHNESS_DAYS", "14"))

# Ask-anything. Two ways in:
#   ANTHROPIC_API_KEY                         -> Anthropic SDK, model KESTREL_LLM_MODEL
#   LLM_API_KEY + LLM_BASE_URL (+ LLM_MODEL)  -> any OpenAI-compatible chat endpoint
# GEMINI_API_KEY is a shortcut for the second form pointed at Google's endpoint,
# which has a free tier. Without any of these the tab offers prepared questions.
ANTHROPIC_MODEL = os.environ.get("KESTREL_LLM_MODEL", "claude-sonnet-5")
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or (
    "https://generativelanguage.googleapis.com/v1beta/openai" if os.environ.get("GEMINI_API_KEY") else None)
LLM_MODEL = os.environ.get("LLM_MODEL") or ("gemini-2.5-flash" if os.environ.get("GEMINI_API_KEY") else None)

# "In full" is normally exact. In this dataset every single order line is
# short by a little (see docs/DATA_NOTES.md), so exact in-full is zero everywhere
# and OTIF would be meaningless. We treat an order as in full when its case
# fill rate is at or above this percentage, and say so on screen.
IN_FULL_PCT = float(os.environ.get("KESTREL_IN_FULL_PCT", "90"))
