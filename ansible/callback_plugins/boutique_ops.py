"""Safe aggregate callback: task/status/recap only, never result stdout or args."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import time
import uuid
from ansible.plugins.callback import CallbackBase

DOCUMENTATION = r'''
name: boutique_ops
type: aggregate
short_description: Publish sanitized execution metadata to the administrative panel
description:
  - Saves structured task statuses and recap counts, excluding command output and module arguments.
  - Optionally publishes to the internal reporting endpoint configured by the operator.
requirements:
  - Enable in callbacks_enabled.
'''


def label(value):
    value = str(value)
    if '{{' in value or '-----BEGIN' in value:
        return '[contenido omitido]'
    value = ''.join(c for c in value if c.isprintable())[:160]
    value = re.sub(r'\b(?:hvs|hvb|hvr)\.[A-Za-z0-9._-]+', '[token oculto]', value)
    value = re.sub(r'(?i)\b([\w-]*(?:token|password|secret|credential|unseal)[\w-]*)\s*[:=]\s*\S+', r'\1=[oculto]', value)
    return re.sub(r'\b[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@', '[conexión oculta]@', value, flags=re.I)


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'aggregate'
    CALLBACK_NAME = 'boutique_ops'
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self.root = Path(__file__).resolve().parents[2]
        self.enabled = False
        self.warned = False
        self.sequence = 0
        self.run_id = str(uuid.uuid4())
        self.started = time.monotonic()
        self.report = {'playbook': 'site.yml', 'status': 'running',
                       'started_at': datetime.now(timezone.utc).isoformat(),
                       'events': [], 'elapsed': 0, 'counters': {k: 0 for k in ('ok', 'changed', 'failed', 'unreachable', 'skipped')}}

    def emit(self, kind, text='', host=''):
        if not self.enabled:
            return
        self.sequence += 1
        self.report['elapsed'] = min(604800, int(time.monotonic() - self.started))
        self.report['events'].append({'type': kind, 'label': label(text), 'host': label(host)[:100], 'elapsed': self.report['elapsed']})
        self.report['events'] = self.report['events'][-400:]
        envelope = {'kind': 'ansible', 'run_id': self.run_id, 'sequence': self.sequence, 'report': self.report}
        try:
            directory = Path(os.environ.get('OPS_REPORT_DIR', str(self.root / '.vault-lab/runs')))
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            target = directory / (self.run_id + '.json')
            temporary = directory / (self.run_id + '.tmp')
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
                json.dump(envelope, stream)
            os.replace(temporary, target)
            if os.environ.get('OPS_REPORT_URL'):
                spec = importlib.util.spec_from_file_location('boutique_operations_report', self.root / 'scripts/operations_report.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.publish(envelope, timeout=1)
        except Exception:
            if not self.warned:
                self._display.warning('Operations telemetry could not be delivered; Ansible execution is independent. No private details logged.')
                self.warned = True

    def v2_playbook_on_start(self, playbook):
        name = Path(playbook._file_name).name
        self.enabled = name in ('site.yml', 'local-check.yml')
        self.report['playbook'] = name

    def v2_playbook_on_play_start(self, play):
        self.emit('play', play.name or 'Play')

    def v2_playbook_on_task_start(self, task, is_conditional):
        self.emit('task', 'Tarea protegida (no_log)' if task.no_log else (task.name or task.action))

    def result(self, result, kind):
        protected = result._task.no_log or result._result.get('_ansible_no_log', False)
        self.emit(kind, 'Resultado protegido (no_log)' if protected else (result._task.name or result._task.action), result._host.get_name())

    def v2_runner_on_ok(self, result):
        self.result(result, 'changed' if result.is_changed() else 'ok')

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self.result(result, 'failed')

    def v2_runner_on_unreachable(self, result):
        self.result(result, 'unreachable')

    def v2_runner_on_skipped(self, result):
        self.result(result, 'skipped')

    def v2_playbook_on_stats(self, stats):
        counters = dict.fromkeys(self.report['counters'], 0)
        for host in stats.processed:
            recap = stats.summarize(host)
            for key, source in [('ok', 'ok'), ('changed', 'changed'), ('failed', 'failures'), ('unreachable', 'unreachable'), ('skipped', 'skipped')]:
                counters[key] += recap.get(source, 0)
        self.report['counters'] = counters
        self.report['status'] = 'unreachable' if counters['unreachable'] else ('failed' if counters['failed'] else 'succeeded')
        self.emit('recap', 'PLAY RECAP')
