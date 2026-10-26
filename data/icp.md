# ICP: AI FDE deploy program (DRAFT, written by our team)

> Status: our own draft. The organisers' official ICP paragraph has been requested.
> If it arrives, replace this file and update data/criteria.json. Do not mix the two.
> Basis: Build Fast with AI's public DEPLOY page (deploy.buildfastwithai.com), read 26 Oct 2026.
> Anything marked [assumption] is our guess, not stated on that page.

## The offer
DEPLOY places forward-deployed AI engineers inside a client's team to take ONE workflow
from proof of concept to production in about six weeks. The client keeps the code, evals
and documentation.

## Who it is for
A company that has a costly, repetitive workflow it wants to automate with AI, believes
AI can help, but has no mature in-house AI team to do it, and is stuck in pilots or
unsure where to start.

## Criteria

Must-haves (fail any one -> not_fit):
1. industry: works in one of the industries DEPLOY has served: energy, consulting,
   venture capital, finance, HR tech, education, or enterprise software.
2. size: 50 to 5,000 employees, a strict range with no margin: big enough to have a dev
   team and a workflow owner, small enough to need outside AI help. [assumption]
   Evidence: prefer the company's own statement of headcount (about page, careers page,
   press release). A third-party workforce estimate (e.g. Revelio Labs) counts only when
   the company states nothing itself. If sources disagree, the company's own figure wins.
3. has_tech_team: has an engineering or IT team (e.g. engineering job posts, a tech blog,
   a product they build).

Signals (each one that is met makes a stronger fit):
4. ai_intent: the company is building or adopting AI in any form: open AI/ML/automation
   roles, an announced AI pilot, AI mentioned as a company priority, or AI features in its
   own product (e.g. "integrations with OpenAI").
5. common_stack: uses tools DEPLOY works with (Salesforce, HubSpot, Slack, Microsoft 365,
   Google Workspace, Power BI, AWS, Azure, Google Cloud), seen in job posts or case studies.
6. ops_heavy: has high-volume operations: a dedicated support, claims or onboarding team,
   or a stated volume (e.g. tickets or claims per month).

Exclusions (any one -> not_fit):
7. is_ai_vendor: AI services or AI agents are the main product the company sells (they
   would build it themselves, or they compete with DEPLOY). A company selling an
   "AI-powered" product in another category is not excluded. [assumption]
8. too_large_ai_team: a big tech company with large in-house AI research teams. [assumption]

## Scoring rule (plain English)
- Any failed must-have or any triggered exclusion -> not_fit.
- A must-have or exclusion still "unknown" after research -> not_enough_evidence,
  but only if that criterion has unknown_blocks: true in criteria.json.
  Size has unknown_blocks: false: a size shown to be out of range still fails, but an
  unknown size alone does not block a fit (company sites rarely state headcount). [assumption]
  too_large_ai_team has unknown_blocks: false: the exclusion applies only when proven,
  because "no large AI research team" can almost never be shown. [assumption]
- All must-haves met (or unknown and non-blocking) and at least 2 signals met -> fit.
- Otherwise -> not_fit.

## Open questions for the organisers
- Is there an official ICP paragraph and a labelled list of 20 companies for Task C?
- Geography: India only, or global?
- Company size range, and whether funding stage matters.
- Are large enterprises (their logo bar includes some) in scope, or only mid-size companies?