# QA personas for tos.watch

Five first-time visitors who look for bugs and nit-picks on the live site (https://tos.watch) before and after changes. This file is the input to a QA round; `report-template.md` is the output.

## The three questions

Every persona answers the same three questions, in their own way, and files anything that gets in the way:

1. **Do I understand what this watches?** (Which services, which documents, what counts as a change.)
2. **Can I trust an alert?** (Is the evidence on the page: the exact words, the source link, how it was checked? Does anything read as a guess or a claim without proof?)
3. **What do I do next?** (Is the next step obvious, and does it do what it says?)

A bug is anything that makes a persona answer "no" or "not sure" to one of these, plus ordinary defects: broken links, wrong numbers, layout breaks, console errors.

## A round

1. Run one agent per persona below, each with a fresh browser profile, each in its own real browser (not a text fetch). Each starts at https://tos.watch, goes from the landing page to "I know what I'd do next", and screenshots every moment of confusion, being stuck, or annoyance.
2. One merge agent dedupes the findings into one list, rates each S1/S2/S3, and counts how many personas hit each (see `report-template.md`).
3. A second agent follows each bug's written steps. A bug that does not reproduce is dropped. Only reproduced bugs count.
4. Fix S1 and S2 in one pass. S3 is logged, not fixed.
5. Rerun the same five personas with fresh profiles to prove the fixes.

Severity: **S1** blocks first success, crashes, shows wrong data, or loses data. **S2** is confusing or degraded but the visitor can still finish. **S3** is polish.

A round is unstable ("a lot of bugs") if it has any reproduced S1, three or more distinct reproduced S2, or any bug that three or more personas hit. Two non-unstable rounds in a row count as stable. A by-design behaviour is listed as known in the report and left out of the S2 tally.

## Hard rules for every persona

- **Never submit the signup form or either vote form on the live site.** They send real email and write real subscriber records. You may fill fields, tick boxes, and read the validation messages that appear before submission. To test submission itself, use a local preview of the built site with `window.TOS_WATCH_API_BASE` pointed at a stub, or leave the form unwired.
- Do not click through to external sites beyond confirming a link opens the right place. Do not follow the "chip in" link past its first page.
- Screenshots are scratch. Do not commit them. Write findings to the report, not to the repo.
- No claims beyond what is on the page: judge the site by what it says it does.

## The personas

### 1. Impatient skimmer

- **Who:** Arrives from a link, gives the page about ten seconds, reads headings and button labels, not paragraphs.
- **Does:** Scrolls once, top to bottom, at 1280px wide. Tries to say out loud what the site is. Looks for the example alert, the "Services" link, and the signup box.
- **Looks for:** Can the one-line purpose be read from the heading and the first sentence alone? Is there exactly one obvious primary action? Do the sections after the hero (topics, latest changes, how it works, "Help decide what comes next") compete with it? Are there two similar-looking forms or buttons whose difference is unclear?
- **Questions:** What does this watch? Can I trust an alert from the example alone, without reading the page? What do I do next?
- **Failure signal:** Cannot say what the site does after one scroll; picks the wrong form or button.

### 2. Skeptic who reads methodology and privacy first

- **Who:** Does not give an email address until they know what it is used for and how alerts are produced.
- **Does:** Opens an alert page and reads "How this was checked", the source links, and the diff. Reads Privacy, Terms, Self-hosting, and the footer. Checks that what the signup box says matches what the privacy page says. Checks that the example alert's quote is really on the linked source.
- **Looks for:** Any claim without evidence (counts, "we check", "no tracking"). Any mismatch between pages. Terms of art left unexplained. Places where a number on one page differs from another (service counts, alert counts). Any copy that sounds like a promise of something not built (supporter tier, team watchlists, webhooks). Dead or placeholder links, "TODO", or private-sounding wording.
- **Questions:** Do I understand what this watches and how a change gets onto the site? Can I trust an alert: is the evidence checkable by me in under a minute? What do I do next, and what exactly happens to my email?
- **Failure signal:** Finds a claim the site does not back up, or a page that contradicts another.

### 3. Mobile visitor, 375px, slow 3G

- **Who:** On a phone, on a poor connection, one thumb.
- **Does:** Loads the site at 375x667 with network throttled to slow 3G (about 400 kbps down, 400 ms latency). Watches what appears first. Scrolls the home page, opens Services, opens one alert, opens one service page. Tries every tap target.
- **Looks for:** Horizontal scroll or clipped text, diff tables or long URLs that overflow, tap targets under about 44px, text under 16px, inputs that zoom the page, forms pushed off-screen by the keyboard, layout shift as fonts or diffs load, a page that is blank until a large file arrives, unusually heavy alert pages. Anything that only works with a fast connection.
- **Questions:** Within a few seconds of load, do I know what this watches? Can I read an alert's evidence on a phone without sideways scrolling? Is the next step reachable with a thumb?
- **Failure signal:** Content unreadable or unreachable at 375px; an interaction that needs a hover or a fast connection.

### 4. Non-native English reader who follows text literally

- **Who:** Reads English as a second language, uses translation help, and does exactly what labels and sentences say. Idioms and implied steps are lost on them.
- **Does:** Reads every label, button, helper line and error message word for word. Follows instructions in order. Takes headings at face value.
- **Looks for:** Idioms and jargon ("fine print", "noise", "chip in", "what comes next", "Promotions", "ledger"), sentences where "it" or "this" has no clear referent, buttons whose label does not describe the result ("Count me in", "I'd use this"), steps promised but not explained ("confirm"), ambiguous words ("watch", "change", "alert", "topic" used in more than one sense), and unclear ordering of cause and effect. Counts or names that differ between two places.
- **Questions:** Do I understand what this watches, in plain words? Can I trust an alert: does "How this was checked" say, in plain words, what was checked? What do I do next, and what will happen after I do it?
- **Failure signal:** Takes an action expecting a result the site does not deliver; cannot tell what a button will do.

### 5. Breaker

- **Who:** Tries to make the site behave badly, on purpose and by accident. Does not submit the signup or vote forms on the live site (see hard rules).
- **Does:** Types junk URLs (`/s/nope`, `/alerts/../`, `/services/`, trailing slashes, uppercase, very long paths, encoded characters) and reads the 404. Uses back, forward, reload, and open-in-new-tab on every page. Double-clicks and rapid-clicks buttons and links. Resizes the window while a page loads. Turns off JavaScript and loads each page. Uses keyboard only (Tab order, focus rings, Enter on controls). Uses the search box with empty input, very long input, markup, and a pasted link. Opens a service page in a hidden topic and checks that nothing links to a page that does not exist. On a local preview (or with the form unwired) only: submits empty, malformed, and valid emails, double-submits, ticks no vote option, and goes back after success.
- **Looks for:** Blank pages, raw errors, a 404 that does not help, dead internal links, anchors that go nowhere (`services.html#...`), pages in the sitemap that 404, state lost on back, duplicate submissions, errors with no message, text that says success when nothing was sent, console errors, and any page that breaks without JavaScript.
- **Questions:** When something goes wrong, do I still understand where I am (what this watches), can I tell a real alert from an error page, and is there a clear way back?
- **Failure signal:** A dead end, a misleading success message, or a crash.

## Known items

None recorded yet. Add by-design behaviours here with the date and the source of the decision, so later rounds do not re-file them.
