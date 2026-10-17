#!/usr/bin/env python3
"""Disposable loopback fixture for the paid preflight; no model calls."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import sys

TOOLS = ('claude-code', 'codex', 'opencode', 'muse')


def serve(root):
    endpoints = {tool: {'body': 'loopback-' + secrets.token_hex(16)} for tool in TOOLS}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            tool = self.path.removeprefix('/ok/')
            if self.path != '/ok/' + tool or tool not in endpoints:
                self.send_error(404)
                return
            body = endpoints[tool]['body']
            evidence = root / ('loopback-' + tool + '.json')
            evidence.write_text(json.dumps(dict(tool=tool, body=body, requested=True)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    with HTTPServer(('127.0.0.1', 0), Handler) as server:
        for tool, item in endpoints.items():
            item['url'] = f'http://127.0.0.1:{server.server_port}/ok/{tool}'
        (root / 'loopback-endpoints.json').write_text(json.dumps(endpoints))
        server.serve_forever()


if __name__ == '__main__':
    serve(Path(sys.argv[1]))
