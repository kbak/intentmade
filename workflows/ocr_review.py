"""Prepare Alibaba delegation inputs without calling a model or exposing parent credentials."""

import json
import shlex
from pathlib import Path, PurePosixPath


def checked_path(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Invalid review path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise ValueError("Review paths must stay inside source")
    return value


def command(workspace, args, source, environment=()):
    # No provider discovery, user rules, inherited Git configuration, or secrets.
    argv = [
        "env",
        "-i",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "HOME=/nonexistent",
        "GIT_CONFIG_NOSYSTEM=1",
        "GIT_CONFIG_GLOBAL=/dev/null",
        "GIT_TERMINAL_PROMPT=0",
        "GIT_OPTIONAL_LOCKS=0",
        *environment,
        *args,
    ]
    result = workspace.execute_command(shlex.join(argv), cwd=str(source), timeout=120)
    if result.exit_code:
        raise RuntimeError(f"OCR preparation failed: {result.stderr[-1500:]}")
    return result.stdout


def ocr(workspace, args, source, environment=()):
    value = json.loads(
        command(
            workspace,
            [
                "ocr",
                "delegate",
                args[0],
                "--rule",
                "/opt/factory/reviewers/rule.json",
                *args[1:],
            ],
            source,
            environment,
        )
    )
    if not isinstance(value, dict) or value.get("schema_version") != "1":
        raise ValueError("Unsupported OCR delegation output schema")
    return value


def prepare(workspace, sources, destination):
    if not sources or destination is None:
        raise ValueError("Review requires controller-supplied sources and an OCR input artifact")
    expected, prepared = {}, {}
    for project, spec in sources.items():
        source = spec["source"]
        environment = ()
        if "files" in spec:
            files = spec["files"]
            if len(files) != spec["changed_files"]:
                raise ValueError("GitHub's changed-file inventory is incomplete")
            # Rule resolution requires Git even for an archive. Give it empty,
            # external metadata; do not invent commits or alter the source archive.
            metadata = Path(destination).parent / ("ocr-git-" + project)
            command(workspace, ["git", "init", "--bare", str(metadata)], source)
            environment = (f"GIT_DIR={metadata}", f"GIT_WORK_TREE={source}")
            inventory = [
                {"path": checked_path(item["filename"]), "status": item["status"]} for item in files
            ]
            preview = {"mode": "github-archive", "to": spec["candidate"], "files": files}
            flags = []
        else:
            flags = ["--from", spec["base"], "--to", spec["candidate"]]
            preview = ocr(
                workspace, ["preview", "--format", "json", "--repo", source, *flags], source
            )
            entries = preview["reviewable_files"] + preview["excluded_files"]
            if len(entries) != preview["total_files"]:
                raise ValueError("OCR preview omitted changed files")
            # Exclusions are context, never permission for target-owned rules to
            # hide changes. The reviewer accounts for excluded files as well.
            inventory = [
                {"path": checked_path(item["path"]), "status": item["status"]} for item in entries
            ]
        keys = [(item["path"], item["status"]) for item in inventory]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate review inventory entries")
        groups = []
        paths = list(dict.fromkeys(item["path"] for item in inventory))
        for start in range(0, len(paths), 40):
            result = ocr(
                workspace,
                [
                    "rule",
                    "--format",
                    "json",
                    "--repo",
                    source,
                    *flags,
                    "--",
                    *paths[start : start + 40],
                ],
                source,
                environment,
            )
            groups.extend(result["groups"])
        resolved = [path for group in groups for path in group["files"]]
        if len(resolved) != len(set(resolved)) or set(resolved) != set(paths):
            raise ValueError("OCR rules do not cover the selected paths")
        expected[project] = {
            "source": source,
            "candidate": spec["candidate"],
            "base": spec.get("base"),
            "files": inventory,
        }
        prepared[project] = {**expected[project], "preview": preview, "rule_groups": groups}
    payload = {"engine": "alibaba-ocr-delegate", "version": "1.12.4", "projects": prepared}
    Path(destination).write_text(json.dumps(payload, indent=2))
    return expected
