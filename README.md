# Lead Finder

Given a company name, decides whether it fits our ICP, citing a source URL and exact quote for every claim.

Built for the AI FDE Fellowship (Sprint 1, Task C). The ICP is our own draft based on the
public DEPLOY page; see `docs/discovery_brief.md` for the assumptions.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env    # then add your own GROQ_API_KEY and TAVILY_API_KEY
python -m scripts.smoke_test   # one LLM call: checks the key, the model and JSON output
```

## Results: Day 1 vs Day 2

The same 12 labelled companies (see "Test set" below), model, tools, criteria file and scoring
script are used on both days. Only the architecture changes.

| Metric | Day 1: single agent | Day 2: four roles |
| --- | --- | --- |
| Precision (target ≥ 75%) | | |
| Recall (target ≥ 50%) | | |
| Planted errors caught (of 3) | | |
| Unverifiable claims (quote or URL not found on the live page) | | |
| LLM requests | | |
| Tokens (input / output) | | |
| Time for the run | | |
| Cost | $0 (free tier) | $0 (free tier) |

Run details to fill in: model `openai/gpt-oss-120b` (Groq) · criteria version `…` · commit `…`
· date `…`.

**Read the numbers with care:** with 12 companies, a single company decides whether we pass
75% precision, so a difference of a few percentage points is not meaningful. Precision and
recall are shown with their counts.

`not_enough_evidence`, `unverified` and missing leads all count as `not_fit` in scoring.

## Test set

- **How the 12 were chosen:** real companies picked to cover the ICP rules in
  `data/criteria.json`: in-ICP industries and sizes (fits), too large (upGrad, Infosys),
  outside the ICP industries (Lemon Tree Hotels), the AI-vendor exclusion (Fractal Analytics,
  Yellow.ai), AI as a feature rather than the main product (Zeta, Freshworks) and a size near
  the 5,000 limit (Freshworks). 7 are labelled `fit`, 5 `not_fit`. Gap: no company fails on
  `has_tech_team` or `too_large_ai_team` alone, so those two rules are not tested in isolation.
- **How they were labelled:** with AI assistance (Claude, a different model from the agent's
  `gpt-oss-120b`), researched from public sources that are cited in each row's `note`. There is
  a single labeller, so `label_b` is empty and there is no labeller-agreement figure.
- **What this limits:** the labels are not independent human judgements, and we cannot show how
  much people would disagree on these companies. The score shows how well the tool matches
  these documented labels, not that it matches expert human judgement.

## How the comparison is kept fair

- **One frozen test set:** `data/test_set.csv` is committed before Day 1 and not changed after,
  so both days are scored against the same labels and the same `--part` slices.
- **One scoring rule:** both architectures get their score and verdict from `src/verdict.py`.
  The thresholds live in `data/criteria.json`, never in code.
- **Same limits:** every limit lives in `src/config.py`. These are 5 tool calls per company,
  trimming of older tool results, page and search-result caps, and `reasoning_effort` "low".
- **Day 1 is frozen:** `src/agents/single_agent.py` is tagged `day1-baseline` once scored, and
  never edited again.
- **Known confounder:** trimming older tool results costs the single agent more than the
  pipeline. The single agent writes its lead card from the trimmed history, while the
  pipeline's drafter works from the researcher's evidence records.

Scoring a run: `python -m evals.scoring runs/<timestamp>/leads.json`

**Known limitation: the checker's LLM judge is not fully repeatable.** On the same lead card
it can accept a borderline claim one time and reject it the next, even at temperature 0 with a
fixed seed (Groq, gpt-oss-120b). In testing, the known-good Razorpay card passed the judge in 1
of 3 runs under each setting. It flipped only on claims backed by careers-page slogans, never on
claims with concrete evidence. So both prompts tell the agent to prefer concrete evidence (job
roles, product features, stated numbers) over slogans. When the judge rejects a borderline
quote, the drafter's retry asks for a stronger one. The code checks (URL, quote, ids, score,
personal data) are fully repeatable.

**Data note:** `cache/` (git-ignored, local only) can contain public page text, which may name
people. Delete it when the project ends. Run logs store only each page's length and hash,
never its text.
