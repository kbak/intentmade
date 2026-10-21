"""Extend pinned Canvas grouping to ACP without changing persisted events.

Canvas 1.29.0 already groups native actions, but excludes ACP tool calls.
Patch only its shipped presentation bundle; refuse upstream drift at build time.
The source counterparts are group-events.ts, event-group.tsx and
get-acp-tool-call-content.ts at OpenHands/OpenHands 9057a57f4a7b5a74e93989090edaffc21e3554ff.
"""

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

BUNDLE = "messages-1nU0NArR.js"
ORIGINAL_SHA256 = "0e9d31cefbd13ebae7ba9d147ecf6fe6c331dceac3251cf5726635f04fef1a21"

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
"""

REPLACEMENTS = (
    (
        "Sa=(e,t)=>{if(M(e))",
        "Sa=(e,t)=>{if(factoryIsACP(e))return factoryRoutineACP(e);if(M(e))",
    ),
    (
        "a.events.length>=t?r.push({kind:`group`",
        "(a.events.length>=t||a.events.some(factoryIsACP))?r.push({kind:`group`",
    ),
    (
        "p=d.filter(e=>!f?.has(e.tool_call_id)),h=e.length",
        "p=[...d.filter(e=>!f?.has(e.tool_call_id)),"
        "...e.filter(e=>factoryIsACP(e)&&e.status===`in_progress`)],h=e.length",
    ),
    ("v&&(M(v)?y=Yi(v).title", "v&&((M(v)||factoryIsACP(v))?y=Yi(v).title"),
    (
        "fn=e=>{let t=e.title,n=e.tool_kind;",
        "fn=e=>{let t=factoryCompactTitle(e.title),n=e.tool_kind;",
    ),
    # Show the complete original title inside details if its label was shortened.
    # Use the upstream fenced-code formatter, including its backtick escaping.
    (
        "s=a.filter(e=>e.type===`text`),c=[];if(t===`execute`",
        "s=a.filter(e=>e.type===`text`),c=[];"
        "if(factoryCompactTitle(e.title)!==e.title)c.push(H(e.title));"
        "if(t===`execute`",
    ),
    # Details are opt-in. Preserve full ACP output there, including diff/text
    # content, instead of silently cutting it off after the first 1,000 chars.
    ("mn=e=>e.length>1e3?`${e.slice(0,V)}...`:e", "mn=e=>e"),
)


def patch_bundle(source):
    if hashlib.sha256(source.encode()).hexdigest() != ORIGINAL_SHA256:
        raise RuntimeError("Pinned Canvas activity bundle changed; review the upstream update")
    for old, new in REPLACEMENTS:
        if source.count(old) != 1:
            raise RuntimeError("Pinned Canvas activity anchor changed; review the upstream update")
        source = source.replace(old, new, 1)
    return HELPERS + source


def install(frontend, node):
    assets = frontend / "assets"
    path = assets / BUNDLE
    patched = patch_bundle(path.read_text())
    subprocess.run([node, "--input-type=module", "--check"], input=patched, text=True, check=True)
    # Vite filenames are immutable cache keys. Reversion the JS import graph
    # together, including the HTML entrypoints, so an existing browser loads the
    # changed component on the next deployment without a manual cache clear.
    version = hashlib.sha256(patched.encode()).hexdigest()[:12]
    names = {p.name: f"{p.stem}-factory-{version}.js" for p in assets.glob("*.js")}
    pattern = re.compile("|".join(re.escape(name) for name in names))
    updates = {}
    for source in [*assets.glob("*.js"), *frontend.rglob("*.html")]:
        text = patched if source == path else source.read_text()
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
