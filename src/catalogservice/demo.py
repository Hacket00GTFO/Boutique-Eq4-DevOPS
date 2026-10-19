"""Persistent demo fixtures and manual simulated orders, enabled only explicitly."""
import hashlib
import json
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
from flask import abort, g, request
from psycopg.types.json import Jsonb
from auth import admin
from catalog import product_view
from db import audit, pool
from orders import order_view
from shipments import shipment_event, update_shipment


def require_demo():
    if os.getenv('DEMO_DATA_ENABLED') != 'true':
        abort(403, 'Las operaciones demo están deshabilitadas en este entorno')


def money_proto(value):
    return {'currencyCode': 'USD', 'units': str(value // 1000000000), 'nanos': value % 1000000000}


def create_demo_order(conn, data, actor, user_id=None):
    if not isinstance(data, dict) or not isinstance(data.get('address'), dict):
        raise ValueError('Datos de pedido o dirección inválidos')
    key = str(data.get('key', ''))
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', key):
        raise ValueError('Llave de pedido inválida')
    email = str(data.get('email', '')).strip().lower()
    name = str(data.get('customer_name', '')).strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254 or not name or len(name) > 128:
        raise ValueError('Nombre y correo del cliente son obligatorios')
    address = {k: str(data.get('address', {}).get(k, '')).strip() for k in
               ('streetAddress', 'city', 'state', 'country', 'zipCode')}
    if not all(address.values()) or any(len(v) > 255 for v in address.values()):
        raise ValueError('Completa la dirección de entrega')
    try:
        fee = Decimal(str(data.get('shipping_fee', '0'))) * 1000000000
        if not fee.is_finite() or fee < 0 or fee > 10**15 or fee != fee.to_integral_value():
            raise ValueError()
        fee = int(fee)
    except (InvalidOperation, ValueError):
        raise ValueError('Costo de envío inválido')
    items = data.get('items', [])
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise ValueError('Agrega de 1 a 20 productos')
    normalized = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('product_id'), str):
            raise ValueError('Producto inválido')
        quantity = str(item.get('quantity', ''))
        if not re.fullmatch(r'[0-9]{1,3}', quantity):
            raise ValueError('La cantidad debe ser un entero entre 1 y 100')
        normalized.append((item['product_id'], int(quantity)))
    normalized.sort()
    if len({id for id, _ in normalized}) != len(normalized) or any(not 1 <= q <= 100 for _, q in normalized):
        raise ValueError('Productos duplicados o cantidades inválidas')
    fingerprint = hashlib.sha256(json.dumps([email, name, address, fee, normalized], sort_keys=True).encode()).hexdigest()
    user_id = user_id or 'admin-demo:' + actor
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (user_id + ':' + key,))
    existing = conn.execute('SELECT * FROM boutique.orders WHERE user_id=%s AND idempotency_key=%s', (user_id, key)).fetchone()
    if existing:
        if existing['payload'].get('demoRequestHash') != fingerprint:
            abort(409, 'La llave del pedido ya corresponde a otros datos')
        return existing, False
    snapshots, payload_items, total = [], [], fee
    for id, quantity in normalized:
        p = conn.execute('SELECT * FROM boutique.products WHERE id=%s FOR UPDATE', (id,)).fetchone()
        if not p or not p['active'] or p['stock'] - p['reserved'] < quantity:
            abort(409, 'Producto inactivo o sin existencias suficientes')
        unit = p['price_units'] * 1000000000 + p['price_nanos']
        total += unit * quantity
        snapshots.append((id, p['name'], p['sku'], quantity, unit))
        payload_items.append({'item': {'productId': id, 'quantity': quantity}, 'cost': money_proto(unit)})
    id = uuid.uuid4()
    payload = {'orderId': str(id), 'shippingAddress': address, 'shippingCost': money_proto(fee),
               'items': payload_items, 'demoRequestHash': fingerprint}
    row = conn.execute('''INSERT INTO boutique.orders(id,user_id,idempotency_key,email,currency,total_nanos,
              total_usd_nanos,state,payload,payment_method,demo,customer_name,transaction_id,email_sent)
              VALUES (%s,%s,%s,%s,'USD',%s,%s,'confirmed',%s,'Pago demo',true,%s,%s,true) RETURNING *''',
              (id, user_id, key, email, total, total, Jsonb(payload), name, 'DEMO-' + str(id))).fetchone()
    for snapshot in snapshots:
        conn.execute('INSERT INTO boutique.order_items(order_id,product_id,name,sku,quantity,unit_nanos) VALUES (%s,%s,%s,%s,%s,%s)', (id,) + snapshot)
        conn.execute('UPDATE boutique.products SET stock=stock-%s,version=version+1,updated_at=now() WHERE id=%s', (snapshot[3], snapshot[0]))
        conn.execute('INSERT INTO boutique.stock_movements(product_id,delta,reason,actor) VALUES (%s,%s,%s,%s)',
                     (snapshot[0], -snapshot[3], 'Pedido demo ' + str(id), actor))
    shipment_event(conn, row, actor, 'Pedido demo creado; pendiente de envío')
    audit(conn, actor, 'demo.order.create', str(id), g.request_id)
    return row, True


