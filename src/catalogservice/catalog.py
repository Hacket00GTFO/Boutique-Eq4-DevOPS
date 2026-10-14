import math
import re
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from flask import abort, g, request
from auth import admin, require_service
from db import audit, pool


def money(nanos, currency='USD'):
    return f'{currency} {Decimal(nanos) / Decimal(1000000000):,.2f}'


def pagination():
    try:
        page = max(1, int(request.args.get('page', 1)))
        size = min(100, max(1, int(request.args.get('page_size', 10))))
    except ValueError:
        abort(422, 'Página inválida')
    return page, size


def page_info(total, page, size):
    return {'Total': total, 'CurrentPage': page, 'TotalPages': max(1, math.ceil(total / size)),
            'ShowingFrom': (page - 1) * size + 1 if total and (page - 1) * size < total else 0,
            'ShowingTo': min(page * size, total)}


def product_view(p):
    return {'ID': p['id'], 'Name': p['name'], 'Description': p['description'],
            'SKU': p['sku'], 'Category': ', '.join(p['categories']), 'Categories': p['categories'],
            'Price': money(p['price_units'] * 1000000000 + p['price_nanos']),
            'PriceValue': str(Decimal(p['price_units']) + Decimal(p['price_nanos']) / 1000000000),
            'Status': 'Active' if p['active'] else 'Inactive', 'ImgURL': p['picture'],
            'Variant': p['variant'], 'Location': p['warehouse'], 'Version': p['version']}


def inventory_view(p):
    available = p['stock'] - p['reserved']
    return dict(product_view(p), Product=p['name'], Stock=available, Reserved=p['reserved'],
                OnHand=p['stock'], Status='Out of Stock' if available == 0 else 'Low Stock' if available <= 5 else 'In Stock')


def filtered_products(inventory=False, all_rows=False):
    clauses, params = ['true'], []
    category = request.args.get('category', 'all')
    if category != 'all':
        clauses.append('%s=ANY(categories)')
        params.append(category)
    q = request.args.get('q', '').strip()[:128]
    if q:
        clauses.append('(name ILIKE %s OR sku ILIKE %s)')
        params.extend(['%' + q + '%'] * 2)
    warehouse = request.args.get('warehouse', '')
    if warehouse:
        clauses.append('warehouse=%s')
        params.append(warehouse)
    stock = request.args.get('stock', 'all')
    stock_sql = {'in': 'stock-reserved>5', 'low': 'stock-reserved BETWEEN 1 AND 5', 'out': 'stock=reserved'}
    if inventory and stock in stock_sql:
        clauses.append(stock_sql[stock])
    page, size = pagination()
    where = ' AND '.join(clauses)
    with pool.connection() as conn:
        total = conn.execute('SELECT count(*) AS n FROM boutique.products WHERE ' + where, params).fetchone()['n']
        rows = conn.execute('SELECT * FROM boutique.products WHERE ' + where + ' ORDER BY created_at,id' +
                            (' LIMIT 10001' if all_rows else ' LIMIT %s OFFSET %s'), params + ([] if all_rows else [size, (page - 1) * size])).fetchall()
        stats = conn.execute('SELECT count(*) AS total,count(*) FILTER(WHERE stock-reserved<=5) AS low FROM boutique.products').fetchone()
        locations = [r['warehouse'] for r in conn.execute('SELECT DISTINCT warehouse FROM boutique.products ORDER BY warehouse')]
    result = page_info(total, page, size)
    result.update(ActiveCategory=category, ActiveFilter=stock, Warehouses=locations)
    result['Items' if inventory else 'Products'] = [(inventory_view if inventory else product_view)(r) for r in rows]
    if inventory:
        result['Stats'] = [{'Label': 'TOTAL SKUS', 'Value': str(stats['total']), 'Icon': 'inventory_2'},
                           {'Label': 'STOCK CRÍTICO', 'Value': str(stats['low']), 'Icon': 'warning'}]
    return result


