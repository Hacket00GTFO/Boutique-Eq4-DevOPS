import hashlib
import hmac
import os
import secrets
from functools import wraps
from flask import g, request
from werkzeug.security import check_password_hash, generate_password_hash
from db import pool

DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def require_service(env):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
            if not supplied or not hmac.compare_digest(supplied, os.environ[env]):
                return {'code': 'unauthorized', 'message': 'Servicio no autorizado'}, 401
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def admin(write=False):
    def decorator(fn):
        @wraps(fn)
        @require_service('ADMIN_API_TOKEN')
        def wrapper(*args, **kwargs):
            with pool.connection() as conn:
                user = conn.execute('''SELECT u.email,u.role FROM boutique.sessions s JOIN boutique.admin_users u ON u.id=s.user_id
                    WHERE s.token_hash=%s AND s.expires_at>now() AND u.active''',
                    (token_hash(request.headers.get('X-Admin-Session', '')),)).fetchone()
            if not user:
                return {'code': 'unauthenticated', 'message': 'Sesión expirada'}, 401
            if write and user['role'] != 'admin':
                return {'code': 'forbidden', 'message': 'Permiso de administrador requerido'}, 403
            g.admin = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def login(email, password):
    email = email.strip().lower()[:254]
    with pool.connection() as conn:
        conn.execute('INSERT INTO boutique.login_attempts(email) VALUES (%s) ON CONFLICT DO NOTHING', (email,))
        attempt = conn.execute("SELECT *,window_start>now()-interval '15 minutes' AS recent FROM boutique.login_attempts WHERE email=%s FOR UPDATE", (email,)).fetchone()
        if attempt['recent'] and attempt['failures'] >= 10:
            return {'code': 'rate_limited', 'message': 'Demasiados intentos. Espera 15 minutos.'}, 429
        user = conn.execute('SELECT * FROM boutique.admin_users WHERE email=%s AND active', (email,)).fetchone()
        if not check_password_hash(user['password_hash'] if user else DUMMY_HASH, password) or not user:
            conn.execute("UPDATE boutique.login_attempts SET failures=CASE WHEN window_start>now()-interval '15 minutes' THEN failures+1 ELSE 1 END, window_start=CASE WHEN window_start>now()-interval '15 minutes' THEN window_start ELSE now() END WHERE email=%s", (email,))
            return {'code': 'invalid_credentials', 'message': 'Credenciales inválidas'}, 401
        token = secrets.token_urlsafe(32)
        conn.execute('DELETE FROM boutique.sessions WHERE expires_at<now()')
        conn.execute("INSERT INTO boutique.sessions(token_hash,user_id,expires_at) VALUES (%s,%s,now()+interval '24 hours')", (token_hash(token), user['id']))
        conn.execute('DELETE FROM boutique.login_attempts WHERE email=%s', (email,))
        return {'token': token, 'email': user['email'], 'role': user['role']}, 200
