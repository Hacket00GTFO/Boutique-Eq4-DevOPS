import atexit
import os
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

pool = ConnectionPool(os.environ['DATABASE_URL'], min_size=1, open=True,
                      max_size=int(os.getenv('DB_POOL_MAX', '4')), timeout=3,
                      kwargs={'row_factory': dict_row, 'connect_timeout': 3,
                              'options': '-c statement_timeout=5000 -c lock_timeout=3000'})
atexit.register(pool.close)


def audit(conn, actor, action, entity, request_id=''):
    conn.execute('INSERT INTO boutique.audit_log(actor,action,entity,request_id) VALUES (%s,%s,%s,%s)',
                 (actor, action, entity, request_id))