def validate_product(data):
    name = str(data.get('name', '')).strip()
    sku = str(data.get('sku', '')).strip()
    if not name or len(name) > 255 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', sku):
        raise ValueError('Nombre y SKU válido son obligatorios')
    try:
        price = Decimal(str(data.get('price', ''))) * 1000000000
        if not price.is_finite() or price < 0 or price > 10**18 or price != price.to_integral_value():
            raise ValueError('Precio fuera de rango o precisión inválida')
        price = int(price)
    except (InvalidOperation, ValueError):
        raise ValueError('Precio inválido')
    picture = str(data.get('picture', '')).strip()
    if picture and not (picture.startswith('/static/') or urlparse(picture).scheme in ('http', 'https')):
        raise ValueError('Imagen debe usar /static/ o una URL http(s)')
    categories = list(dict.fromkeys(c.strip().lower() for c in str(data.get('category', 'accessories')).split(',')))
    if not 1 <= len(categories) <= 10 or not all(re.fullmatch(r'[a-z0-9_-]{1,64}', c) for c in categories):
        raise ValueError('Categoría inválida')
    return (name, str(data.get('description', ''))[:4000], picture[:2048], categories,
            price // 1000000000, price % 1000000000, sku, data.get('active', True) is True,
            str(data.get('variant', 'Standard'))[:128], str(data.get('warehouse', 'Principal'))[:128])


def register_catalog(app):
    @app.get('/internal/products')
    @require_service('CATALOG_READ_TOKEN')
    def storefront_catalog():
        with pool.connection() as conn:
            rows = conn.execute('SELECT * FROM boutique.products WHERE active ORDER BY created_at,id').fetchall()
        return {'products': [{'id': p['id'], 'name': p['name'], 'description': p['description'],
                             'picture': p['picture'], 'categories': p['categories'],
                             'priceUsd': {'currencyCode': 'USD', 'units': str(p['price_units']), 'nanos': p['price_nanos']}} for p in rows]}

    @app.get('/api/v1/products')
    @admin()
    def products():
        return filtered_products()

    @app.get('/api/v1/products/<id>')
    @admin()
    def product(id):
        with pool.connection() as conn:
            row = conn.execute('SELECT * FROM boutique.products WHERE id=%s', (id,)).fetchone()
        if not row:
            abort(404)
        return product_view(row)

    @app.post('/api/v1/products')
    @admin(write=True)
    def create_product():
        values = validate_product(request.get_json())
        id = str(uuid.uuid4())
        with pool.connection() as conn:
            row = conn.execute('''INSERT INTO boutique.products(id,name,description,picture,categories,price_units,price_nanos,sku,active,variant,warehouse)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''', (id,) + values).fetchone()
            audit(conn, g.admin['email'], 'product.create', id, g.request_id)
        return product_view(row), 201

    @app.patch('/api/v1/products/<id>')
    @admin(write=True)
    def update_product(id):
        data = request.get_json()
        values = validate_product(data)
        with pool.connection() as conn:
            row = conn.execute('''UPDATE boutique.products SET name=%s,description=%s,picture=%s,categories=%s,price_units=%s,price_nanos=%s,
                              sku=%s,active=%s,variant=%s,warehouse=%s,version=version+1,updated_at=now()
                              WHERE id=%s AND version=%s RETURNING *''', values + (id, int(data.get('version', 0)))).fetchone()
            if not row:
                abort(409, 'Producto cambiado por otro usuario; vuelve a cargarlo')
            audit(conn, g.admin['email'], 'product.update', id, g.request_id)
        return product_view(row)

    @app.delete('/api/v1/products/<id>')
    @admin(write=True)
    def archive_product(id):
        data = request.get_json()
        with pool.connection() as conn:
            row = conn.execute('UPDATE boutique.products SET active=false,version=version+1,updated_at=now() WHERE id=%s AND version=%s RETURNING id', (id, int(data.get('version', 0)))).fetchone()
            if not row:
                abort(409, 'Producto cambiado o inexistente')
            audit(conn, g.admin['email'], 'product.archive', id, g.request_id)
        return {}, 200

    @app.get('/api/v1/inventory')
    @admin()
    def inventory():
        return filtered_products(inventory=True)

    @app.post('/api/v1/inventory/adjustments')
    @admin(write=True)
    def adjust():
        data = request.get_json()
        delta, reason = int(data.get('delta', 0)), str(data.get('reason', '')).strip()[:512]
        if not reason or not delta or abs(delta) > 1000000:
            raise ValueError('Cantidad y motivo de ajuste son obligatorios')
        with pool.connection() as conn:
            row = conn.execute('''UPDATE boutique.products SET stock=stock+%s,version=version+1,updated_at=now()
                                WHERE id=%s AND stock+%s>=reserved RETURNING *''', (delta, data['product_id'], delta)).fetchone()
            if not row:
                abort(409, 'Stock insuficiente o reservado')
            conn.execute('INSERT INTO boutique.stock_movements(product_id,delta,reason,actor) VALUES (%s,%s,%s,%s)', (row['id'], delta, reason, g.admin['email']))
            audit(conn, g.admin['email'], 'inventory.adjust', row['id'], g.request_id)
        return inventory_view(row)
