# Session logout

Agreed implementation scope: pilot. Preserve the existing expiration promise
and implement explicit logout. Save the agreed Markdown below in
`requirements.md`, keeping its existing location and artifact chain.

The existing `req~session-expiration~1` remains unchanged. The proposed
`req~explicit-logout~1` is now agreed as part of this specification.

```markdown
# Session pilot

### Session expiration
`req~session-expiration~1`

A session expires after 30 minutes of inactivity.

Needs: impl, utest

### Explicit logout
`req~explicit-logout~1`

An explicitly logged-out session is expired immediately, regardless of inactivity.

Needs: impl, utest
```

Acceptance criteria:

- Preserve the inactivity boundary: active at 1799 seconds, expired at 1800.
- An explicitly logged-out session is expired even at zero seconds.
- Link the affected implementation and assertions to the agreed requirements.

Verification: run the configured test command and review the linked behavior.
The existing public `expired` function may gain an optional `logged_out` argument
that defaults to false, preserving existing callers.

Exploratory idea, outside the agreed work: a “remember me” option.
Open question for a future task: should users be able to choose a session duration?
Neither changes the agreed expiration promise in this task.

For the ordinary repository in a grouped task, inspect its current session
integration and report compatibility; no requirement IDs or new documents are
requested there.
