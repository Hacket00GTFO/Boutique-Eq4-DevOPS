"""Read literal application settings without executing a shell or logging values."""
from pathlib import Path
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
