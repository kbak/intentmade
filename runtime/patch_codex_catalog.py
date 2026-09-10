"""Add Astra and extra-high picker metadata to the pinned Canvas 1.17.

The SDK and compiled frontend each contain the Codex provider registry. Fail
the image build if either changes so this backport is reviewed on a base-image
upgrade. Runtime model availability still comes from the authenticated adapter.
"""

import hashlib
import re
from pathlib import Path

from openhands.sdk.settings import acp_providers


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"Expected one pinned catalog fragment: {old!r}")
    return source.replace(old, new, 1)


def main() -> None:
    if acp_providers.CODEX_ACP_VERSION != "1.10.0":
        raise RuntimeError("Catalog patch requires the pinned Codex ACP 1.10.0")
    registry = Path(acp_providers.__file__)
    source = replace_once(
        registry.read_text(),
        '    ACPModelOption(id="gpt-6-astra", label="GPT-6 Astra"),\n',
        '    ACPModelOption(id="gpt-6-astra", label="GPT-6 Astra"),\n'
        '    ACPModelOption(id="gpt-6-astra/xhigh", label="GPT-6 Astra (Extra high)"),\n',
    )

    frontend = Path("/opt/agent-canvas/frontend")
    old_models = "available_models:[{id:`gpt-5.6`,label:`GPT-5.6`}"
    bundles = [p for p in (frontend / "assets").glob("*.js") if old_models in p.read_text()]
    if len(bundles) != 1:
        raise RuntimeError("Expected one pinned frontend Codex provider registry")
    bundle = bundles[0]
    patched = replace_once(
        bundle.read_text(),
        old_models,
        "available_models:[{id:`gpt-6-astra`,label:`GPT-6 Astra`},"
        "{id:`gpt-6-astra/xhigh`,label:`GPT-6 Astra (Extra high)`},"
        "{id:`gpt-5.6`,label:`GPT-5.6`}",
    )
    patched = replace_once(
        patched,
        "@agentclientprotocol/codex-acp@1.1.7",
        "@agentclientprotocol/codex-acp@1.10.0",
    )

    # Assets are served immutable. Version the whole JS/CSS import graph so
    # cached importers cannot continue loading the old model registry.
    revision = hashlib.sha256(patched.encode()).hexdigest()[:12]
    assets = [p for p in (frontend / "assets").iterdir() if p.suffix in {".js", ".css"}]
    names = {p.name: f"{p.stem}-factory-{revision}{p.suffix}" for p in assets}
    pattern = re.compile("|".join(re.escape(name) for name in names))
    registry.write_text(source)
    bundle.write_text(patched)
    for path in frontend.rglob("*"):
        if path.suffix in {".html", ".js", ".css", ".json"}:
            text = path.read_text()
            updated = pattern.sub(lambda match: names[match[0]], text)
            if updated != text:
                path.write_text(updated)
    for path in assets:
        path.rename(path.with_name(names[path.name]))


if __name__ == "__main__":
    main()
