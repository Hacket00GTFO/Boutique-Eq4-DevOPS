"""Bounded, fail-closed synchronization with the SSM supervisor."""
import json
import os
from pathlib import Path
import time

from common import SafeError


def require_process(pid):
    if pid <= 1:
        raise SafeError('Invalid SSM supervisor PID.')
    try:
        os.kill(pid, 0)
        stat = Path('/proc') / str(pid) / 'stat'
        if stat.exists() and stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
            raise ProcessLookupError()
    except ProcessLookupError:
        raise SafeError('SSM supervisor exited before Vault was ready; inspect the sanitized tunnel log.') from None


def wait_tunnel(ready_file, pid, timeout=120, clock=time.monotonic, sleep=time.sleep,
                alive=require_process):
    deadline = clock() + timeout
    while True:
        alive(pid)
        path = Path(ready_file)
        if path.exists():
            state = json.loads(path.read_text())
            if state.get('pid') != pid or state.get('port') != 18200 or not state.get('session'):
                raise SafeError('SSM readiness belongs to a different supervisor or port.')
            return
        if clock() >= deadline:
            raise SafeError('SSM readiness timed out; no Vault login was attempted.')
        sleep(min(1, max(0, deadline - clock())))
