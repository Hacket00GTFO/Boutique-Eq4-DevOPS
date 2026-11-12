"""Recover failed first-boot workers only in the deliberate initial-setup window."""
import os
from pathlib import Path
import subprocess
import sys

from common import SafeError
from require_automation import read_nodes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'aws'))
from inspect_worker import main as inspect_worker, restart_unjoined_workers, PreparationError


def recover(values=os.environ, read=read_nodes, inspect=None, repair=restart_unjoined_workers):
    if (values.get('GITHUB_REF') != 'refs/heads/main'
            or values.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or values.get('VAULT_AUTOMATION_READY') != 'false'):
        raise SafeError('Initial worker recovery requires the deliberate main bootstrap window before activation.')
    if read().get('items'):
        print('EKS workers already registered; no initial worker restart requested.')
        return
    report = (inspect or (lambda: inspect_worker(emit=False)))()
    startup = report.get('startup', {})
    if startup.get('nodeadm-config', {}).get('state') != 'failed':
        print('EKS startup is not in the known failed nodeadm state; waiting for normal registration.')
        return
    if not startup.get('current_ec2_permission', {}).get('describe_allowed'):
        raise SafeError('Worker startup is failed and its current AWS permission is denied; verify the active Lab.')
    # Reuses the independent checks for an empty cluster, absent application namespace,
    # exact three owned running instances, expected node roles and inspected storage worker.
    repair(report)


if __name__ == '__main__':
    try:
        recover()
    except (SafeError, PreparationError) as error:
        sys.exit('Initial worker recovery stopped: ' + str(error))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Initial worker recovery stopped; private command output was suppressed.')
