"""All settings and limits in one place: model, base URL, call caps, retries, delays, file paths."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env once here, so every other module just imports config
load_dotenv()

# --- LLM provider (from .env, so switching provider never touches code) ---
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL: str = os.getenv("LLM_MODEL", "")
LLM_API_KEY: str = os.getenv("GROQ_API_KEY", "")
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

# --- Agent limits (identical for Day 1 and Day 2: part of the fair comparison) ---
MAX_TOOL_CALLS: int = 5   # per company; cut from 8 to fit 200K tokens/day per key
MAX_RETRIES: int = 3      # on HTTP 429, and checker rejections before "unverified"

# --- History trimming (applied inside llm.py, so both architectures get it identically) ---
# The newest tool result stays in full; older ones are cut, because a tool loop resends
# the whole history on every turn and old pages would otherwise be paid for again and again.
TRIMMED_TOOL_RESULT_CHARS: int = 800

# --- Free-tier rate limits (Groq free plan for gpt-oss-120b, read from response headers) ---
TPM_LIMIT: int = 8_000              # tokens per minute: our real bottleneck
SECONDS_BETWEEN_CALLS: float = 2.0  # 30 requests/minute allowed -> at most one every 2 s
TOKEN_SAFETY_MARGIN: float = 0.9    # our token estimate is rough, so stay 10% under the limit
MAX_TOKENS_PER_REQUEST: int = int(TPM_LIMIT * TOKEN_SAFETY_MARGIN)  # llm.py refuses anything bigger
CHARS_PER_TOKEN: int = 4            # rule of thumb for English text; used only for the estimate
MAX_RETRY_WAIT_SECONDS: float = 90.0  # a longer retry-after means the DAILY limit is used up: stop, don't sleep for hours
LLM_TIMEOUT_SECONDS: float = 60.0     # so one hung request can't freeze the run

# --- Output size ---
MAX_OUTPUT_TOKENS: int = 1_200  # caps reasoning + answer; also reserved in the size check
REASONING_EFFORT: str = "low"   # gpt-oss "thinking" tokens count against TPM; low is enough for most calls

# --- Tool output caps ---
# Largest request (6th of a 5-tool loop): ~1,500 base + 4 trimmed results x ~260 tokens
# + 1 full result ~560 + 1,200 output ~= 4,300 tokens, well under MAX_TOKENS_PER_REQUEST
MAX_PAGE_CHARS: int = 2_000     # fetch_page cuts extracted text to this length (~500 tokens)
MAX_SEARCH_RESULTS: int = 5     # web_search returns at most this many results
MAX_SNIPPET_CHARS: int = 300    # each search snippet is cut to this length
FETCH_TIMEOUT_SECONDS: float = 15.0  # so one slow site can't freeze the run
MAX_JOB_ROLES: int = 15         # find_jobs returns at most this many roles (plus the total count)
MAX_JOB_BOARD_GUESSES: int = 4  # board names tried per company, each on Greenhouse and Lever
# Browser-style so ordinary sites serve us, but honest about who we are
FETCH_USER_AGENT: str = "Mozilla/5.0 (compatible; LeadFinder/0.1; fellowship project)"
MIN_PAGE_TEXT_CHARS: int = 200  # less readable text than this: a JavaScript shell or block page

# Ground rule: no personal data, no review sites. Search hides these and fetch refuses them.
# An entry matches the domain and its subdomains; "domain/path" also needs that path prefix.
BLOCKED_DOMAINS: list[str] = [
    "linkedin.com", "rocketreach.co", "zoominfo.com", "apollo.io", "lusha.com", "signalhire.com",
    "glassdoor.com", "ambitionbox.com", "indeed.com/cmp",
]

# --- File paths (absolute, so scripts work from any folder) ---
ROOT_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = ROOT_DIR / "data"
ICP_PATH: Path = DATA_DIR / "icp.md"
CRITERIA_PATH: Path = DATA_DIR / "criteria.json"
TEST_SET_PATH: Path = DATA_DIR / "test_set.csv"
RUNS_DIR: Path = ROOT_DIR / "runs"
CACHE_DIR: Path = ROOT_DIR / "cache"
