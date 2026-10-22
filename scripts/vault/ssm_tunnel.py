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
    parser.add_argument('--instance-id', required=True)
    parser.add_argument('--pid-file', required=True)
    parser.add_argument('--port', type=int, default=18200)
    args = parser.parse_args()
    if not re.fullmatch(r'i-[0-9a-f]{8,17}', args.instance_id) or not 1024 <= args.port <= 65535:
        raise ValueError('Invalid instance or local port.')
    if os.name != 'posix':
        raise ValueError('Run the SSM supervisor on Linux/WSL.')
    # Refuse an occupied port, rather than trusting an unrelated local service.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    pid_file = Path(args.pid_file)
    session_file = pid_file.with_suffix('.session')
    if pid_file.exists() or session_file.exists():
        raise ValueError('Use fresh supervisor state files.')
    pid_file.write_text(str(os.getpid()))
    session = []
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    child = None
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
        deadline = time.monotonic() + 90
        ready = False
        while not stop.wait(0.25):
            if child.poll() is not None:
                reader.join(timeout=2)
                raise RuntimeError('SSM exited before the tunnel was closed.')
            if not ready:
                try:
                    with socket.create_connection(('127.0.0.1', args.port), timeout=1):
                        ready = True
                        print('SSM tunnel ready; TLS and Vault health still require verification.', flush=True)
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('SSM tunnel startup timed out.')
    finally:
        if child is not None:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        if session:
            result = subprocess.run(['aws', 'ssm', 'terminate-session', '--region', 'us-east-1',
                                     '--session-id', session[-1]], capture_output=True, timeout=30)
            if result.returncode:
                print('SSM termination needs verification; session state file retained.', file=sys.stderr)
            else:
                session_file.unlink(missing_ok=True)
        pid_file.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('SSM tunnel failed; check session permissions, SSM Agent and network egress.', file=sys.stderr)
        sys.exit(1)
