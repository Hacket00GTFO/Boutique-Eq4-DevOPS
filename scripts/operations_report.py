"""Publish bounded operator telemetry. No Vault token or terminal stdout is collected."""
import argparse
import json
import os
from pathlib import Path
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def publish(payload, timeout=3):
    base = os.environ.get('OPS_REPORT_URL', '').rstrip('/')
    token = os.environ.get('OPS_REPORT_TOKEN', '')
    url = urllib.parse.urlsplit(base)
    if not token or url.username or url.password or url.query or url.fragment:
        raise ValueError('Configure OPS_REPORT_URL and OPS_REPORT_TOKEN privately.')
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', 'catalogservice')):
        raise ValueError('Use verified HTTPS or a local/private service endpoint.')
    context = ssl.create_default_context(cafile=os.environ.get('OPS_REPORT_CA') or None)
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    raw = json.dumps(payload).encode()
    if len(raw) > 262144:
        raise ValueError('Telemetry report exceeds the allowed size.')
    req = urllib.request.Request(base + '/api/v1/operations/reports', data=raw,
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='POST')
    with opener.open(req, timeout=timeout) as response:
        if response.status != 202:
            raise ValueError('Report was not accepted.')


def vault_report():
    addr = os.environ.get('VAULT_ADDR', 'https://localhost:18200').rstrip('/')
    if urllib.parse.urlsplit(addr).scheme != 'https' or not os.environ.get('VAULT_CACERT'):
        raise ValueError('Set VAULT_ADDR (HTTPS) and VAULT_CACERT; never disable certificate verification.')
    context = ssl.create_default_context(cafile=os.environ['VAULT_CACERT'])
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    report = {'reachable': False, 'initialized': None, 'sealed': None, 'standby': None, 'version': '', 'http_status': 0}
    try:
        try:
            response = opener.open(addr + '/v1/sys/health', timeout=4)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 472, 473, 501, 503):
                raise
            response = exc
        with response:
            health = json.loads(response.read(16384))
            if not all(type(health.get(key)) is bool for key in ('initialized', 'sealed', 'standby')):
                raise ValueError('Invalid health response')
            report.update({key: health[key] for key in ('initialized', 'sealed', 'standby')})
            report.update(reachable=True, version=health.get('version', ''), http_status=response.code)
    except (OSError, urllib.error.URLError, ValueError):
        pass  # Explicit unreachable state, never publish response bodies or exception details.
    return {'kind': 'vault', 'run_id': '11111111-1111-4111-8111-111111111111',
            'sequence': time.time_ns() // 1000000, 'report': report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    vault = sub.add_parser('vault', help='Read public Vault health through the verified SSM tunnel')
    vault.add_argument('--watch', action='store_true')
    report = sub.add_parser('publish', help='Publish a structured report written by the Ansible callback')
    report.add_argument('path')
    args = parser.parse_args()
    if args.command == 'publish':
        publish(json.loads(Path(args.path).read_text()))
        print('Execution report published.')
    else:
        while True:
            publish(vault_report())
            print('Vault health report published.', flush=True)
            if not args.watch:
                break
            time.sleep(15)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        print('Could not publish telemetry. Check endpoint, TLS and service credentials; private details suppressed.', file=sys.stderr)
        sys.exit(1)
