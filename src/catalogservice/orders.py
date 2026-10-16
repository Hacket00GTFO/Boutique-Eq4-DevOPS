import uuid
from decimal import Decimal
from flask import abort, g, request
from psycopg.types.json import Jsonb
from auth import admin, require_service
from catalog import money, page_info, pagination
from db import audit, pool
from shipments import shipment_event, update_shipment


def nanos(value):
    units, fraction = int(value.get('units', 0)), int(value.get('nanos', 0))
    if units < 0 or not 0 <= fraction < 1000000000:
        raise ValueError('Importe inválido')
    return units * 1000000000 + fraction


def record(row):
    return {'id': str(row['id']), 'state': row['state'], 'result': row['payload'],
            'transaction_id': row['transaction_id'], 'email': row['email']}


def order_view(row):
    return {'ID': str(row['id']), 'Date': row['created_at'].isoformat(),
            'Customer': row['email'], 'Total': money(row['total_nanos'], row['currency']),
            'Status': 'CANCELADO' if row['state'] in ('cancelled', 'failed') else row['fulfillment'],
            'State': row['state'], 'PaymentMethod': row['payment_method'], 'PaymentIcon': 'credit_card',
            'Result': row['payload'], 'TransactionID': row['transaction_id'],
            'Demo': row['demo'], 'CustomerName': row['customer_name'],
            'ShippingCost': money(nanos(row['payload'].get('shippingCost', {})), row['currency']),
            'Shipment': {'Version': row['shipment_version'], 'Carrier': row['shipping_carrier'],
                         'TrackingID': row['payload'].get('shippingTrackingId', ''),
                         'Notes': row['shipping_notes'],
                         'EstimatedDelivery': row['estimated_delivery'].isoformat() if row['estimated_delivery'] else ''}}


def orders_page(all_rows=False):
    page, size = pagination()
    clauses, params = ['true'], []
    for field, operator in [('from', '>='), ('to', '<')]:
        if request.args.get(field):
            # Exclusive end date is the next day; invalid dates return 422.
            from datetime import date
            try:
                bound = date.fromisoformat(request.args[field])
            except ValueError:
                raise ValueError('Fecha inválida; usa YYYY-MM-DD')
            clauses.append('created_at ' + operator + (' %s::date' if field == 'from' else " %s::date+interval '1 day'"))
            params.append(bound)
    q = request.args.get('q', '')[:128]
    if q:
        clauses.append('(email ILIKE %s OR customer_name ILIKE %s OR id::text ILIKE %s)')
        params.extend(['%' + q + '%'] * 3)
    state = request.args.get('status', '')
    if state:
        clauses.append("(CASE WHEN state IN ('cancelled','failed') THEN 'CANCELADO' ELSE fulfillment END)=%s")
        params.append(state)
    where = ' AND '.join(clauses)
    with pool.connection() as conn:
        total = conn.execute('SELECT count(*) AS n FROM boutique.orders WHERE ' + where, params).fetchone()['n']
        rows = conn.execute('SELECT * FROM boutique.orders WHERE ' + where + ' ORDER BY created_at DESC,id' +
                            (' LIMIT 10001' if all_rows else ' LIMIT %s OFFSET %s'), params + ([] if all_rows else [size, (page - 1) * size])).fetchall()
    return dict(page_info(total, page, size), Orders=[order_view(r) for r in rows],
                DateRange=request.args.get('from', '') + ' / ' + request.args.get('to', ''))


def release(conn, order):
    items = conn.execute('SELECT * FROM boutique.order_items WHERE order_id=%s ORDER BY product_id', (order['id'],)).fetchall()
    for item in items:
        conn.execute('UPDATE boutique.products SET reserved=reserved-%s WHERE id=%s', (item['quantity'], item['product_id']))


def expire_unclaimed(conn):
    for order in conn.execute("SELECT * FROM boutique.orders WHERE state='reserved' AND created_at<now()-interval '15 minutes' FOR UPDATE SKIP LOCKED").fetchall():
        release(conn, order)
        conn.execute("UPDATE boutique.orders SET state='cancelled',updated_at=now() WHERE id=%s", (order['id'],))


