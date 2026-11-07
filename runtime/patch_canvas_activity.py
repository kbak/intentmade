"""Extend pinned Canvas grouping to ACP without changing persisted events.

Canvas 1.37.0 groups native and ACP activity. Retain the factory's attention
boundaries, compact labels and complete expandable details.
Patch only its shipped presentation bundle; refuse upstream drift at build time.
The source counterparts are group-events.ts and get-acp-tool-call-content.ts
at OpenHands/OpenHands v1.37.0.
"""

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

GROUP_BUNDLE = "use-shared-conversation-CYDmc-N3.js"
CONTENT_BUNDLE = "event-group-D497VdGK.js"

# [impl->req~im-canvas-activity~1]
# Keep pending/unknown states and errors visible. Guardian in-progress events
# are approval work, so only their successful terminal events may be folded.
HELPERS = r"""
function factoryIsACP(event) { return event?.kind === "ACPToolCallEvent"; }
function factoryRoutineACP(event) {
  if (!factoryIsACP(event) || event.is_error) return false;
  if (event.status === "completed") return true;
  return event.status === "in_progress"
    && !event.tool_call_id?.startsWith("guardian_assessment:");
}
function factoryCompactTitle(title) {
  if (typeof title !== "string") return title;
  const text = title.replace(/\s+/g, " ").trim();
  return text.length > 80 ? text.slice(0, 79) + "…" : text;
}
function factoryActivityCounts(events, allEvents = events) {
  const resolved = new Set(allEvents.filter(event =>
    event.kind === "AgentErrorEvent").map(event => event.tool_call_id));
  const observed = new Set(allEvents.filter(event =>
    event.kind === "ObservationEvent" || event.kind === "UserRejectObservation"
  ).map(event => event.action_id));
  const running = events.filter(event => factoryIsACP(event)
    ? event.status === "in_progress" || event.status === "pending"
    : event.kind === "ActionEvent" && !resolved.has(event.tool_call_id)
      && !observed.has(event.id)).length;
  const completed = events.filter(event => factoryIsACP(event)
    ? event.status === "completed"
    : event.kind === "ObservationEvent" || event.kind === "UserRejectObservation"
      || (event.kind === "HookExecutionEvent" && event.success)).length;
  return {total: events.length, completed, running};
}
"""

BUNDLES = {
    GROUP_BUNDLE: (
        "b60c8e490ad7472210ebf887f807f581d20a85701106d384e0b7ee23afb30bf3",
        (
            ("R(e)?e.status!==`failed`", "R(e)?factoryRoutineACP(e)"),
            (
                "l.session||l.reasoning?.length||l.events.length>=e",
                "l.session||l.reasoning?.length||l.events.length>=e||l.events.some(factoryIsACP)",
            ),
        ),
    ),
    CONTENT_BUNDLE: (
        "631046399f94fa5b1a904f2770a68f444e165a4b78c871cb99d5d7eec06dde0c",
        (
            # Native summaries no longer show completion counts. Keep those
            # beside the native title, including while a call is in progress.
            (
                "let E=ye(T?.timestamp,p?.language)",
                "const factoryCounts=factoryActivityCounts(e,S);"
                "if(factoryCounts.total)w=(0,O.jsxs)(O.Fragment,{children:[w,"
                "` (${factoryCounts.completed}/${factoryCounts.total})`]});"
                "let E=ye(T?.timestamp,p?.language)",
            ),
            (
                "ft=e=>{let t=e.title,n=e.tool_kind;",
                "ft=e=>{let t=factoryCompactTitle(e.title),n=e.tool_kind;",
            ),
            # Preserve the original title using upstream's safe fenced formatter.
            (
                "l=s.filter(e=>e.type===`text`),u=[];if(t===`execute`",
                "l=s.filter(e=>e.type===`text`),u=[];"
                "if(factoryCompactTitle(e.title)!==e.title)u.push(V(e.title));"
                "if(t===`execute`",
            ),
            # Expanded details must retain complete text and diff output.
            ("H=e=>e.length>1e3?`${e.slice(0,L)}...`:e", "H=e=>e"),
        ),
    ),
}


def patch_bundle(source, bundle=GROUP_BUNDLE):
    digest, replacements = BUNDLES[bundle]
    if hashlib.sha256(source.encode()).hexdigest() != digest:
        raise RuntimeError("Pinned Canvas activity bundle changed; review the upstream update")
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError("Pinned Canvas activity anchor changed; review the upstream update")
        source = source.replace(old, new, 1)
    return HELPERS + source


def install(frontend, node):
    assets = frontend / "assets"
    patched = {name: patch_bundle((assets / name).read_text(), name) for name in BUNDLES}
    for source in patched.values():
        subprocess.run(
            [node, "--input-type=module", "--check"], input=source, text=True, check=True
        )
    # Vite filenames are immutable cache keys. Reversion the JS import graph
    # together, including the HTML entrypoints, so an existing browser loads the
    # changed component on the next deployment without a manual cache clear.
    version = hashlib.sha256("".join(patched.values()).encode()).hexdigest()[:12]
    names = {p.name: f"{p.stem}-factory-{version}.js" for p in assets.glob("*.js")}
    pattern = re.compile("|".join(re.escape(name) for name in names))
    updates = {}
    for source in [*assets.glob("*.js"), *frontend.rglob("*.html")]:
        text = patched.get(source.name, source.read_text())
        target = source.with_name(names[source.name]) if source.suffix == ".js" else source
        updates[target] = pattern.sub(lambda match: names[match[0]], text)
    for target, text in updates.items():
        target.write_text(text)
    for name in names:
        (assets / name).unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", type=Path, default=Path("/opt/agent-canvas/frontend"))
    parser.add_argument("--node", default="/acp-node/bin/node")
    args = parser.parse_args()
    install(args.frontend, args.node)


if __name__ == "__main__":
    main()
