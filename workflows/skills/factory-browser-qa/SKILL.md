---
name: factory-browser-qa
description: Exercise changed UI behavior in a disposable application and return checks with retained screenshot evidence.
---

# Factory browser QA

Adapted from OpenHands extensions `qa-changes` at
`1bad294d4b9648b14ad335f516edf6f0a6622305` (MIT; see LICENSE).
The factory handles startup, tests, review, artifacts and publication.

Read the specification and diff against the supplied base. Identify the changed
screens and user journeys. Use the Playwright MCP browser to exercise the actual
running application at the supplied local URL, including relevant success,
empty, loading and error states. Check the changed interaction end to end and
capture legible PNG screenshots with captions identifying the observed state.
Use before/after evidence when available without modifying this checkout.

Do not edit source, commit, push, change test adapters, disable authentication,
or post to GitHub. Use only the disposable test data and accounts described by
the profile. Do not connect to production services. Save screenshots outside
the repository in the supplied output directory. Pass the absolute output path
to the screenshot tool's `filename` argument; return just the simple filename.
The parent exports them before deleting this sandbox.

Existing tests run separately. This stage verifies behavior in the browser;
passing tests, reading code, a login screen, or mocked API responses alone do
not prove the affected authenticated workflow works. Report any mocked data
explicitly and mark unverified live behavior BLOCKED. Do not invent evidence.

After three materially different attempts at one verification approach, switch
approaches. After two approaches fail, report what remains unverified as BLOCKED
with a specific prerequisite. A missing account, service, or usable browser is
BLOCKED, not an application defect. FAIL requires an observed functional defect
within the approved scope. PASS requires all relevant checks passed, at least
one actual screenshot, and a concrete description of each observed result.

Return the supplied structured schema. The summary should explain whether the
change achieves the requested behavior, with verification limits. Do not resolve
GitHub feedback threads or expand the approved task scope.
