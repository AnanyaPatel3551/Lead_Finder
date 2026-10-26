# Discovery brief: Lead Finder (AI FDE Fellowship, Sprint 1, Task C)

Written 26 Oct 2026 from the builder's answers. Items marked **[assumption]** are our own
guesses and need confirming with the organisers. Nothing here is a customer fact we were told.

## About our ICP

The ICP is **our own draft**, based on Build Fast with AI's public DEPLOY page
(deploy.buildfastwithai.com, read 26 Oct 2026). We have asked the organisers for the official
ICP. The tool reads its criteria from `data/criteria.json` when it runs, so the real ICP can
replace the draft without any code changes.

Assumptions in the draft (from `data/icp.md`):
- **Size:** about 50 to 5,000 employees. **[assumption]**
- **Exclusion `is_ai_vendor`:** AI consultancies, AI agencies and AI-native product companies
  are excluded, because they would build it themselves or compete with DEPLOY. **[assumption]**
- **Exclusion `too_large_ai_team`:** big tech companies with large in-house AI research teams
  are excluded. **[assumption]**
- **Scoring threshold:** "fit" needs at least 2 of the 3 signals. This is our own rule; the
  DEPLOY page does not state one.
- **An unknown size does not block a fit.** Each criterion in `criteria.json` has
  `unknown_blocks`. For `size` it is false: a size shown to be out of range still fails, but
  an unknown size alone does not lead to `not_enough_evidence`. Every other must-have and
  both exclusions still block when unknown. We chose this because company sites rarely state
  headcount (see Risks). **[assumption]**
- **`is_ai_vendor`** covers only companies whose main product is AI services or AI agents.
  An "AI-powered" product company is not excluded. **[assumption]**
- **Size is a strict 50 to 5,000 range, with no margin**, so the rule is the same for the
  agent and the labellers. The company's own headcount statement (about page, careers page,
  press release) is preferred. A third-party workforce estimate (e.g. Revelio Labs) counts
  only when the company states nothing itself. These are company-level totals, not personal
  data. If sources disagree, the company's own figure wins. **[assumption]**
- **`too_large_ai_team` blocks only when proven.** Its `unknown_blocks` is false, because
  "no large AI research team" can almost never be shown, and an unknown would otherwise turn
  most fits into `not_enough_evidence`. **[assumption]**
- **`ai_intent` counts building or adopting AI in any form**: open AI/ML roles, an announced
  AI pilot, or AI features in the company's own product. **[assumption]**

Open questions for the organisers: Is there an official ICP and a labelled list of companies?
Is the target geography India only, or global? What is the intended size range, and does
funding stage matter? Are large enterprises in scope?

## 1. User and workflow

**User:** someone at Build Fast with AI who does sales or partnerships for the DEPLOY program.
They look for companies that might want a forward-deployed AI engineer for one workflow.
The fellowship judges evaluate the tool, but they are not its user.

**Current workflow [assumption, to confirm]:** companies are found and qualified by hand.
That means Googling, reading the company website and about page, checking careers pages for AI
or automation roles, looking at company pages on LinkedIn, and tracking everything in a
spreadsheet.

**Pain [assumption, to confirm]:**
- It is slow: maybe 15 to 30 minutes per company.
- Judgement differs from person to person.
- Claims such as "they're hiring AI engineers" often have no link, so nobody can check them
  later.

**What happens next:** a person reviews each fit lead and its evidence, then decides whether to
reach out manually. **Nothing is ever sent automatically.**

## 2. Good and bad examples (company types, not real companies)

These are kept separate from the 12 labelled test companies, so they are not used for scoring.

| Should be | Company type | Why |
| --- | --- | --- |
| fit | About 300-person Indian HR-tech SaaS company with an engineering team. Hiring its first ML/AI engineer. Large support or onboarding workflow. | Right industry and size, has a tech team, AI intent, ops-heavy |
| fit | About 1,000-person financial services firm (lending or insurance). Has announced an AI pilot. Runs on Salesforce or Microsoft 365. | Right industry and size, AI intent, common stack, document-heavy work |
| fit | About 150-person edtech company with an in-house dev team. Job posts mention automation. Large student-support operation. | Right industry and size, tech team, AI intent, ops-heavy |
| not_fit | 200-person AI consultancy that builds AI agents for clients | Triggers the `is_ai_vendor` exclusion |
| not_fit | 20-person fintech startup with three engineers | Fails the `size` must-have |
| not_fit | 50,000-person global bank with a large AI research group | Fails `size` **and** triggers `too_large_ai_team`. A good test of whether both reasons are reported |

All three not_fit types are near misses on purpose: each one almost fits.

## 3. Success metric

On our 12 human-labelled test companies:
- **Precision at least 75%:** of the companies the tool calls "fit", at least 3 in 4 really are.
- **Recall at least 50%:** the tool finds at least half of the companies that really are fit.
  Without this, a tool that hardly ever says "fit" could score high precision while missing
  most real fits.
- **All 3 planted errors caught by the checker.**
- **Every "yes" and "no" criterion is backed** by a source URL and a quote that the checker
  finds again on the live page.
- **$0 cost:** each scored run fits within the free-tier daily limits, split across our three
  Groq keys. We report requests and tokens for every run.
- **An honest Day 1 vs Day 2 comparison**, reported even if the four-role split does not help.

**Read the numbers with care:** with 12 companies, a single company decides whether we pass
75% precision. A difference of a few percentage points between Day 1 and Day 2 is not
meaningful. We report the counts behind every percentage.

## 4. Sources and risks

**Sources the tool may read:**
- Tavily web search, with ddgs (DuckDuckGo) as the free fallback
- Company websites: home, about and careers pages
- Public job boards (Greenhouse and Lever), through `find_jobs`
- Free, publicly readable news or company blog posts about AI pilots

**Ruled out:** LinkedIn; paywalled news; Glassdoor and other review sites; anything behind a
login; anything about individual people. Sources can be from any country, but whether the ICP
covers India only or the whole world is an open question for the organisers.

**Risks:**

| Risk | How we handle it |
| --- | --- |
| Outdated pages (old job posts, old headcount) | Note the page date when we can |
| Two companies with the same name | The agent must confirm the company's own domain |
| JavaScript-only sites come back empty | Scored as `unknown`, never as "not met" |
| The model invents quotes | The checker finds every quote again on the live page |
| Our draft ICP does not match what the organisers mean | Assumptions listed above; real ICP requested |
| Free-tier limits stop a scored run halfway | Runs split across three keys; if a run stops, re-run only the missing companies |
| Small test set | One company moves precision a lot; we report counts, not only percentages |
| Wrong or missing company size | Company sites rarely state headcount, so `size` is often `unknown`. To stop this alone causing missed fits, `size` has `unknown_blocks: false`. The cost: a company that is really too small or too large, but whose size we never find, can still come out as `fit`. Check this first among Day 1 disagreements |
| Labellers disagree | Our labels are a judgement call. Two people label each company independently, and we record where they disagreed |

## 5. Smallest version (what is in for today)

- Evaluation mode on our 12 labelled companies only
- Day 1 single agent, then the Day 2 four roles, with the same model and tools
- Three tools: `web_search`, `fetch_page`, `find_jobs`
- Checker: URL check, quote check, evidence-id check, score recalculation, and the LLM judgement
- 3 planted-error tests, a run log with usage, a run report, and the comparison table

**Out for now:** discovering new companies; Playwright for JavaScript pages; any UI; any
outreach; paid models or services.

**Later, if there is time:** automatic resume of a run that stopped halfway.
