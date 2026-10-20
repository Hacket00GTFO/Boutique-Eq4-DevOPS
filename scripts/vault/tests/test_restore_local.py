import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import seed_values
from common import SafeError
import restore_local


class LocalRestoreTests(unittest.TestCase):
    def test_corrupt_or_foreign_backup_is_rejected_before_cluster_writes(self):
        dump = b'PGDMPtest'
        data = {'version': 1, 'repo': 'Hacket00GTFO/Boutique-Eq4-DevOPS', 'values': seed_values(),
                'database_dump_b64': base64.b64encode(dump).decode(), 'dump_sha256': hashlib.sha256(dump).hexdigest()}
        with tempfile.TemporaryDirectory() as directory, patch.object(restore_local, 'DIRECTORY', Path(directory)):
            for change in ({'dump_sha256': 'incorrect'}, {'repo': 'other/project'}):
                (Path(directory) / 'local-payload.json').write_text(json.dumps(dict(data, **change)))
                with self.subTest(kind=list(change)), self.assertRaises(SafeError):
                    restore_local.payload()

    def test_vault_values_must_match_the_accepted_local_settings_before_writes(self):
        accepted, other = seed_values(), seed_values()
        with (patch.object(restore_local, 'payload', return_value=({'values': accepted}, b'dump')),
              patch.dict('os.environ', other), patch.object(restore_local, 'run') as command,
              self.assertRaises(SafeError)):
            restore_local.restore()
        command.assert_not_called()

    def test_changed_table_counts_and_unknown_table_names_cannot_verify_recovery(self):
        for expected, replies in (({'boutique.products': 9}, ['["boutique.products"]', '8']),
                                   ({'boutique.products;DROP': 9}, ['["boutique.products;DROP"]'])):
            with (self.subTest(kind='invalid database'), patch.object(restore_local, 'sql', side_effect=replies),
                  self.assertRaises(SafeError)):
                restore_local.verify_database(expected, seed_values())
