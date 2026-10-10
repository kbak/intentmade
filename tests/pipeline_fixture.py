"""Reusable real-Git pipeline scenarios with scripted worker and agent boundaries."""

import json
import os
import re
import shutil
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import common
import run


def execute_local(command, cwd, **kwargs):
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=kwargs.get("timeout"),
    )
    return SimpleNamespace(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)


def seed_repository(source, bare, *, files=None, fixture=None, branch="factory/task"):
    source, bare = Path(source), Path(bare)
    if fixture is not None:
        shutil.copytree(fixture, source)
    common.git(["init", "-b", branch, str(source)])
    common.git(["config", "user.name", "Fixture"], cwd=source)
    common.git(["config", "user.email", "fixture@localhost"], cwd=source)
    for name, contents in (files or {}).items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    common.git(["add", "."], cwd=source)
    common.git(["commit", "-m", "base"], cwd=source)
    base = common.git(["rev-parse", "HEAD"], cwd=source).stdout.strip()
    common.git(["clone", "--bare", str(source), str(bare)])
    return {"repository": str(bare), "base": base, "branch": branch}


class PipelineScenario:
    def __init__(self, root, *, executor=execute_local):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.executor = executor
        self.contexts = []

    @contextmanager
    def worker(self, root, configs):
        workspace = SimpleNamespace(working_dir=str(root / "source"))

        def execute(command, cwd=None, **kwargs):
            return self.executor(command, cwd or workspace.working_dir, **kwargs)

        workspace.execute_command = execute
        yield workspace

    def worktree(self, workspace, traceability=False):
        self.contexts.append(traceability)
        source = Path(workspace.working_dir)
        checkout = source.parent.parent / "worktrees" / source.name
        checkout.parent.mkdir(parents=True, exist_ok=True)
        common.git(["worktree", "add", "-b", "openhands/test", str(checkout), "main"], cwd=source)
        workspace.working_dir = str(checkout)
        return "scripted-agent"

    @contextmanager
    def activate(self, *, implement=None, review=None, export=None):
        with ExitStack() as stack:
            for name, replacement in (
                ("DATA", self.root),
                ("git_identity", lambda: ("Fixture", "fixture@localhost")),
                ("worker", self.worker),
                ("worktree", self.worktree),
            ):
                stack.enter_context(patch.object(run, name, replacement))
            hooks = SimpleNamespace()
            for name, replacement in (
                ("converse", implement),
                ("review_code", review),
                ("export_task", export),
            ):
                if replacement is not None:
                    setattr(
                        hooks,
                        name,
                        stack.enter_context(patch.object(run, name, side_effect=replacement)),
                    )
            yield hooks


class TraceabilityScenario(PipelineScenario):
    def __init__(self, case, root):
        super().__init__(root)
        self.case = case
        seed = self.root / "seed"
        self.states = {
            "pilot": seed_repository(
                seed,
                self.root / "task.git",
                fixture=Path(__file__).parent / "fixtures/traceability",
            )
        }
        self.config = {
            "project": "pilot",
            "repository": "",
            "repair_attempts": 0,
            "publish_draft": False,
            "traceability_scope": json.loads(
                (Path(__file__).parent / "fixtures/traceability-scope.json").read_text()
            ),
        }
        self.artifact = self.root / "artifacts"
        self.artifact.mkdir()
        self.attempts = []
        self.contexts = []
        self.review_prompts = []
        self.request = "Preserve the 30 minute session promise; improve tests."

    def invoke(
        self,
        edit=lambda root, attempt: None,
        verdict="PASS",
        export=None,
        feedback=0,
        assessment=None,
        implementation_error=None,
    ):
        import review
        import review_report
        from review_fixture import assessed, change, evidence, specialist

        def implement(workspace, prompt, *args, **kwargs):
            self.attempts.append(prompt)
            self.case.assertEqual(kwargs.get("traceability"), "traceability_scope" in self.config)
            edit(Path(workspace.working_dir), len(self.attempts))
            if feedback:
                command = re.search(r"```sh\n(.*?)\n```", prompt, re.DOTALL).group(1)
                for _ in range(feedback):
                    result = workspace.execute_command(command, timeout=120)
                    self.case.assertEqual(result.exit_code, 0, result.stdout + result.stderr)
                bundles = list(
                    self.root.glob("job-*/traceability/pilot/agent-check-*/evidence/evidence.json")
                )
                self.case.assertEqual(len(bundles), feedback)
                self.case.assertTrue(
                    all(
                        json.loads(p.read_text())["predicate"]["status"] == "passed"
                        for p in bundles
                    )
                )
            if implementation_error:
                raise implementation_error
            return run.ImplementationResult(status="IMPLEMENTED", summary="Fixture edit")

        def review_stage(workspace, prompt, **kwargs):
            self.review_prompts.append(prompt)
            expected = kwargs.get("traceability", {})
            code = specialist()
            if expected:
                if assessment:
                    code = assessment(expected)
                else:
                    # These pre-existing cases test tracing/export, not semantic
                    # judgment. Supply explicit scripted review data to the gate.
                    paths = expected["pilot"]["changed_paths"]
                    status = "missing" if verdict == "CHANGES_REQUESTED" else "covered"
                    changes = (
                        [
                            change(
                                status,
                                changed_paths=paths,
                                remediation="Restore the approved session promise."
                                if status == "missing"
                                else None,
                            )
                        ]
                        if paths
                        else []
                    )
                    code = specialist(traceability_assessment=[assessed(changes)])

            self.case.assertEqual(kwargs["intent_projects"], ["pilot"])
            code["intent_alignment"] = [
                {
                    "project": "pilot",
                    "status": "aligned",
                    "references": ["supplied request", "requirements.md: Session expiration"],
                    "summary": "Scripted judgment for pipeline tests; semantic gaps are exercised separately.",
                }
            ]

            def converse(workspace, prompt, **options):
                inputs = json.loads(Path(kwargs["input_path"]).read_text())["projects"]
                code["coverage"] = [
                    {
                        "project": project,
                        **item,
                        "outcome": "reviewed",
                        "evidence": "Scripted fixture review.",
                    }
                    for project, spec in inputs.items()
                    for item in spec["files"]
                ]
                options["event_log"].append(evidence(code=code))

            with (
                patch.object(review, "converse", converse),
                patch.object(
                    review,
                    "consolidate",
                    side_effect=lambda workspace, result, **options: review_report.validate_report(
                        result, review_report.draft_report(result)
                    ),
                ),
            ):
                return review.review_code(workspace, prompt, **kwargs)

        with (
            self.activate(
                implement=implement, review=review_stage, export=export or run.export_task
            ) as hooks,
            patch.object(run, "publish") as publish,
            patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"}),
        ):
            try:
                result = run.execute_build(
                    [self.config],
                    "task",
                    self.request,
                    "",
                    None,
                    False,
                    self.artifact,
                    self.states,
                )
                if "traceability_scope" in self.config:
                    self.case.assertEqual(
                        set(hooks.review_code.call_args.kwargs["traceability"]), {"pilot"}
                    )
                return result
            finally:
                publish.assert_not_called()
