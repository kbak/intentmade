"""Bound the pinned upstream PR downloader; retain its safe extraction rules."""

import ast
from pathlib import Path

path = Path("/opt/factory/upstream/reviews.py")
source = path.read_text()
old = """        with urllib.request.urlopen(req) as response:
            archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
        with archive:
            members = archive.getmembers()"""
new = """        from resource_limits import archive_members, repository_archive

        with repository_archive(req) as archive:
            members = archive_members(archive)"""
if source.count(old) != 1:
    raise RuntimeError("Pinned PR archive downloader changed; review upstream bounds")
source = source.replace(old, new, 1)
ast.parse(source)
# Preserve the checksum-verifiable upstream input alongside the runtime variant.
path.with_name("reviews_bounded.py").write_text(source)