DEMO_PRODUCTS = [
    ('demo-camiseta', 'DEMO-CAM-01', 'Camiseta algodón demo', 'men, clothing', 25, 'tank-top.jpg', 32, 'M / Blanco'),
    ('demo-reloj', 'DEMO-REL-01', 'Reloj urbano demo', 'women, accessories', 65, 'watch.jpg', 20, 'Negro'),
    ('demo-lentes', 'DEMO-LEN-01', 'Lentes de sol demo', 'accessories', 19, 'sunglasses.jpg', 7, 'Unisex'),
    ('demo-zapatos', 'DEMO-ZAP-01', 'Zapatos casuales demo', 'men, shoes', 89, 'loafers.jpg', 0, '27 / Café'),
]


def register_demo(app):
    @app.get('/api/v1/demo/products')
    @admin()
    def options():
        require_demo()
        with pool.connection() as conn:
            rows = conn.execute('SELECT * FROM boutique.products WHERE active AND stock>reserved ORDER BY name,id LIMIT 200').fetchall()
        return {'Products': [dict(product_view(p), Available=p['stock'] - p['reserved']) for p in rows]}

    @app.post('/api/v1/demo/orders')
    @admin(write=True)
    def create():
        require_demo()
        with pool.connection() as conn:
            row, created = create_demo_order(conn, request.get_json(), g.admin['email'])
        return order_view(row), 201 if created else 200

    @app.post('/api/v1/demo/seed')
    @admin(write=True)
    def seed():
        require_demo()
        products_created, orders_created = 0, 0
        with pool.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(482402)')
            for id, sku, name, categories, price, image, stock, variant in DEMO_PRODUCTS:
                row = conn.execute('''INSERT INTO boutique.products(id,sku,name,description,categories,price_units,
                         picture,stock,variant,warehouse) VALUES (%s,%s,%s,'Producto de demostración persistente',%s,%s,%s,%s,%s,'Demo Central')
                         ON CONFLICT(id) DO NOTHING RETURNING id''',
                         (id, sku, name, categories.split(', '), price, '/static/img/products/' + image, stock, variant)).fetchone()
                if row:
                    products_created += 1
                    if stock:
                        conn.execute('INSERT INTO boutique.stock_movements(product_id,delta,reason,actor) VALUES (%s,%s,%s,%s)',
                                     (id, stock, 'Existencias iniciales demo', g.admin['email']))
            for key, name, email, product, quantity, status in [
                ('demo-order-01', 'Ana Demo', 'ana@example.invalid', 'demo-camiseta', 2, 'PENDIENTE'),
                ('demo-order-02', 'Luis Demo', 'luis@example.invalid', 'demo-reloj', 1, 'ENVIADO'),
                ('demo-order-03', 'María Demo', 'maria@example.invalid', 'demo-lentes', 3, 'ENTREGADO'),
            ]:
                # Repeated loading preserves subsequent edits, stock and shipment states.
                if conn.execute("SELECT 1 FROM boutique.orders WHERE user_id='demo-seed' AND idempotency_key=%s", (key,)).fetchone():
                    continue
                row, _ = create_demo_order(conn, {'key': key, 'customer_name': name, 'email': email,
                          'shipping_fee': '5.00', 'address': {'streetAddress': 'Av. Demo 123', 'city': 'Monterrey',
                          'state': 'Nuevo León', 'country': 'México', 'zipCode': '64000'},
                          'items': [{'product_id': product, 'quantity': quantity}]}, g.admin['email'], 'demo-seed')
                orders_created += 1
                if status != 'PENDIENTE':
                    data = {'version': row['shipment_version'], 'status': 'ENVIADO', 'carrier': 'Paquetería demo',
                            'tracking_id': key.upper(), 'notes': 'Envío simulado de ejemplo'}
                    row = update_shipment(conn, row, data, g.admin['email'], g.request_id)
                    if status == 'ENTREGADO':
                        update_shipment(conn, row, dict(data, version=row['shipment_version'], status=status), g.admin['email'], g.request_id)
            audit(conn, g.admin['email'], 'demo.seed', 'demo-v1', g.request_id)
        return {'ProductsCreated': products_created, 'OrdersCreated': orders_created}

    @app.post('/api/v1/orders/<uuid:id>/cancel-demo')
    @admin(write=True)
    def cancel(id):
        require_demo()
        reason = str(request.get_json().get('reason', '')).strip()
        if not 3 <= len(reason) <= 512:
            raise ValueError('Indica un motivo de cancelación de 3 a 512 caracteres')
        with pool.connection() as conn:
            row = conn.execute('SELECT * FROM boutique.orders WHERE id=%s FOR UPDATE', (id,)).fetchone()
            if not row:
                abort(404)
            if not row['demo'] or row['state'] != 'confirmed' or row['fulfillment'] != 'PENDIENTE':
                abort(409, 'Solo se puede cancelar un pedido demo pendiente de envío')
            items = conn.execute('SELECT * FROM boutique.order_items WHERE order_id=%s ORDER BY product_id', (id,)).fetchall()
            for item in items:
                conn.execute('UPDATE boutique.products SET stock=stock+%s,version=version+1,updated_at=now() WHERE id=%s', (item['quantity'], item['product_id']))
                conn.execute('INSERT INTO boutique.stock_movements(product_id,delta,reason,actor) VALUES (%s,%s,%s,%s)',
                             (item['product_id'], item['quantity'], 'Cancelación demo: ' + reason, g.admin['email']))
            row = conn.execute("UPDATE boutique.orders SET state='cancelled',fulfillment='CANCELADO',shipment_version=shipment_version+1,updated_at=now() WHERE id=%s RETURNING *", (id,)).fetchone()
            shipment_event(conn, row, g.admin['email'], reason)
            audit(conn, g.admin['email'], 'demo.order.cancel', str(id), g.request_id)
        return order_view(row)
