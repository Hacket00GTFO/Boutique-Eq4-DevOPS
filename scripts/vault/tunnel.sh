#!/usr/bin/env bash
set -euo pipefail
umask 077
state="${RUNNER_TEMP:?}/vault-tunnel"
case "${1:-}" in
  start)
    test -s "${VAULT_CACERT:?}"
    python -u scripts/vault/ssm_tunnel.py --instance-id "${VAULT_INSTANCE_ID:?}" \
      --pid-file "$state.pid" --ready-file "$state.ready" > "$state.log" 2>&1 &
    supervisor=$!
    if ! python scripts/vault/require_automation.py runtime --tunnel-pid "$supervisor" \
         --ready-file "$state.ready"; then
      # The supervisor logs only fixed diagnostics, never raw AWS process output.
      cat "$state.log"
      exit 1
    fi
    ;;
  stop)
    python scripts/vault/ssm_tunnel.py --stop --pid-file "$state.pid"
    rm -f -- "$state.log" "${VAULT_CACERT:?}"
    ;;
  *) echo 'Expected start or stop.' >&2; exit 1 ;;
esac
