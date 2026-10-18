"""Read-only operator views and authenticated, bounded telemetry ingestion.

Vault tokens, unseal shares and command stdout are never part of this contract.
"""
from datetime import datetime, timedelta, timezone
import re
import uuid
from flask import request
from psycopg.types.json import Jsonb
from auth import admin, require_service
from db import pool

EVENT_TYPES = {'play', 'task', 'ok', 'changed', 'failed', 'unreachable', 'skipped', 'recap'}
COUNTERS = ('ok', 'changed', 'failed', 'unreachable', 'skipped')
SECRET_ASSIGNMENT = re.compile(r'(?i)\b([\w-]*(?:token|password|secret|credential|unseal|private_key)[\w-]*)\s*[:=]\s*\S+')
VAULT_TOKEN = re.compile(r'\b(?:hvs|hvb|hvr)\.[A-Za-z0-9._-]+')
URL_PASSWORD = re.compile(r'\b[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@', re.I)


def safe_label(value, limit=160):
    if not isinstance(value, str):
        raise ValueError('Etiqueta de operación inválida')
    if '{{' in value or '-----BEGIN' in value or '\x1b' in value:
        return '[contenido omitido]'
    value = ''.join(c for c in value if c.isprintable())[:limit]
    value = SECRET_ASSIGNMENT.sub(r'\1=[oculto]', value)
    value = VAULT_TOKEN.sub('[token oculto]', value)
    return URL_PASSWORD.sub('[conexión oculta]@', value)


def integer(value, maximum=1000000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('Contador de operación inválido')
    return value


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed > datetime.now(timezone.utc) + timedelta(minutes=2):
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (AttributeError, TypeError, ValueError):
        raise ValueError('Fecha de operación inválida') from None


def normalize(data):
    if not isinstance(data, dict) or data.get('kind') not in ('vault', 'ansible'):
        raise ValueError('Tipo de reporte inválido')
    try:
        run_id = str(uuid.UUID(str(data['run_id'])))
    except (KeyError, ValueError, TypeError):
        raise ValueError('Identificador de ejecución inválido') from None
    sequence = integer(data.get('sequence'), 9223372036854775807)
    if not sequence:
        raise ValueError('La secuencia debe ser positiva')
    body = data.get('report')
    if not isinstance(body, dict):
        raise ValueError('Reporte inválido')
    if data['kind'] == 'vault':
        reachable = body.get('reachable')
        if type(reachable) is not bool:
            raise ValueError('Estado de conectividad inválido')
        report = {'reachable': reachable}
        for key in ('initialized', 'sealed', 'standby'):
            value = body.get(key)
            if (reachable and type(value) is not bool) or (not reachable and value is not None):
                raise ValueError('Estado de Vault inválido')
            report[key] = value
        version = body.get('version', '')
        if not isinstance(version, str) or not re.fullmatch(r'(?:\d+\.\d+\.\d+(?:[+.-][A-Za-z0-9.-]+)?)?', version):
            raise ValueError('Versión de Vault inválida')
        report['version'] = version
        report['http_status'] = integer(body.get('http_status', 0), 599)
    else:
        if body.get('playbook') not in ('site.yml', 'local-check.yml'):
            raise ValueError('Playbook no registrado')
        if body.get('status') not in ('running', 'succeeded', 'failed', 'unreachable'):
            raise ValueError('Resultado de ejecución inválido')
        events = body.get('events', [])
        if not isinstance(events, list) or len(events) > 400:
            raise ValueError('El reporte admite hasta 400 eventos')
        clean = []
        for event in events:
            if not isinstance(event, dict) or event.get('type') not in EVENT_TYPES:
                raise ValueError('Evento de ejecución inválido')
            clean.append({'type': event['type'], 'label': safe_label(event.get('label', '')),
                          'host': safe_label(event.get('host', ''), 100),
                          'elapsed': integer(event.get('elapsed', 0), 604800)})
        counters = body.get('counters', {})
        if not isinstance(counters, dict):
            raise ValueError('Resumen de ejecución inválido')
        report = {'playbook': body['playbook'], 'status': body['status'],
                  'started_at': timestamp(body.get('started_at')), 'events': clean,
                  'counters': {key: integer(counters.get(key, 0)) for key in COUNTERS},
                  'elapsed': integer(body.get('elapsed', 0), 604800)}
    return data['kind'], run_id, sequence, report


def serialized(row, include_events=True):
    payload = dict(row['payload'])
    if not include_events:
        payload.pop('events', None)
    age = max(0, int((datetime.now(timezone.utc) - row['received_at']).total_seconds()))
    return {'id': str(row['run_id']), 'received_at': row['received_at'].isoformat(),
            'age_seconds': age, 'stale': age > (60 if row['kind'] == 'vault' else 120), **payload}


def register_operations(app):
    @app.post('/api/v1/operations/reports')
    @require_service('ADMIN_API_TOKEN')
    def ingest_report():
        # Machine endpoint: no browser proxy, session cookie or command execution.
        if request.content_length is None or request.content_length > 262144:
            return {'message': 'Reporte demasiado grande'}, 413
        kind, run_id, sequence, report = normalize(request.get_json())
        with pool.connection() as conn:
            row = conn.execute('''INSERT INTO boutique.operations_reports(kind,run_id,sequence,payload)
                VALUES (%s,%s,%s,%s) ON CONFLICT(kind,run_id) DO UPDATE
                SET sequence=EXCLUDED.sequence,payload=EXCLUDED.payload,received_at=now()
                WHERE EXCLUDED.sequence>boutique.operations_reports.sequence
                AND (EXCLUDED.kind='vault' OR boutique.operations_reports.payload->>'status'='running')
                RETURNING run_id''', (kind, run_id, sequence, Jsonb(report))).fetchone()
            conn.execute('''DELETE FROM boutique.operations_reports WHERE kind=%s AND run_id IN
                (SELECT run_id FROM boutique.operations_reports WHERE kind=%s ORDER BY received_at DESC OFFSET 200)''', (kind, kind))
        return {'accepted': bool(row)}, 202

    @app.get('/api/v1/operations/vault')
    @admin(write=True)
    def vault_status():
        with pool.connection() as conn:
            row = conn.execute("SELECT * FROM boutique.operations_reports WHERE kind='vault' ORDER BY received_at DESC LIMIT 1").fetchone()
        return {'snapshot': serialized(row) if row else None}

    @app.get('/api/v1/operations/ansible')
    @admin(write=True)
    def ansible_runs():
        selected_id = request.args.get('run_id')
        if selected_id:
            try:
                selected_id = str(uuid.UUID(selected_id))
            except ValueError:
                raise ValueError('Identificador de ejecución inválido') from None
        with pool.connection() as conn:
            rows = conn.execute("SELECT * FROM boutique.operations_reports WHERE kind='ansible' ORDER BY received_at DESC LIMIT 30").fetchall()
            selected = next((row for row in rows if str(row['run_id']) == selected_id), None) if selected_id else (rows[0] if rows else None)
            if selected_id and not selected:
                selected = conn.execute("SELECT * FROM boutique.operations_reports WHERE kind='ansible' AND run_id=%s", (selected_id,)).fetchone()
                if not selected:
                    return {'message': 'Ejecución no encontrada'}, 404
        return {'runs': [serialized(row, False) for row in rows],
                'selected': serialized(selected) if selected else None}
