import uuid
import psycopg
from flask import Flask, g, request
from werkzeug.exceptions import HTTPException
from psycopg_pool import PoolTimeout
from auth import admin, login, require_service, token_hash
from db import pool

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024


@app.before_request
def request_id():
    g.request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))[:128]


@app.after_request
def response_headers(response):
    response.headers['X-Request-ID'] = g.request_id
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.errorhandler(Exception)
def errors(exc):
    if isinstance(exc, HTTPException):
        status, code, message = exc.code, 'invalid_request', exc.description
    elif isinstance(exc, (psycopg.OperationalError, PoolTimeout)):
        status, code, message = 503, 'dependency_unavailable', 'Base de datos temporalmente no disponible'
    elif isinstance(exc, (psycopg.IntegrityError, psycopg.errors.LockNotAvailable)):
        status, code, message = 409, 'conflict', 'Conflicto de datos o stock'
    elif isinstance(exc, ValueError):
        status, code, message = 422, 'validation', str(exc)
    else:
        app.logger.exception('Request failed id=%s', g.request_id)
        status, code, message = 500, 'internal', 'No se pudo completar la operación'
    return {'code': code, 'message': message, 'request_id': g.request_id}, status


@app.get('/healthz')
def healthz():
    return {'status': 'alive'}


@app.get('/readyz')
def readyz():
    with pool.connection() as conn:
        conn.execute('SELECT id FROM boutique.products LIMIT 1')
        conn.execute('SELECT id FROM boutique.orders LIMIT 1')
    return {'status': 'ready'}


@app.post('/api/v1/auth/login')
@require_service('ADMIN_API_TOKEN')
def auth_login():
    data = request.get_json()
    return login(str(data.get('email', '')), str(data.get('password', '')))


@app.get('/api/v1/auth/me')
@admin()
def auth_me():
    return g.admin


@app.post('/api/v1/auth/logout')
@admin()
def auth_logout():
    with pool.connection() as conn:
        conn.execute('DELETE FROM boutique.sessions WHERE token_hash=%s', (token_hash(request.headers.get('X-Admin-Session', '')),))
    return {}, 200


from catalog import register_catalog
from orders import register_orders
from reports import register_reports
register_catalog(app)
register_orders(app)
register_reports(app)
