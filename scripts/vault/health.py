"""Wait for verified HTTPS and an initialized, unsealed Vault through the tunnel."""
import sys
import time
from common import SafeError, Vault


def main():
    vault = Vault()
    for attempt in range(30):
        try:
            vault.ready()
            print('PASS Vault TLS and readiness.')
            return
        except SafeError:
            if attempt == 29:
                raise
            time.sleep(2)


if __name__ == '__main__':
    try:
        main()
    except SafeError as exc:
        print('Vault readiness failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except OSError:
        print('Vault TLS/readiness check failed; deployment was not started.', file=sys.stderr)
        sys.exit(1)