def register_orders(app):
    @app.get('/internal/orders/by-key')
    @require_service('CHECKOUT_API_TOKEN')
    def by_key():
        with pool.connection() as conn:
            row = conn.execute('SELECT * FROM boutique.orders WHERE user_id=%s AND idempotency_key=%s', (request.args.get('user_id'), request.args.get('key'))).fetchone()
        if not row:
            abort(404)
        return record(row)

    @app.post('/internal/orders')
    @require_service('CHECKOUT_API_TOKEN')
    def prepare():
        data = request.get_json()
        items = data.get('items', [])
        if not items or len(items) > 100 or len(data.get('key', '')) > 128 or not data.get('key'):
            raise ValueError('Carrito vacío o llave de compra inválida')
        if data['shipping_cost']['currencyCode'] != data['currency'] or data['shipping_cost_usd']['currencyCode'] != 'USD':
            raise ValueError('Moneda de envío inválida')
        if len({i['item']['productId'] for i in items}) != len(items):
            raise ValueError('Productos duplicados')
        with pool.connection() as conn:
            # Same key serialized across workers; no external RPC under this lock.
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (data['user_id'] + ':' + data['key'],))
            existing = conn.execute('SELECT * FROM boutique.orders WHERE user_id=%s AND idempotency_key=%s', (data['user_id'], data['key'])).fetchone()
            if existing:
                if existing['email'] != data['email'] or existing['currency'] != data['currency'] or existing['payload']['shippingAddress'] != data['address']:
                    abort(409, 'La llave ya corresponde a otra solicitud')
                return record(existing)
            expire_unclaimed(conn)
            order_id = uuid.uuid4()
            total = nanos(data['shipping_cost'])
            usd_total = nanos(data['shipping_cost_usd'])
            snapshots = []
            for item in sorted(items, key=lambda i: i['item']['productId']):
                id, quantity = item['item']['productId'], int(item['item']['quantity'])
                if not 1 <= quantity <= 10 or item['cost']['currencyCode'] != data['currency']:
                    raise ValueError('Cantidad o moneda inválida')
                p = conn.execute('SELECT * FROM boutique.products WHERE id=%s FOR UPDATE', (id,)).fetchone()
                if not p or not p['active'] or p['stock'] - p['reserved'] < quantity:
                    abort(409, 'Producto sin stock disponible')
                # Catalog may have been edited after checkout fetched prices.
                if nanos(item['price_usd']) != p['price_units'] * 1000000000 + p['price_nanos']:
                    abort(409, 'Precio cambiado; vuelve a cargar el carrito')
                total += nanos(item['cost']) * quantity
                usd_total += nanos(item['price_usd']) * quantity
                conn.execute('UPDATE boutique.products SET reserved=reserved+%s WHERE id=%s', (quantity, id))
                snapshots.append((id, p['name'], p['sku'], quantity, nanos(item['cost'])))
            payload = {'orderId': str(order_id), 'shippingCost': data['shipping_cost'],
                       'shippingAddress': data['address'], 'items': [{'item': i['item'], 'cost': i['cost']} for i in items]}
            row = conn.execute('''INSERT INTO boutique.orders(id,user_id,idempotency_key,email,currency,total_nanos,total_usd_nanos,state,payload,payment_method)
                              VALUES (%s,%s,%s,%s,%s,%s,%s,'reserved',%s,%s) RETURNING *''',
                              (order_id, data['user_id'], data['key'], data['email'], data['currency'], total, usd_total,
                               Jsonb(payload), 'Tarjeta •••• ' + data['last_four'][-4:])).fetchone()
            for snapshot in snapshots:
                conn.execute('INSERT INTO boutique.order_items(order_id,product_id,name,sku,quantity,unit_nanos) VALUES (%s,%s,%s,%s,%s,%s)', (order_id,) + snapshot)
            audit(conn, data['user_id'], 'order.reserve', str(order_id), g.request_id)
        return record(row), 201

    @app.post('/internal/orders/<uuid:id>/claim')
    @require_service('CHECKOUT_API_TOKEN')
    def claim(id):
        with pool.connection() as conn:
            row = conn.execute("UPDATE boutique.orders SET state='charging',updated_at=now() WHERE id=%s AND state='reserved' RETURNING *", (id,)).fetchone()
            if not row:
                abort(409, 'Compra en curso; no repitas el cobro')
        return record(row)

    @app.post('/internal/orders/<uuid:id>/payment')
    @require_service('CHECKOUT_API_TOKEN')
    def payment(id):
        data = request.get_json()
        target = data['state']
        if target not in ('paid', 'failed', 'payment_unknown'):
            raise ValueError('Estado de pago inválido')
        with pool.connection() as conn:
            row = conn.execute('SELECT * FROM boutique.orders WHERE id=%s FOR UPDATE', (id,)).fetchone()
            if not row:
                abort(404)
            if row['state'] == target:
                return record(row)
            if row['state'] not in ('charging', 'payment_unknown'):
                abort(409, 'Transición de pago inválida')
            if target == 'failed':
                release(conn, row)
            tx = str(data.get('transaction_id', ''))[:128]
            if target == 'paid' and not tx:
                raise ValueError('Referencia de pago requerida')
            row = conn.execute('UPDATE boutique.orders SET state=%s,transaction_id=%s,updated_at=now() WHERE id=%s RETURNING *', (target, tx, id)).fetchone()
        return record(row)

    @app.post('/internal/orders/<uuid:id>/confirm')
    @require_service('CHECKOUT_API_TOKEN')
    def confirm(id):
        data = request.get_json()
        with pool.connection() as conn:
            row = conn.execute('SELECT * FROM boutique.orders WHERE id=%s FOR UPDATE', (id,)).fetchone()
            if not row:
                abort(404)
            if row['state'] == 'confirmed':
                return record(row)
            if row['state'] != 'paid' or not data.get('tracking_id'):
                abort(409, 'Pago o tracking pendientes')
            items = conn.execute('SELECT * FROM boutique.order_items WHERE order_id=%s ORDER BY product_id', (id,)).fetchall()
            for item in items:
                conn.execute('UPDATE boutique.products SET stock=stock-%s,reserved=reserved-%s WHERE id=%s', (item['quantity'], item['quantity'], item['product_id']))
                conn.execute('INSERT INTO boutique.stock_movements(product_id,delta,reason,actor) VALUES (%s,%s,%s,%s)', (item['product_id'], -item['quantity'], 'Compra ' + str(id), row['user_id']))
            payload = dict(row['payload'], shippingTrackingId=str(data['tracking_id'])[:128])
            row = conn.execute("UPDATE boutique.orders SET state='confirmed',fulfillment='ENVIADO',shipping_carrier='Envío demo',payload=%s,shipment_version=shipment_version+1,updated_at=now() WHERE id=%s RETURNING *", (Jsonb(payload), id)).fetchone()
            shipment_event(conn, row, row['user_id'], 'Envío generado por checkout')
            audit(conn, row['user_id'], 'order.confirm', str(id), g.request_id)
        return record(row)

    @app.get('/internal/email-jobs')
    @require_service('CHECKOUT_API_TOKEN')
    def email_jobs():
        with pool.connection() as conn:
            expire_unclaimed(conn)
            rows = conn.execute("""WITH claimed AS (SELECT id FROM boutique.orders WHERE state='confirmed' AND NOT email_sent
                       AND (email_lease_until IS NULL OR email_lease_until<now()) ORDER BY created_at LIMIT 10 FOR UPDATE SKIP LOCKED)
                       UPDATE boutique.orders o SET email_lease_until=now()+interval '60 seconds' FROM claimed WHERE o.id=claimed.id RETURNING o.*""").fetchall()
        return {'jobs': [record(r) for r in rows]}

    @app.post('/internal/orders/<uuid:id>/email-sent')
    @require_service('CHECKOUT_API_TOKEN')
    def email_sent(id):
        with pool.connection() as conn:
            conn.execute("UPDATE boutique.orders SET email_sent=true,email_lease_until=NULL WHERE id=%s AND state='confirmed'", (id,))
        return {}, 200

    @app.get('/api/v1/orders')
    @admin()
    def orders():
        return orders_page()

    @app.get('/api/v1/orders/<uuid:id>')
    @admin()
    def detail(id):
        with pool.connection() as conn:
            row = conn.execute('SELECT * FROM boutique.orders WHERE id=%s', (id,)).fetchone()
            if not row:
                abort(404)
            items = conn.execute('SELECT product_id,name,sku,quantity,unit_nanos FROM boutique.order_items WHERE order_id=%s', (id,)).fetchall()
            events = conn.execute('SELECT * FROM boutique.shipment_events WHERE order_id=%s ORDER BY id DESC LIMIT 100', (id,)).fetchall()
        return dict(order_view(row), Items=[{'Name': i['name'], 'SKU': i['sku'], 'Quantity': i['quantity'],
                    'Price': money(i['unit_nanos'], row['currency']), 'Subtotal': money(i['unit_nanos'] * i['quantity'], row['currency'])} for i in items],
                    ShipmentEvents=[{'Date': e['created_at'].isoformat(), 'Status': e['status'],
                                     'Carrier': e['carrier'], 'TrackingID': e['tracking_id'],
                                     'Notes': e['notes'], 'Actor': e['actor']} for e in events])

    @app.patch('/api/v1/orders/<uuid:id>/shipment')
    @admin(write=True)
    def shipment(id):
        with pool.connection() as conn:
            row = conn.execute('SELECT * FROM boutique.orders WHERE id=%s FOR UPDATE', (id,)).fetchone()
            if not row:
                abort(404)
            updated = update_shipment(conn, row, request.get_json(), g.admin['email'], g.request_id)
        return order_view(updated)

    @app.patch('/api/v1/orders/<uuid:id>/status')
    @admin(write=True)
    def fulfillment(id):
        target = request.get_json().get('status')
        with pool.connection() as conn:
            row = conn.execute('SELECT * FROM boutique.orders WHERE id=%s FOR UPDATE', (id,)).fetchone()
            if not row:
                abort(404)
            if row['state'] != 'confirmed' or row['fulfillment'] != 'ENVIADO' or target != 'ENTREGADO':
                abort(409, 'Solo se permite ENVIADO → ENTREGADO; cancelar un pago requiere conciliación')
            update_shipment(conn, row, {'status': target, 'version': row['shipment_version'],
                            'carrier': row['shipping_carrier'] or 'Envío demo',
                            'tracking_id': row['payload'].get('shippingTrackingId', ''),
                            'notes': row['shipping_notes'],
                            'estimated_delivery': row['estimated_delivery'].isoformat() if row['estimated_delivery'] else ''},
                            g.admin['email'], g.request_id)
        return {}, 200

    @app.post('/api/v1/orders/<uuid:id>/reconcile')
    @admin(write=True)
    def reconcile(id):
        data = request.get_json()
        target, reason = data.get('state'), str(data.get('reason', '')).strip()
        tx = str(data.get('transaction_id', '')).strip()[:128]
        if target not in ('paid', 'failed') or data.get('verified') is not True or len(reason) < 6 or (target == 'paid' and not tx):
            raise ValueError('Se requiere resultado verificado, evidencia y referencia de pago si se confirmó')
        with pool.connection() as conn:
            row = conn.execute("SELECT *,updated_at<now()-interval '5 minutes' AS stale FROM boutique.orders WHERE id=%s FOR UPDATE", (id,)).fetchone()
            if not row or row['state'] not in ('charging', 'payment_unknown') or not row['stale']:
                abort(409, 'Pedido no conciliable o todavía en procesamiento')
            if target == 'failed':
                release(conn, row)
            conn.execute('UPDATE boutique.orders SET state=%s,transaction_id=%s,updated_at=now() WHERE id=%s', (target, tx, id))
            audit(conn, g.admin['email'], 'payment.reconcile.' + target + ': ' + reason[:512], str(id), g.request_id)
        return {}, 200
