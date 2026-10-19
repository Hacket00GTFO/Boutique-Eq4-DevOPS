"""Shared secret validation and a TLS-only Vault client (standard library)."""
import getpass
import json
import os
from pathlib import Path
import ssl
import urllib.error
import urllib.parse
import urllib.request

DATA_PATH = 'secret/data/boutique/lab/integration'
KEYS = ('DATABASE_URL', 'MIGRATION_DATABASE_URL', 'APP_DB_PASSWORD', 'ADMIN_EMAIL',
        'ADMIN_PASSWORD', 'ADMIN_API_TOKEN', 'CATALOG_READ_TOKEN', 'CHECKOUT_API_TOKEN',
        'POSTGRES_PASSWORD')
DB_KEYS = ('DATABASE_URL', 'MIGRATION_DATABASE_URL', 'APP_DB_PASSWORD', 'POSTGRES_PASSWORD')


class SafeError(Exception):
    """Only fixed, non-sensitive diagnostics may be printed by CLI entrypoints."""


def validate_values(values):
    if not isinstance(values, dict) or any(not isinstance(values.get(k), str) or
                                         not values[k].strip() for k in KEYS):
        raise SafeError('Vault secret is incomplete: all nine integration keys are required.')
    if any('\n' in values[k] or '\r' in values[k] or '\0' in values[k] for k in KEYS):
        raise SafeError('Integration values must be single-line strings.')
    if len(values['ADMIN_PASSWORD']) < 12 or any(len(values[k]) < 24 for k in
                                              ('APP_DB_PASSWORD', 'POSTGRES_PASSWORD',
                                               'ADMIN_API_TOKEN', 'CATALOG_READ_TOKEN', 'CHECKOUT_API_TOKEN')):
        raise SafeError('Passwords and service tokens do not meet minimum lengths.')
    if '@' not in values['ADMIN_EMAIL']:
        raise SafeError('ADMIN_EMAIL is invalid.')
    for key, user, password in [('DATABASE_URL', 'boutique_app', values['APP_DB_PASSWORD']),
                                ('MIGRATION_DATABASE_URL', 'postgres', values['POSTGRES_PASSWORD'])]:
        try:
            parsed = urllib.parse.urlsplit(values[key])
            valid = (parsed.scheme in ('postgresql', 'postgres') and parsed.hostname == 'postgres'
                     and parsed.port in (None, 5432) and parsed.path == '/boutique'
                     and urllib.parse.unquote(parsed.username or '') == user
                     and urllib.parse.unquote(parsed.password or '') == password)
        except ValueError:
            valid = False
        if not valid:
            raise SafeError('Database URLs must match the chart database, users and passwords.')
    return {k: values[k] for k in KEYS}


def private_json(path, value):
    """Exclusive creation on POSIX; never overwrite prior custody material."""
    target = Path(path).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    if target == repo or repo in target.parents:
        raise SafeError('Custody files must be outside the repository.')
    if os.name != 'posix':
        raise SafeError('Create custody material from Linux/WSL to enforce mode 0600.')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = target.parent.stat()
    if parent_stat.st_uid != os.getuid() or parent_stat.st_mode & 0o077:
        raise SafeError('Custody directory must belong to this operator and have mode 0700.')
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(value, stream)
        stream.write('\n')


class Vault:
    def __init__(self, token=None):
        self.url = os.environ.get('VAULT_ADDR', 'https://localhost:18200').rstrip('/')
        if urllib.parse.urlsplit(self.url).scheme != 'https':
            raise SafeError('VAULT_ADDR must use verified HTTPS.')
        ca = os.environ.get('VAULT_CACERT')
        if not ca:
            raise SafeError('Set VAULT_CACERT to the exported public certificate.')
        self.context = ssl.create_default_context(cafile=ca)
        self.token = token

    def authenticate(self):
        self.token = os.environ.get('VAULT_TOKEN') or getpass.getpass('Operator token: ')
        if not self.token:
            raise SafeError('An operator token is required.')
        return self

    def request(self, method, path, payload=None, allowed=(200, 204)):
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['X-Vault-Token'] = self.token
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + '/v1/' + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=self.context, timeout=15) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in allowed:
                return exc.code, {}
            raise SafeError(f'Vault request failed (HTTP {exc.code}); no response body was logged.') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SafeError('Vault is unreachable or TLS verification failed.') from None

    def ready(self):
        _, health = self.request('GET', 'sys/health')
        if not health.get('initialized') or health.get('sealed', True):
            raise SafeError('Vault must be initialized and unsealed.')
