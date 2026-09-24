"""Use ASCII headers and UTF-8 filename* for native automation downloads."""

import ast
import importlib.metadata
import importlib.util
from pathlib import Path

MARKER = '@router.get("/{automation_id}/tarball")\n'
HEADER = """headers={"Content-Disposition": f'attachment; filename="{safe_name}.tar"'},"""
HELPER = """def _factory_download_disposition(safe_name):
    from urllib.parse import quote
    fallback = safe_name.encode("ascii", errors="ignore").decode().strip() or "automation"
    encoded = quote(safe_name + ".tar", safe="", encoding="utf-8", errors="replace")
    return f'attachment; filename="{fallback}.tar"; filename*=UTF-8\\'\\'{encoded}'


"""


def patch_router(source):
    if source.count(MARKER) != 1 or source.count(HEADER) != 1:
        raise RuntimeError("Pinned automation download route changed; review filename patch")
    result = source.replace(MARKER, HELPER + MARKER).replace(
        HEADER, 'headers={"Content-Disposition": _factory_download_disposition(safe_name)},'
    )
    ast.parse(result)
    return result


def main():
    if importlib.metadata.version("openhands-automation") != "1.15.0":
        raise RuntimeError("Download filename integration requires automation 1.15.0")
    path = Path(importlib.util.find_spec("openhands.automation.router").origin)
    path.write_text(patch_router(path.read_text()))


if __name__ == "__main__":
    main()
