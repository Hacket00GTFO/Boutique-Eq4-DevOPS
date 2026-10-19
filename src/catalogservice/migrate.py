"""Versioned migration/seed job, separate from web workers."""
import json
import os
import uuid
from pathlib import Path
import psycopg
from psycopg import sql
from werkzeug.security import generate_password_hash

with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=5) as conn:
    conn.execute('SELECT pg_advisory_xact_lock(482401)')
    conn.execute('CREATE TABLE IF NOT EXISTS public.boutique_migrations(version text PRIMARY KEY, applied_at timestamptz DEFAULT now())')
    for migration in sorted(Path('migrations').glob('*.sql')):
        if not conn.execute('SELECT 1 FROM public.boutique_migrations WHERE version=%s', (migration.name,)).fetchone():
            conn.execute(migration.read_text())
            conn.execute('INSERT INTO public.boutique_migrations(version) VALUES (%s)', (migration.name,))
    password = os.environ['ADMIN_PASSWORD']
    if len(password) < 12:
        raise ValueError('ADMIN_PASSWORD must have at least 12 characters')
    conn.execute('INSERT INTO boutique.admin_users(id,email,password_hash,role) VALUES (%s,%s,%s,%s) ON CONFLICT(email) DO NOTHING',
                 (uuid.uuid4(), os.getenv('ADMIN_EMAIL', 'admin@boutique.com').strip().lower(), generate_password_hash(password), 'admin'))
    if os.getenv('SEED_CATALOG') == 'true':
        for p in json.loads(Path('/seed/products.json').read_text())['products']:
            price = p['priceUsd']
            conn.execute('''INSERT INTO boutique.products(id,name,description,picture,categories,price_units,price_nanos,sku,stock)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,100) ON CONFLICT(id) DO NOTHING''',
                         (p['id'], p['name'], p['description'], p['picture'], p['categories'], int(price['units']), int(price.get('nanos', 0)), p['id']))
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='boutique_app'").fetchone():
        conn.execute(sql.SQL('CREATE ROLE boutique_app LOGIN PASSWORD {}').format(sql.Literal(os.environ['APP_DB_PASSWORD'])))
    conn.execute('GRANT USAGE ON SCHEMA boutique TO boutique_app')
    conn.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA boutique TO boutique_app')
    conn.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA boutique TO boutique_app')
print('Migrations applied; bootstrap admin ready.')
