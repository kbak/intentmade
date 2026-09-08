"""Trust the operator's read-only catalogs for ordinary Git inspection."""

import subprocess
from pathlib import Path

CATALOG = Path("/projects/repos")


def trust_catalog():
    """Trust only enumerated, operator-mounted catalogs for normal Git reads."""
    result = subprocess.run(
        ["git", "config", "--global", "--get-all", "safe.directory"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("Cannot read Git catalog trust configuration")
    existing = set(result.stdout.splitlines())
    for repo in sorted(CATALOG.iterdir()):
        if repo.is_symlink() or not repo.is_dir() or not (repo / ".git").exists():
            continue
        for path in (repo, repo / ".git"):
            if str(path) not in existing:
                subprocess.run(
                    ["git", "config", "--global", "--add", "safe.directory", str(path)],
                    check=True,
                )


if __name__ == "__main__":
    trust_catalog()
