# QA round report: <date>, round <n>

Copy this file to `docs/qa/<date>-round-<n>.md` and fill it in. One file per round. Screenshots stay in scratch and are not committed; describe what they show.

- **Site and build:** <host, deployment id or commit, date and time of the run>
- **Personas run:** <which of the five, with browser and viewport for each>
- **Run by:** <agent or person>
- **Merge pass by:** <agent or person>
- **Reproduction pass by:** <agent or person, separate from the merge pass>
- **Live-site rule kept:** <yes/no: no signup or vote form was submitted on the live host>

## Verdict

<Stable / Unstable ("a lot of bugs").> Unstable means any reproduced S1, three or more distinct reproduced S2, or any bug hit by three or more personas. Counts: S1 <n>, S2 <n>, S3 <n>, bugs hit by three or more personas: <n>.

## The three questions

One row per persona: did they understand what this watches, could they trust an alert, did they know what to do next. Yes, no or unsure, with the bug id that explains any no.

| Persona | What does this watch? | Can I trust an alert? | What do I do next? |
|---|---|---|---|
| Impatient skimmer | | | |
| Skeptic | | | |
| Mobile, 375px, slow 3G | | | |
| Non-native literal reader | | | |
| Breaker | | | |

## Bugs

Severity: **S1** blocks first success, crash, wrong data, data loss. **S2** confusing or degraded, but the visitor can finish. **S3** polish. Sort by severity, then by persona hit count.

| Id | Severity | Personas hit (n of 5) | Which personas | Page or URL | Reproduced by | Status |
|---|---|---|---|---|---|---|
| B1 | S2 | 2 | Skimmer, Literal reader | /services | <agent or person> | open |

For each bug, below the table:

### B1: <short title>

- **Where:** <URL, viewport, browser>
- **Steps:** <numbered, so someone else can follow them cold>
- **Expected:** <what should happen>
- **Actual:** <what happened, including exact text or console message>
- **Screenshot:** <scratch filename and what it shows>
- **Question it blocks:** <what does this watch / can I trust an alert / what do I do next>
- **Reproduced by:** <who followed the steps, and the result: reproduced, or dropped as noise and why>
- **Suggested fix:** <optional, one line>

Reproduced-by is required. A bug with no second reproduction is listed under "Not reproduced" and does not count towards the verdict.

## Not reproduced

Bugs a persona reported that the second pass could not reproduce, with one line each on why.

## Known items

By-design behaviours or items owned elsewhere (legal copy, for example). Listed here and left out of the S2 tally. Each with the date and source of the decision.

## Fixes and rerun

- **Fixed in this round:** <bug ids, PR links>
- **Deferred (S3, or known):** <bug ids>
- **Rerun with fresh profiles:** <date, verdict, counts>
