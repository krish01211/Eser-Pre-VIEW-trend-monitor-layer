"""
Pre-VIEW Dashboard HTTP Server
================================
Minimal Python server that bridges the audit engine to the web dashboard.

Architecture (identical pattern to DC-648):
  Python engine  →writes→  data/*.json
  This server    →reads→   data/*.json  →serves→  Dashboard (browser)
  Dashboard      →POST→    This server  →writes→  data/command.json  →read by→  Engine

No external dependencies. Uses Python's built-in http.server.

Usage:
  python server.py
  Then open http://localhost:8080 in your browser.
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
import os
import json

DATA_DIR = 'data'
DASHBOARD_DIR = 'dashboard'
PORT = 8080


class AuditDashboardHandler(SimpleHTTPRequestHandler):
    """Serves the dashboard static files and provides a JSON API for audit data."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DASHBOARD_DIR, **kwargs)

    def do_GET(self):
        # Strip query parameters for matching
        path = self.path.split('?')[0]
        
        if path == '/api/feed':
            self._serve_data_file('feed.json')
        elif path == '/api/trend':
            self._serve_data_file('trend.json')
        elif path == '/api/status':
            self._serve_data_file('status.json')
        else:
            # Serve static files from dashboard/ directory
            super().do_GET()

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        super().end_headers()

    def do_POST(self):
        if self.path == '/api/command':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')

            os.makedirs(DATA_DIR, exist_ok=True)
            filepath = os.path.join(DATA_DIR, 'command.json')
            with open(filepath, 'w') as f:
                f.write(body)

            self._send_json(200, {'status': 'ok'})
        else:
            self.send_error(404)

    def _serve_data_file(self, filename):
        """Read a JSON file from the data directory and serve it."""
        filepath = os.path.join(DATA_DIR, filename)
        data = '{}'
        try:
            with open(filepath, 'r') as f:
                data = f.read()
        except (FileNotFoundError, OSError):
            pass

        self._send_raw(200, 'application/json', data.encode('utf-8'))

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self._send_raw(code, 'application/json', body)

    def _send_raw(self, code, content_type, body):
        try:
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-cache, no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # Browser closed the connection during a poll, perfectly normal

    def log_message(self, format, *args):
        """Suppress repetitive GET logs to keep console clean."""
        if hasattr(self, 'path') and '/api/' not in self.path:
            super().log_message(format, *args)


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(DASHBOARD_DIR, exist_ok=True)

    server = HTTPServer(('localhost', PORT), AuditDashboardHandler)
    print(f'\n  Pre-VIEW Audit Trend Monitor -- Dashboard Server')
    print(f'  =======================================================')
    print(f'  Dashboard:  http://localhost:{PORT}')
    print(f'  Feed:       http://localhost:{PORT}/api/feed')
    print(f'  Trend:      http://localhost:{PORT}/api/trend')
    print(f'  Status:     http://localhost:{PORT}/api/status')
    print(f'  Commands:   POST http://localhost:{PORT}/api/command')
    print(f'\n  Press Ctrl+C to stop.\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServer stopped.')
        server.server_close()
