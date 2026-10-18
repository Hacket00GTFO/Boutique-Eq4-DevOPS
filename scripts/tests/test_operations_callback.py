"""Exercise the actual aggregate callback contract without EC2 or credentials."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
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


if __name__ == '__main__':
    unittest.main()
