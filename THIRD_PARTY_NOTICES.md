# Third-party notices

IntentMade's original code is licensed under [MIT](LICENSE). Dependencies and
bundled upstream material retain their own licenses.

- `runtime/codex-seccomp.json` derives from Moby's default seccomp profile.
  See its [Apache-2.0 license](runtime/LICENSE.moby-profiles) and
  [source and modifications](runtime/codex-seccomp.md).
- The browser QA skill includes OpenHands material under its retained
  [MIT notice](workflows/skills/factory-browser-qa/LICENSE).
- The runtime builds on OpenHands and installs Codex ACP, Open Code Review,
  agency-agents, and optional audit guidance. Revisions, checksums, and source
  locations are recorded in [docker/runtime.Dockerfile](docker/runtime.Dockerfile)
  and [upstream.lock.json](upstream.lock.json). Their upstream notices remain
  applicable to those components.

The root license does not relicense dependency code, model services, or optional
enterprise components supplied by upstream projects.
