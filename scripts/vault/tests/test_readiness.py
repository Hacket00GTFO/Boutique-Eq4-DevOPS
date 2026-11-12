"""Behavioral coverage for delayed tunnels, fatal TLS, auto-unseal and storage ordering."""
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import SafeError, Vault, VaultUnavailable
from require_automation import await_runtime, cluster
from tunnel_state import wait_tunnel


class Clock:
    def __init__(self):
        self.now = 0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ReadinessTests(unittest.TestCase):
    def test_delayed_tunnel_is_not_ready_until_its_own_session_is_published(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / 'tunnel.ready'
            def sleep(seconds):
                clock.sleep(seconds)
                if clock.now == 3:
                    ready.write_text(json.dumps({'pid': 1234, 'port': 18200, 'session': 'owned-session'}))
            wait_tunnel(ready, 1234, clock=clock.clock, sleep=sleep, alive=lambda _: None)
            self.assertEqual(clock.now, 3)
            with self.assertRaisesRegex(SafeError, 'different supervisor'):
                wait_tunnel(ready, 4321, alive=lambda _: None)

    def test_missing_tunnel_times_out_and_dead_supervisor_fails_immediately(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SafeError, 'timed out'):
                wait_tunnel(Path(directory) / 'missing', 1234, timeout=3,
                            clock=clock.clock, sleep=clock.sleep, alive=lambda _: None)
            self.assertEqual(clock.now, 3)
            dead = Mock(side_effect=SafeError('supervisor exited'))
            with self.assertRaisesRegex(SafeError, 'exited'):
                wait_tunnel(Path(directory) / 'missing', 1234, alive=dead)
            dead.assert_called_once()

    def test_runtime_retries_only_transient_connection_or_seal_states(self):
        vault = Mock()
        vault.ready.side_effect = [VaultUnavailable('connecting'), VaultUnavailable('sealed'), None]
        vault.request.return_value = (200, {'type': 'awskms'})
        clock = Clock()
        await_runtime(vault, clock=clock.clock, sleep=clock.sleep)
        self.assertEqual(vault.ready.call_count, 3)
        for failure in (SafeError('TLS invalid'), SafeError('HTTP 403')):
            vault.reset_mock()
            vault.ready.side_effect = failure
            with self.assertRaises(SafeError):
                await_runtime(vault, sleep=Mock(side_effect=AssertionError('fatal error retried')))
            vault.ready.assert_called_once()
        vault.ready.side_effect = VaultUnavailable('sealed')
        with self.assertRaisesRegex(SafeError, 'timed out'):
            await_runtime(vault, timeout=4, clock=clock.clock, sleep=clock.sleep)

    def test_tls_and_connection_errors_are_distinct_and_never_echo_private_details(self):
        client = Vault.__new__(Vault)
        client.url, client.token, client.context = 'https://localhost:18200', None, None
        for reason, kind in ((ConnectionRefusedError('private-fixture'), VaultUnavailable),
                             (ssl.SSLCertVerificationError('private-fixture'), SafeError)):
            with patch('urllib.request.urlopen', side_effect=urllib.error.URLError(reason)):
                with self.assertRaises(kind) as error:
                    client.ready()
                self.assertNotIn('private-fixture', str(error.exception))
                if isinstance(reason, ssl.SSLError):
                    self.assertNotIsInstance(error.exception, VaultUnavailable)
        sealed = urllib.error.HTTPError(client.url, 503, 'private-fixture', {},
                                        io.BytesIO(b'{"initialized":true,"sealed":true}'))
        with patch('urllib.request.urlopen', side_effect=sealed), self.assertRaises(VaultUnavailable):
            client.ready()

    def test_storage_is_checked_only_after_all_expected_workers_are_ready(self):
        def node(storage=False, ready=True):
            return {'metadata': {'labels': {'eks.amazonaws.com/nodegroup': 'boutique-eq4-eks-storage' if storage else 'boutique-eq4-eks-nodes',
                                            'boutique.dev/node-purpose': 'storage-controller' if storage else 'app'}},
                    'status': {'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}]}}
        read = Mock(side_effect=[{'items': []}, {'items': [node(), node(), node(True, False)]},
                                 {'items': [node(), node(), node(True)]}])
        def addon(*args):
            self.assertEqual(read.call_count, 3)
            return {'addon': {'status': 'ACTIVE'}}
        cluster(read=read, call=addon, sleep=lambda _: None)
        call = Mock()
        with self.assertRaises(SafeError):
            cluster(read=lambda: {'items': []}, call=call, attempts=2, sleep=lambda _: None)
        call.assert_not_called()


@unittest.skipUnless(os.name == 'posix', 'SSM supervisor requires POSIX process groups')
class SupervisorProcessTests(unittest.TestCase):
    def test_occupied_port_and_expired_credentials_never_publish_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'ssm_tunnel.py'),
                       '--instance-id', 'i-12345678', '--pid-file', str(root / 'tunnel.pid')]
            with socket.socket() as occupied:
                occupied.bind(('127.0.0.1', 0))
                port = occupied.getsockname()[1]
                result = subprocess.run(command + ['--port', str(port)], capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('already occupied', result.stderr)
                self.assertFalse((root / 'tunnel.pid').exists())
            fake = root / 'aws'
            fake.write_text('#!' + sys.executable + '\n'
                            "import sys\nprint('ExpiredToken: private-credential-fixture', flush=True)\nsys.exit(1)\n")
            fake.chmod(0o700)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(command + ['--port', str(port)], env=env, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('credentials expired', result.stderr)
            self.assertNotIn('private-credential-fixture', result.stderr)
            self.assertFalse((root / 'tunnel.ready').exists())
            self.assertFalse((root / 'tunnel.pid').exists())

    def test_delayed_listener_publishes_readiness_and_term_closes_remote_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'aws'
            fake.write_text('#!' + sys.executable + '\n' + '''import json, os, socket, sys, time
from pathlib import Path
if 'terminate-session' in sys.argv:
    Path(os.environ['TEST_TERMINATED']).write_text('closed')
    sys.exit(0)
port = int(json.loads(sys.argv[sys.argv.index('--parameters') + 1])['localPortNumber'][0])
print('Starting session with SessionId: isolated-owned-session', flush=True)
print('private-output-that-must-never-be-logged', flush=True)
time.sleep(0.4)
with socket.socket() as listener:
    listener.bind(('127.0.0.1', port))
    listener.listen()
    while True:
        connection, _ = listener.accept()
        connection.close()
''')
            fake.chmod(0o700)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'], TEST_TERMINATED=str(root / 'closed'))
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'ssm_tunnel.py'),
                       '--instance-id', 'i-12345678', '--pid-file', str(root / 'tunnel.pid'), '--port', str(port)]
            with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
                try:
                    deadline = time.monotonic() + 10
                    while not (root / 'tunnel.ready').exists() and time.monotonic() < deadline:
                        self.assertIsNone(process.poll())
                        time.sleep(0.05)
                    self.assertEqual(json.loads((root / 'tunnel.ready').read_text())['pid'], process.pid)
                finally:
                    process.terminate()
                output = process.communicate(timeout=15)[0]
                self.assertEqual(process.returncode, 0, output)
                self.assertNotIn('private-output-that-must-never-be-logged', output)
                self.assertTrue((root / 'closed').exists())
                self.assertFalse((root / 'tunnel.ready').exists())
                self.assertFalse((root / 'tunnel.pid').exists())
                self.assertFalse((root / 'tunnel.session').exists())


if __name__ == '__main__':
    unittest.main()
