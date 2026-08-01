# Asking the maintainers whether this is worth continuing

The work is already built. Validating costs about ten minutes, not more
building — so ask before doing the rehearsal, the tests, or the i18n pass.

## Where

**DataHub Slack — https://datahub.com/slack** — first choice. Fast, human, low
ceremony. Look for `#contribute`, `#feature-requests`, or `#general`.

GitHub Discussions are **disabled** on `datahub-project/datahub`, and there are
**1,056 open issues** with only bug-report templates — an issue is durable but
easily missed. Use it as a follow-up if Slack gets traction, not as the opener.

Don't post to both at once. Slack first; if someone bites, they'll tell you
where to put it permanently.

---

## The Slack message

> **Is "which agents break if I change this table" a question DataHub should
> answer?**
>
> Dashboards have lineage. Agents don't. Drop a column a dashboard needs and it
> errors — someone gets paged. Drop a column an agent's skill quietly depends on
> and it doesn't error; it returns a confidently wrong number.
>
> I built a thing that scans agent repos, emits agents/skills/MCP tools into
> DataHub with lineage back to the tables they read, then answers that question.
> The part I think is interesting: for a dropped column, lineage-only impact
> flags 7 downstream consumers, this flags 3 — the other 4 read the same table
> but never name that column in their skill text.
>
> It works end to end today against master, and the DataHub-side diff is 4 files
> (a tab on the dataset profile, registered through the existing
> `getProfileTabs()`, gated through the existing `display.visible`). No fork, no
> patched image.
>
> Before I put more time in: **is this a problem DataHub wants to own, and is
> this the right shape for it?** Genuinely fine with "no" — I'd rather hear it
> now.
>
> Write-up, source and runbook: https://github.com/AKSHAJ-SHELL/Datahub_submission

## Why it's written that way

- **Leads with the question, not the artifact.** People answer questions; they
  ignore announcements.
- **The 7-vs-3 number does the work.** It is the one thing that isn't obvious
  from the description and the one thing that says this isn't just lineage.
- **"4 files, no fork" pre-empts the first objection** — that this is invasive.
- **"Genuinely fine with no" invites an honest answer.** Without it, people give
  you polite encouragement, which is worse than a clear no because it costs you
  four more days.
- **One link, at the end.** Not three.

## The single highest-leverage addition

**A 30-second screen recording** of the Agents tab: pick "Drop a column",
choose `line_total`, show 2 break / 1 degrade / 4 unchanged, then the line that
reads *"Lineage-only impact analysis flags every consumer of this table: 7.
AgentLens says 3. 4 would have been chased for nothing."*

That sentence is the entire argument and it lands in seconds on video where it
takes paragraphs in text. If you only do one more thing before asking, do this.

---

## If you'd rather have something durable

Open **PR 2 as a draft** against `datahub-project/datahub` (see
`datahub-patch/PR-2-agents-tab.md`). It costs nothing extra — the code exists —
and maintainers can read actual code instead of a description. Its body already
carries the three open questions.

The draft PR and the Slack message work well together: post the message, link
the draft.

## What a "no" saves you

The remaining work if this goes forward is real: i18n keys, frontend tests on
682 lines, a usage guide, the gradle lint run, and answering the sidecar
data-delivery question — which may mean rewriting how the tab gets its data.
That is days, not hours. Worth ten minutes to find out.
