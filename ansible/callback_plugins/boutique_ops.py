"""Structured task diagnostics; raw output, arguments and no_log results stay private."""
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
  - Saves task timings, modules, roles, exit codes and classified errors, excluding raw output and arguments.
  - Optionally publishes to the internal reporting endpoint configured by the operator.
requirements:
  - Enable in callbacks_enabled.
'''

# Only a classification leaves the controller. Never forward a matched message,
# stdout, stderr, exception, invocation, diff, loop item or module arguments.
ERROR_PATTERNS = (
    ('tls_verification', r'certificate verify failed|certificate_verify_failed|self.signed certificate|unknown ca'),
    ('checksum_mismatch', r'checksum.*(?:mismatch|did not match)|(?:sha256|digest).*mismatch'),
    ('disk_full', r'no space left on device|disk full'),
    ('authentication', r'authentication failed|accessdenied|invalid credentials|unauthorized'),
    ('permission_denied', r'permission denied|operation not permitted|insufficient permissions'),
    ('connection_refused', r'connection refused|connection reset|failed to connect'),
    ('timeout', r'timed out|timeout|time limit exceeded'),
    ('not_found', r'no such file or directory|command not found|could not find'),
    ('syntax', r'syntax error|invalid syntax|template error'),
)


def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}', value) else ''


def diagnostic(result, kind, action):
    if kind not in ('failed', 'unreachable', 'retry'):
        return 'condition_false' if kind == 'skipped' else ''
    text = '\n'.join(value[:16384] for key in ('msg', 'stderr', 'exception')
                     if isinstance(value := result.get(key), str)).lower()
    for code, pattern in ERROR_PATTERNS:
        if re.search(pattern, text):
            return code
    if action.rsplit('.', 1)[-1] == 'assert':
        return 'assertion_failed'
    return 'unreachable' if kind == 'unreachable' else 'command_failed' if result.get('rc') else 'module_failed'


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
        self.task_starts = {}
        self.host_starts = {}
        self.report = {'playbook': 'site.yml', 'status': 'running',
                       'started_at': datetime.now(timezone.utc).isoformat(),
                       'events': [], 'elapsed': 0, 'counters': {k: 0 for k in ('ok', 'changed', 'failed', 'unreachable', 'skipped')}}

    def emit(self, kind, text='', host='', **details):
        if not self.enabled:
            return
        self.sequence += 1
        self.report['elapsed'] = min(604800, max(0, int(time.monotonic() - self.started)))
        self.report['events'].append({'type': kind, 'label': label(text), 'host': label(host)[:100],
                                     'elapsed': self.report['elapsed'],
                                     'timestamp': datetime.now(timezone.utc).isoformat(), **details})
        self.report['events'] = self.report['events'][-400:]
        envelope = {'kind': 'ansible', 'run_id': self.run_id, 'sequence': self.sequence, 'report': self.report}
        while len(json.dumps(envelope).encode()) > 240000 and len(self.report['events']) > 1:
            self.report['events'].pop(0)
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
        task_id = self.task_id(task)
        self.task_starts[task_id] = time.monotonic()
        self.emit('task', 'Tarea protegida (no_log)' if task.no_log else (task.name or task.action),
                  **self.metadata(task))

    def v2_playbook_on_handler_task_start(self, task):
        self.v2_playbook_on_task_start(task, False)

    @staticmethod
    def task_id(task):
        value = str(getattr(task, '_uuid', ''))
        try:
            return str(uuid.UUID(value))
        except ValueError:
            return ''

    def metadata(self, task):
        result = {'task_id': self.task_id(task), 'protected': bool(task.no_log)}
        if not task.no_log:
            role = getattr(task, '_role', None)
            result['module'] = identifier(task.action)
            result['role'] = identifier(role.get_name()) if role else ''
        return result

    def v2_runner_on_start(self, host, task):
        self.host_starts[(self.task_id(task), host.get_name())] = time.monotonic()

    def result(self, result, kind, ignore_errors=False):
        protected = result._task.no_log or result._result.get('_ansible_no_log', False)
        task_id = self.task_id(result._task)
        host = result._host.get_name()
        start = self.host_starts.get((task_id, host), self.task_starts.get(task_id))
        details = {'task_id': task_id, 'protected': bool(protected)}
        if start is not None:
            details['duration_ms'] = min(604800000, max(0, int((time.monotonic() - start) * 1000)))
        if not protected:
            details.update(self.metadata(result._task))
            details['diagnostic'] = diagnostic(result._result, kind, result._task.action)
            details['ignored'] = bool(ignore_errors)
            for key, maximum in (('rc', 65535), ('attempts', 10000)):
                value = result._result.get(key)
                if type(value) is int and (-65535 if key == 'rc' else 0) <= value <= maximum:
                    details[key] = value
            items = result._result.get('results')
            if isinstance(items, list):
                details['items_total'] = min(len(items), 1000000)
                details['items_failed'] = min(sum(isinstance(item, dict) and bool(item.get('failed')) for item in items), 1000000)
        if kind in ('ok', 'changed'):
            self.report['counters']['ok'] += 1
        if kind == 'changed':
            self.report['counters']['changed'] += 1
        elif kind in ('failed', 'unreachable', 'skipped') and not ignore_errors:
            self.report['counters'][kind] += 1
        self.emit(kind, 'Resultado protegido (no_log)' if protected else (result._task.name or result._task.action), host, **details)

    def v2_runner_on_ok(self, result):
        self.result(result, 'changed' if result.is_changed() else 'ok')

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self.result(result, 'failed', ignore_errors)

    def v2_runner_retry(self, result):
        self.result(result, 'retry')

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
