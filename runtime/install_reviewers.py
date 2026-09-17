"""Build factory roles from the pinned upstream review procedures."""

import json
from pathlib import Path


def install(root=Path("/opt/factory")):
    reviewers = root / "reviewers"
    agents = reviewers / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    alibaba = reviewers / "alibaba/skills/open-code-review-delegate/SKILL.md"
    roles = [
        (
            "alibaba-reviewer",
            "Alibaba Reviewer",
            "Review correctness and security using Alibaba OCR delegation and the host subscription.",
            alibaba.read_text()
            + "\n\nFactory integration: the controller has already run OCR's deterministic "
            "preparation. Read the supplied OCR input artifact and review every selected file. "
            "For GitHub archives, the controller supplies the complete GitHub file inventory "
            "and OCR rule groups; Git history is unavailable. Use the supplied patches and "
            "source, and report missing evidence. Do not run OCR's LLM-backed review command. "
            "The factory's supplied policy and JSON schema govern findings, coverage, "
            "traceability, and publication. Stay read-only; do not fix or delegate further.",
        ),
    ]
    for slug, name, description, instructions in roles:
        values = {"name": name, "description": description, "developer_instructions": instructions}
        (agents / f"{slug}.toml").write_text(
            "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()) + "\n"
        )


if __name__ == "__main__":
    install()
