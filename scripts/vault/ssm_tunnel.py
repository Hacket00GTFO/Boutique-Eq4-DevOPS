"""Foreground SSM tunnel supervisor; SIGTERM closes the remote session as well."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time

from common import SafeError


def publish_ready(path, session, port):
    temporary = path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump({'pid': os.getpid(), 'session': session, 'port': port}, stream)
    temporary.replace(path)


def stop_tunnel(pid_file):
    ready_file = pid_file.with_suffix('.ready')
    session_file = pid_file.with_suffix('.session')
    if pid_file.exists():
        pid = int(pid_file.read_text())
        if pid <= 1:
            raise SafeError('Invalid supervisor PID; cleanup stopped.')
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pid_file.unlink(missing_ok=True)
        deadline = time.monotonic() + 45
        while pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.25)
    if session_file.exists():
        result = subprocess.run(['aws', 'ssm', 'terminate-session', '--region', 'us-east-1',
                                 '--session-id', session_file.read_text().strip()],
                                capture_output=True, timeout=30)
        if result.returncode:
            raise SafeError('Remote SSM closure could not be verified; session state and sanitized log retained.')
        session_file.unlink(missing_ok=True)
    if pid_file.exists():
        raise SafeError('SSM supervisor did not exit within the cleanup deadline; state retained.')
    ready_file.unlink(missing_ok=True)


def session_diagnostic(line):
    """Classify AWS failures without forwarding process output or request details."""
    normalized = line.lower()
    for patterns, message in (
        (('expiredtoken', 'requestexpired', 'security token included in the request is expired'),
         'SSM: temporary AWS credentials expired; refresh all three Learner Lab credentials.'),
        (('unable to locate credentials', 'nocredentials'),
         'SSM: AWS credentials are missing in this terminal.'),
        (('invalidclienttokenid', 'unrecognizedclientexception'),
         'SSM: AWS credentials are invalid; check that all three belong to the same Lab session.'),
        (('accessdenied', 'not authorized to perform'),
         'SSM: AWS denied session access; verify the active role and StartSession permission.'),
        (('targetnotconnected',),
         'SSM: the target instance is not connected; check instance state and SSM Online status.'),
        (('sessionmanagerplugin is not found', 'session-manager-plugin: not found'),
         'SSM: the Session Manager plugin is missing from PATH.'),
    ):
        if any(pattern in normalized for pattern in patterns):
            return message
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance-id')
    parser.add_argument('--pid-file', required=True)
    parser.add_argument('--ready-file')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--port', type=int, default=18200)
    args = parser.parse_args()
    if os.name != 'posix':
        raise SafeError('Run the SSM supervisor on Linux/WSL.')
    if args.stop:
        stop_tunnel(Path(args.pid_file))
        return
    if not re.fullmatch(r'i-[0-9a-f]{8,17}', args.instance_id or '') or not 1024 <= args.port <= 65535:
        raise ValueError('Invalid instance or local port.')
    # Refuse an occupied port, rather than trusting an unrelated local service.
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError:
            raise SafeError('SSM local port is already occupied; no existing listener was trusted.') from None
    pid_file = Path(args.pid_file)
    session_file = pid_file.with_suffix('.session')
    ready_file = Path(args.ready_file) if args.ready_file else pid_file.with_suffix('.ready')
    if any(path.exists() for path in (pid_file, session_file, ready_file, ready_file.with_suffix('.tmp'))):
        raise ValueError('Use fresh supervisor state files.')
    os.umask(0o077)
    pid_file.write_text(str(os.getpid()))
    session = []
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    child = None
    reader = None
    try:
        child = subprocess.Popen([
            'aws', 'ssm', 'start-session', '--region', 'us-east-1', '--target', args.instance_id,
            '--document-name', 'AWS-StartPortForwardingSession', '--parameters',
            json.dumps({'portNumber': ['8200'], 'localPortNumber': [str(args.port)]})],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)

        def read_session():
            reported = set()
            for line in child.stdout:
                diagnostic = session_diagnostic(line)
                if diagnostic and diagnostic not in reported:
                    reported.add(diagnostic)
                    print(diagnostic, file=sys.stderr, flush=True)
                match = re.search(r'Starting session with SessionId:\s*([\w@.:+=,-]+)', line)
                if match:
                    session.append(match.group(1))
                    session_file.write_text(session[-1])
        reader = threading.Thread(target=read_session, daemon=True)
        reader.start()
        deadline = time.monotonic() + 120
        ready = False
        while not stop.wait(0.25):
            if child.poll() is not None:
                if reader is not None:
                    reader.join(timeout=2)
                raise RuntimeError('SSM exited before the tunnel was closed.')
            if not ready and time.monotonic() > deadline:
                raise SafeError('SSM tunnel startup timed out.')
            if not ready and session:
                try:
                    with socket.create_connection(('127.0.0.1', args.port), timeout=1):
                        if child.poll() is not None:
                            raise SafeError('SSM exited during tunnel startup.')
                        publish_ready(ready_file, session[-1], args.port)
                        ready = True
                        print('SSM tunnel ready; TLS and Vault health still require verification.', flush=True)
                except OSError:
                    pass
    finally:
        ready_file.unlink(missing_ok=True)
        try:
            if child is not None:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                if reader is not None:
                    reader.join(timeout=2)
            if session:
                result = subprocess.run(['aws', 'ssm', 'terminate-session', '--region', 'us-east-1',
                                         '--session-id', session[-1]], capture_output=True, timeout=30)
                if result.returncode:
                    print('SSM termination needs verification; session state file retained.', file=sys.stderr)
                else:
                    session_file.unlink(missing_ok=True)
        finally:
            pid_file.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except SafeError as error:
        sys.exit(str(error))
    except Exception:
        print('SSM tunnel failed; check session permissions, SSM Agent and network egress.', file=sys.stderr)
        sys.exit(1)
