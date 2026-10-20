from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_vault_kms import PreparationError
from provision_lab import check_plan, vault_key_in_state


class InfrastructurePlanTests(unittest.TestCase):
    def plan(self, kind, actions, before=None):
        return {'resource_changes': [{'type': kind, 'mode': 'managed',
                                      'change': {'actions': actions, 'before': before or {}}}]}

    def test_replacement_deletion_or_unexpected_iam_change_are_rejected(self):
        for kind, actions in (('aws_eks_cluster', ['delete', 'create']),
                              ('aws_instance', ['delete']), ('aws_iam_role', ['create']),
                              ('aws_eks_node_group', ['update'])):
            with self.subTest(kind=kind, actions=actions), self.assertRaises(PreparationError):
                check_plan(self.plan(kind, actions), 'key')

    def test_only_the_preserved_kms_key_can_be_updated(self):
        self.assertEqual(check_plan(self.plan('aws_kms_key', ['update'], {'arn': 'expected'}), 'expected'), 0)
        with self.assertRaises(PreparationError):
            check_plan(self.plan('aws_kms_key', ['update'], {'arn': 'other'}), 'expected')

    def test_owned_additions_and_no_changes_are_accepted(self):
        self.assertEqual(check_plan(self.plan('aws_eks_cluster', ['create']), 'key'), 1)
        self.assertEqual(check_plan(self.plan('aws_instance', ['no-op']), 'key'), 0)

    def test_fresh_vault_backend_does_not_require_an_existing_state_file(self):
        for response in ({}, {'Contents': [{'Key': 'modulo-vault/terraform.tfstate.tflock'}]}):
            with (self.subTest(response=response), patch('provision_lab.aws', return_value=response),
                  patch('provision_lab.tf') as tf):
                self.assertFalse(vault_key_in_state())
                tf.assert_not_called()

    def test_existing_key_is_recognized_and_denied_state_is_not_treated_as_empty(self):
        with (patch('provision_lab.aws', return_value={'Contents': [{'Key': 'modulo-vault/terraform.tfstate'}]}),
              patch('provision_lab.tf', return_value='aws_kms_key.vault[0]\n')):
            self.assertTrue(vault_key_in_state())
        with (patch('provision_lab.aws', side_effect=PreparationError('Access denied')),
              patch('provision_lab.tf') as tf, self.assertRaises(PreparationError)):
            vault_key_in_state()
        tf.assert_not_called()
