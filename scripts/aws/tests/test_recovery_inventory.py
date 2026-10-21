import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recovery_inventory import finish, query, kubernetes_inventory


class RecoveryInventoryTests(unittest.TestCase):
    def test_canceled_session_diagnostic_never_forwards_private_aws_body(self):
        result = subprocess.CompletedProcess([], 1, '',
            'An error occurred (AccessDeniedException): private-request-body voc-cancel-cred')
        with patch('recovery_inventory.subprocess.run', return_value=result):
            response = query('eks', 'list-clusters')
        self.assertEqual(response, {'unavailable': True, 'error_code': 'AccessDeniedException', 'session_cancelled': True})

    def test_unavailable_inventory_fails_instead_of_reporting_a_green_run(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(SystemExit, 'incomplete'):
            finish({'eks': {'unavailable': True}})

    def test_empty_accessible_cluster_list_is_a_valid_inventory(self):
        with contextlib.redirect_stdout(io.StringIO()):
            finish({'eks': []})

    def test_kubernetes_probe_does_not_print_annotations_tokens_or_private_errors(self):
        nodes = {'items': [{'metadata': {'name': 'node', 'annotations': {'private': 'private-marker'}},
                            'status': {'conditions': [{'type': 'Ready', 'status': 'False', 'reason': 'KubeletNotReady'}]}}]}
        responses = [subprocess.CompletedProcess([], 0, 'public context', ''),
                     subprocess.CompletedProcess([], 0, json.dumps(nodes), ''),
                     subprocess.CompletedProcess([], 1, '', 'Forbidden private-marker')]
        with patch('recovery_inventory.subprocess.run', side_effect=responses):
            result = kubernetes_inventory('boutique-eq4-eks')
        self.assertEqual(result['nodes'][0]['ready'], 'False')
        self.assertEqual(result['system_pods']['error_code'], 'KubernetesForbidden')
        self.assertNotIn('private-marker', json.dumps(result))
