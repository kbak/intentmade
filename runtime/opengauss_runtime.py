"""Adapt pinned OpenGauss managed Codex proof guidance to a native ACP session.

No provider login, model selection, terminal driver or credential staging occurs
here. OpenHands/Codex ACP owns those. Keep generated state outside project source.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path("/opt/factory/opengauss")


def install_skill(assets=ROOT):
    """Register immutable skill assets in the existing native Codex home."""
    source = assets / "lean4-skills/plugins/lean4/skills/lean4"
    if not (source / "SKILL.md").is_file():
        raise ValueError("Pinned Lean skill is unavailable")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    directory = codex_home / "skills"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "lean4"
    if destination.is_symlink() and destination.resolve() == source.resolve():
        return
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Existing Lean skill conflicts with pinned workflow")
    destination.symlink_to(source, target_is_directory=True)


# [impl->req~im-opengauss-workflow~1]
def prepare(project, assets=ROOT, mode="autoprove"):
    project = Path(project).resolve(strict=True)
    if not any((project / name).is_file() for name in ("lakefile.lean", "lakefile.toml")):
        raise ValueError("OpenGauss requires the ACP session cwd to be a Lean project")
    if mode not in ("autoprove", "lean-mcp"):
        raise ValueError("Unsupported proof workflow")
    mcp = {
        "command": str(assets / "venv/bin/lean-lsp-mcp"),
        "args": [],
        "env": {"LEAN_PROJECT_PATH": str(project)},
    }
    if not Path(mcp["command"]).is_file():
        raise ValueError("Pinned Lean MCP runtime is incomplete")
    if mode == "lean-mcp":
        # The ablation adds only the same pinned MCP tools: no skill registration,
        # managed workflow generation or secondary planning/tracking instructions.
        return {"instructions": "", "skills": [], "mcp": mcp}
    # Import only the source installed in the optional immutable runtime. Never
    # import Python from the candidate project or resolve floating dependencies.
    sys.path.insert(0, str(assets / "source"))
    from gauss_cli import autoformalize as gauss

    pins = json.loads((assets / "pins.json").read_text())
    plugin = assets / "lean4-skills/plugins/lean4"
    skill = plugin / "skills/lean4"
    guide = plugin / "commands/autoprove.md"
    for path in (skill / "SKILL.md", guide, assets / "venv/bin/lean-lsp-mcp"):
        if not path.is_file():
            raise ValueError("Pinned OpenGauss runtime is incomplete")
    runtime = Path(tempfile.mkdtemp(prefix="intentmade-opengauss-"))
    if runtime.is_relative_to(project):
        runtime.rmdir()
        raise ValueError("OpenGauss runtime state must be outside project source")
    workflow = gauss._parse_managed_workflow_command(
        "/autoprove --max-cycles=1 --max-total-runtime=30m --deep=never "
        "--commit=never --checkpoint=false --formalize=never --statement-policy=preserve"
    )
    config_path = runtime / "session-config.json"
    context = gauss._write_startup_context(
        startup_dir=runtime,
        backend_name="codex",
        project_root=project,
        lean_root=project,
        active_cwd=project,
        user_instruction="Follow only the factory's accepted task and its pinned IntentBond guidance.",
        workflow=workflow,
        plugin_root=plugin,
        mcp_config_path=config_path,
        backend_config_path=config_path,
        skills_root=skill,
    )
    instructions = runtime / "instructions.md"
    gauss._write_codex_instructions(
        instructions_path=instructions,
        startup_context_path=context,
        project_root=project,
        lean_root=project,
        active_cwd=project,
        skills_root=skill,
        plugin_root=plugin,
        scripts_root=plugin / "lib/scripts",
        references_root=skill / "references",
        workflow=workflow,
    )
    # Preserve the factory's built-in persona/instructions and ACP-provided
    # permissions. Supply the generated upstream guidance as an additive layer.
    guidance = instructions.read_text() + "\n\n" + context.read_text()
    guidance += """

## Factory proof task
Use the `factory-lean-lsp` MCP server for Lean goals and diagnostics.
Follow the repository's AGENTS.md, accepted requirements and pinned IntentBond skill.
Work only on the approved proof obligations. Preserve target statements, existing
definitions and assumptions; ask for input if the task requires choosing semantics.
Do not introduce sorry, admit or additional axioms as completed proofs.
The controller's validation and independent review determine acceptance.
Return the factory's requested structured response when the workflow ends.
"""
    result = {
        "instructions": guidance,
        "skills": [{"path": str(skill), "enabled": True}],
        "mcp": mcp,
    }
    config_path.write_text(
        json.dumps(
            {
                "skills": result["skills"],
                "mcp_servers": {"factory-lean-lsp": result["mcp"]},
                "instructions_path": str(instructions),
            },
            indent=2,
        )
        + "\n"
    )
    # Source identities and generated nonsecret context remain available to the
    # worker/evidence collector without copying credentials or execution diaries.
    (runtime / "provenance.json").write_text(json.dumps(pins, indent=2) + "\n")
    return result


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--install-skill":
            install_skill()
        else:
            print(
                json.dumps(
                    prepare(sys.argv[1], mode=sys.argv[2] if len(sys.argv) > 2 else "autoprove")
                )
            )
    except Exception as exc:
        # Keep candidate text and provider environment out of startup diagnostics.
        print(f"OpenGauss preparation failed ({type(exc).__name__})", file=sys.stderr)
        sys.exit(1)
