from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_vault_kms import PreparationError
from provision_lab import check_plan


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
