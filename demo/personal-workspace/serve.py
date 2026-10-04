"""Small loopback-only demo server with no system MIME database dependency."""
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

root = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    def guess_type(self, path):
        # Explicit local demo types avoid optional reads of host MIME databases.
        return {
            '.html': 'text/html; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.js': 'text/javascript; charset=utf-8',
            '.json': 'application/json',
            '.csv': 'text/csv; charset=utf-8',
            '.md': 'text/plain; charset=utf-8',
        }.get(Path(path).suffix.lower(), 'application/octet-stream')


with HTTPServer(('127.0.0.1', 8765), partial(Handler, directory=str(root))) as server:
    print('Personal demo serving on loopback port 8765', flush=True)
    server.serve_forever()
