"""Test native Kit egress against a disposable host fixture; no model credentials."""

import argparse
import http.server
import ipaddress
import json
import os
import socket
import subprocess
import threading
import urllib.request
from pathlib import Path
from uuid import uuid4

PROBE = """
import json, urllib.request
for url in URLS:
    for mode, handler in [('proxy', urllib.request.ProxyHandler()),
                          ('direct', urllib.request.ProxyHandler({}))]:
        try:
            response = urllib.request.build_opener(handler).open(url, timeout=8)
            reached = response.read(256).decode() == MARKER
            result = {'status': response.status, 'fixture_reached': reached}
        except Exception as error:
            result = {'error': type(error).__name__}
        print(json.dumps({'url': url, 'mode': mode, **result}), flush=True)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", default="sbx")
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--host", required=True, help="This host's reachable private IPv4 address")
    parser.add_argument("--private-name", required=True, help="DNS name resolving to --host")
    args = parser.parse_args()
    address = ipaddress.IPv4Address(args.host)
    if not address.is_private or address.is_loopback or address.is_unspecified:
        parser.error("--host must be a reachable private address on this host")
    addresses = {item[4][0] for item in socket.getaddrinfo(args.private_name, None)}
    if addresses != {args.host}:
        parser.error("--private-name must resolve only to --host")
    marker, name = uuid4().hex, "intentmade-network-" + uuid4().hex
    environment = {**os.environ, "SBX_NO_TELEMETRY": "1"}

    def run(*arguments):
        return subprocess.check_output(
            [args.command, *arguments], env=environment, text=True, timeout=180
        )

    def probe(urls):
        code = PROBE.replace("URLS", repr(urls)).replace("MARKER", repr(marker))
        rows = [json.loads(line) for line in run("exec", name, "python", "-c", code).splitlines()]
        if len(rows) != 2 * len(urls):
            raise RuntimeError("Incomplete probe output")
        for row in rows:
            print(json.dumps(row), flush=True)
        return rows

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(marker.encode())

        def log_message(self, *arguments):
            pass

    # Binding succeeds only for a local address. Never probe existing LAN services.
    with http.server.ThreadingHTTPServer((args.host, 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        private = [f"http://{host}:{port}" for host in (args.host, args.private_name)]
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for url in private:
                with opener.open(url, timeout=5) as response:
                    if response.read().decode() != marker:
                        raise RuntimeError("Host fixture positive control failed")
            try:
                run(
                    "create",
                    "--name",
                    name,
                    "--pull",
                    "never",
                    "--skills",
                    "off",
                    str(args.kit.resolve()),
                )
                denied = probe(private)
                public = probe(["https://api.github.com/zen"])
                # Prove routing with a rule scoped only to this disposable VM.
                run("policy", "allow", "network", "--sandbox", name, f"{args.private_name}:{port}")
                control = probe([private[1]])
                if not all(row.get("fixture_reached") for row in control):
                    raise RuntimeError("VM fixture control failed; denial result is inconclusive")
                if any("status" in row for row in denied):
                    raise RuntimeError("Kit allowed access to the private fixture")
                if not all(row.get("status") == 200 for row in public):
                    raise RuntimeError("Allowed public access failed")
                print(
                    "PASS private IP/DNS denial, public access, and VM routing control", flush=True
                )
            finally:
                sandboxes = json.loads(run("ls", "--json"))["sandboxes"]
                if any(item["name"] == name for item in sandboxes):
                    run("rm", "--force", name)
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
