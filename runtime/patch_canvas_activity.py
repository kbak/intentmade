"""Extend pinned Canvas grouping to ACP without changing persisted events.

Canvas 1.35.0 groups native and ACP activity. Retain the factory's attention
boundaries, compact labels and complete expandable details.
Patch only its shipped presentation bundle; refuse upstream drift at build time.
The source counterparts are group-events.ts and get-acp-tool-call-content.ts
at OpenHands/OpenHands v1.35.0.
"""

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

GROUP_BUNDLE = "use-shared-conversation-teFPR8Jf.js"
CONTENT_BUNDLE = "event-group-BwEbL1FU.js"

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
        "81626c71c14f219f279ce05bae72c4c3917ff9f3205fb26ec25b95cb17190307",
        (
            ("z(e)?e.status!==`failed`", "z(e)?factoryRoutineACP(e)"),
            (
                "s.session||s.reasoning?.length||s.events.length>=e",
                "s.session||s.reasoning?.length||s.events.length>=e||s.events.some(factoryIsACP)",
            ),
        ),
    ),
    CONTENT_BUNDLE: (
        "6c2ac1295ddc1a09ed852c17cba6a1305830ec3814e0ee1ed2fa3d87e8daafa0",
        (
            # Native summaries no longer show completion counts. Keep those
            # beside the native title, including while a call is in progress.
            (
                "let T=_e(C?.timestamp,f?.language)",
                "const factoryCounts=factoryActivityCounts(e,v);"
                "if(factoryCounts.total)S=(0,k.jsxs)(k.Fragment,{children:[S,"
                "` (${factoryCounts.completed}/${factoryCounts.total})`]});"
                "let T=_e(C?.timestamp,f?.language)",
            ),
            (
                "at=e=>{let t=e.title,n=e.tool_kind;",
                "at=e=>{let t=factoryCompactTitle(e.title),n=e.tool_kind;",
            ),
            # Preserve the original title using upstream's safe fenced formatter.
            (
                "l=o.filter(e=>e.type===`text`),u=[];if(t===`execute`",
                "l=o.filter(e=>e.type===`text`),u=[];"
                "if(factoryCompactTitle(e.title)!==e.title)u.push(H(e.title));"
                "if(t===`execute`",
            ),
            # Expanded details must retain complete text and diff output.
            ("U=e=>e.length>1e3?`${e.slice(0,R)}...`:e", "U=e=>e"),
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
