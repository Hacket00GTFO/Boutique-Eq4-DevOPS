"""Read literal application settings without executing a shell or logging values."""
from pathlib import Path
import hashlib
import hmac
from urllib.parse import quote

from common import KEYS, SafeError, validate_values


def integration_values(path):
    try:
        lines = Path(path).expanduser().read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        raise SafeError('The local environment file could not be read.') from None
    values = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, separator, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if name not in KEYS:
            continue
        if not separator or name in values:
            raise SafeError('The local environment file has duplicate integration settings.')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise SafeError('An integration setting has unmatched quotes.')
            value = value[1:-1]
        # Values are literal. Never interpolate environment variables or shell code.
        values[name] = value
    for name, user, password in (
        ('DATABASE_URL', 'boutique_app', 'APP_DB_PASSWORD'),
        ('MIGRATION_DATABASE_URL', 'postgres', 'POSTGRES_PASSWORD'),
    ):
        if name not in values and password in values:
            values[name] = f'postgresql://{user}:{quote(values[password], safe="")}@postgres:5432/boutique'
    return validate_values(values)


def verify_admin(values, users):
    matches = [user for user in users if user.get('email') == values['ADMIN_EMAIL'].strip().lower()]
    if len(matches) != 1 or matches[0].get('role') != 'admin' or not matches[0].get('active'):
        raise SafeError('The recovered active administrator does not match the accepted local environment.')
    try:
        method, salt, expected = matches[0]['password_hash'].split('$')
        if method.startswith('scrypt:'):
            _, n, r, p = method.split(':')
            n, r, p = int(n), int(r), int(p)
            if not (1024 <= n <= 131072 and 1 <= r <= 16 and 1 <= p <= 4):
                raise ValueError()
            actual = hashlib.scrypt(values['ADMIN_PASSWORD'].encode(), salt=salt.encode(),
                                     n=n, r=r, p=p, maxmem=132*n*r*p, dklen=64).hex()
        elif method.startswith('pbkdf2:'):
            _, algorithm, iterations = method.split(':')
            if algorithm not in ('sha256', 'sha512') or not (1000 <= int(iterations) <= 2000000):
                raise ValueError()
            actual = hashlib.pbkdf2_hmac(algorithm, values['ADMIN_PASSWORD'].encode(), salt.encode(), int(iterations)).hex()
        else:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise SafeError('The recovered administrator hash requires application verification.') from None
    if not hmac.compare_digest(actual, expected):
        raise SafeError('The recovered administrator password does not match the local environment.')
