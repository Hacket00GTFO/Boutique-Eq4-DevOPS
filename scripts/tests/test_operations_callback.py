"""Exercise the actual aggregate callback contract without EC2 or credentials."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('boutique_ops_test', ROOT / 'ansible/callback_plugins/boutique_ops.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CallbackTests(unittest.TestCase):
    def test_callback_never_copies_results_args_or_no_log_names(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'OPS_REPORT_DIR': directory, 'OPS_REPORT_URL': ''}):
            callback = module.CallbackModule()
            callback.v2_playbook_on_start(Mock(_file_name='site.yml'))
            task = Mock(name='RAW_PRIVATE_TASK_NAME', no_log=True, action='debug')
            task.name = 'RAW_PRIVATE_TASK_NAME'
            callback.v2_playbook_on_task_start(task, False)
            result = Mock(_task=task, _result={'stdout': 'RAW_PRIVATE_OUTPUT', 'msg': 'RAW_PRIVATE_OUTPUT', '_ansible_no_log': True})
            result.is_changed.return_value = False
            result._host.get_name.return_value = 'localhost'
            callback.v2_runner_on_ok(result)
            record = json.loads((Path(directory) / (callback.run_id + '.json')).read_text())
            raw = json.dumps(record)
            self.assertNotIn('RAW_PRIVATE', raw)
            self.assertEqual([event['type'] for event in record['report']['events']], ['task', 'ok'])

    def test_callback_keeps_bounded_history_and_real_recap(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'OPS_REPORT_DIR': directory, 'OPS_REPORT_URL': ''}):
            callback = module.CallbackModule()
            callback.v2_playbook_on_start(Mock(_file_name='local-check.yml'))
            for _ in range(405):
                callback.emit('ok', 'Check controller', 'localhost')
            stats = Mock(processed={'localhost': 1})
            stats.summarize.return_value = {'ok': 3, 'changed': 0, 'failures': 0, 'unreachable': 0, 'skipped': 0}
            callback.v2_playbook_on_stats(stats)
            self.assertEqual(len(callback.report['events']), 400)
            self.assertEqual(callback.report['status'], 'succeeded')
            self.assertEqual(callback.report['counters']['ok'], 3)

    def test_dynamic_names_and_known_credentials_are_suppressed(self):
        self.assertEqual(module.label('{{ password }}'), '[contenido omitido]')
        self.assertNotIn('PRIVATE', module.label('password=PRIVATE hvs.PRIVATE postgresql://user:PRIVATE@host/db'))

    def test_failure_records_host_duration_and_classification_without_raw_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'OPS_REPORT_DIR': directory, 'OPS_REPORT_URL': ''}):
            callback = module.CallbackModule()
            callback.v2_playbook_on_start(Mock(_file_name='site.yml'))
            task = Mock(no_log=False, action='ansible.builtin.command', _uuid=str(uuid.uuid4()), _role=None)
            task.name = 'Verify service'
            host = Mock()
            host.get_name.return_value = 'vault-host'
            with patch.object(module.time, 'monotonic', return_value=10):
                callback.v2_playbook_on_task_start(task, False)
            with patch.object(module.time, 'monotonic', return_value=12):
                callback.v2_runner_on_start(host, task)
            result = Mock(_task=task, _host=host, _result={'rc': 13, 'attempts': 2,
                          'msg': 'Permission denied SENTINEL_PRIVATE_VALUE', 'stderr': 'SENTINEL_PRIVATE_VALUE',
                          'invocation': {'module_args': 'SENTINEL_PRIVATE_VALUE'}})
            with patch.object(module.time, 'monotonic', return_value=14.5):
                callback.v2_runner_on_failed(result, ignore_errors=True)
            event = callback.report['events'][-1]
            self.assertEqual(event['duration_ms'], 2500)
            self.assertEqual(event['module'], 'ansible.builtin.command')
            self.assertEqual(event['rc'], 13)
            self.assertEqual(event['diagnostic'], 'permission_denied')
            self.assertTrue(event['ignored'])
            self.assertEqual(callback.report['counters']['failed'], 0)
            self.assertNotIn('SENTINEL_PRIVATE_VALUE', json.dumps(callback.report))

    def test_result_no_log_suppresses_diagnostics_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'OPS_REPORT_DIR': directory, 'OPS_REPORT_URL': ''}):
            callback = module.CallbackModule()
            callback.v2_playbook_on_start(Mock(_file_name='site.yml'))
            task = Mock(no_log=False, action='ansible.builtin.command', _uuid=str(uuid.uuid4()), _role=None)
            task.name = 'Safe task'
            result = Mock(_task=task, _result={'_ansible_no_log': True, 'rc': 42, 'attempts': 3, 'msg': 'Permission denied PRIVATE'})
            result._host.get_name.return_value = 'localhost'
            callback.v2_runner_on_failed(result)
            event = callback.report['events'][-1]
            self.assertTrue(event['protected'])
            for field in ('rc', 'attempts', 'diagnostic', 'module', 'role'):
                self.assertNotIn(field, event)

    def test_retry_and_loop_metadata_do_not_capture_loop_values(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'OPS_REPORT_DIR': directory, 'OPS_REPORT_URL': ''}):
            callback = module.CallbackModule()
            callback.v2_playbook_on_start(Mock(_file_name='site.yml'))
            task = Mock(no_log=False, action='ansible.builtin.uri', _uuid=str(uuid.uuid4()))
            task.name = 'Wait for service'
            task._role.get_name.return_value = 'vault_service'
            result = Mock(_task=task, _result={'attempts': 2, 'msg': 'Connection refused PRIVATE',
                          'results': [{'failed': True, 'item': 'PRIVATE'}, {'item': 'PRIVATE'}]})
            result._host.get_name.return_value = 'localhost'
            callback.v2_runner_retry(result)
            event = callback.report['events'][-1]
            self.assertEqual(event['type'], 'retry')
            self.assertEqual(event['role'], 'vault_service')
            self.assertEqual(event['items_total'], 2)
            self.assertEqual(event['items_failed'], 1)
            self.assertEqual(event['diagnostic'], 'connection_refused')
            self.assertEqual(callback.report['counters']['failed'], 0)
            self.assertNotIn('PRIVATE', json.dumps(callback.report))

    def test_error_families_are_classified_without_exporting_messages(self):
        for message, expected in [('certificate verify failed', 'tls_verification'),
                                  ('Checksum did not match', 'checksum_mismatch'),
                                  ('No space left on device', 'disk_full'),
                                  ('Operation timed out', 'timeout'),
                                  ('No such file or directory', 'not_found')]:
            with self.subTest(message=message):
                self.assertEqual(module.diagnostic({'msg': message}, 'failed', 'command'), expected)

    def test_byte_limit_accounts_for_expanded_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'OPS_REPORT_DIR': directory, 'OPS_REPORT_URL': ''}):
            callback = module.CallbackModule()
            callback.v2_playbook_on_start(Mock(_file_name='site.yml'))
            for _ in range(400):
                callback.emit('failed', 'á' * 160, 'á' * 100, module='ansible.builtin.command', role='vault_service', duration_ms=1000)
            record = (Path(directory) / (callback.run_id + '.json')).read_bytes()
            self.assertLessEqual(len(record), 240000)
            self.assertLess(len(callback.report['events']), 400)


if __name__ == '__main__':
    unittest.main()
