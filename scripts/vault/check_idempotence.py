"""Require a successful second Ansible recap with zero changes on every host."""
import argparse
from pathlib import Path
import re
import sys


def check(text):
    recaps = re.findall(r'^\S+\s+:\s+ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)', text, re.MULTILINE)
    return bool(recaps) and all(int(ok) > 0 and (changed, unreachable, failed) == ('0', '0', '0')
                                for ok, changed, unreachable, failed in recaps)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recap')
    args = parser.parse_args()
    if not check(Path(args.recap).read_text()):
        print('FAIL second Ansible execution changed state, failed, or had no host recap.', file=sys.stderr)
        sys.exit(1)
    print('PASS repeated configuration is idempotent.')
