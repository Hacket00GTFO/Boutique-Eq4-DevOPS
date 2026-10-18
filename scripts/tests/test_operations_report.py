import importlib.util
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location('operations_report_test', Path(__file__).resolve().parents[1] / 'operations_report.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReportTests(unittest.TestCase):
    def test_cleartext_remote_endpoint_is_rejected(self):
        with patch.dict(os.environ, {'OPS_REPORT_URL': 'http://public.example.invalid', 'OPS_REPORT_TOKEN': 'fixture'}):
            with self.assertRaises(ValueError):
                module.publish({})

    def test_redirect_cannot_forward_a_service_credential(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(self.path)
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(302)
                self.send_header('Location', '/credential-trap')
                self.end_headers()
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.dict(os.environ, {'OPS_REPORT_URL': f'http://127.0.0.1:{server.server_port}', 'OPS_REPORT_TOKEN': 'fixture-token'}):
                with self.assertRaises(urllib.error.HTTPError):
                    module.publish({'kind': 'ansible'})
            self.assertEqual(requests, ['/api/v1/operations/reports'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
