"""End-to-end HTTP integration against the LOCAL demo containers only.

Runs in catalogservice with temporary ADMIN_EMAIL/ADMIN_PASSWORD env values.
Creates its own SKU and a simulated order; archives the SKU afterwards.
"""
import html
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

jar = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
checks = []


def request(path, data=None, backend=False, method=None, expected=200):
    base = 'http://localhost:8080' if backend else 'http://frontend:8080'
    headers = {}
    if backend:
        headers['Authorization'] = 'Bearer ' + os.environ['ADMIN_API_TOKEN']
        headers['X-Admin-Session'] = next((c.value for c in jar if c.name == 'admin_session'), '')
        headers['Content-Type'] = 'application/json'
        raw = json.dumps(data).encode() if data is not None else None
    else:
        raw = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=raw, headers=headers, method=method)
    try:
        res = client.open(req, timeout=25)
    except urllib.error.HTTPError as exc:
        res = exc
    with res:
        body = res.read().decode()
        status, final_path = res.code, urllib.parse.urlparse(res.url).path
    assert status == expected, f'{path}: HTTP {status}, expected {expected}'
    return json.loads(body) if backend and status in (200, 201) else (body, final_path)


def token(body, name='csrf_token'):
    match = re.search(r'name="' + name + r'" value="([^"]+)"', body)
    assert match, f'No {name} in rendered form'
    return html.unescape(match.group(1))


def passed(name):
    checks.append(name)
    print('PASS ' + name, flush=True)


for _ in range(30):
    try:
        request('/_readyz')
        break
    except Exception:
        time.sleep(1)
else:
    raise RuntimeError('Frontend dependencies did not become ready')
passed('frontend readiness with backend and DB')
body, path = request('/admin')
assert path == '/admin/login'
passed('admin routes require login')
login_token = token(body)
request('/admin/login', {'email': os.environ['ADMIN_EMAIL'], 'password': 'incorrect-test-password', 'csrf_token': login_token}, expected=401)
passed('invalid credentials are rejected without hiding the error')
request('/admin/login', {'email': os.environ['ADMIN_EMAIL'], 'password': os.environ['ADMIN_PASSWORD'], 'csrf_token': login_token})
body, path = request('/admin')
assert path == '/admin'
csrf = token(body)
passed('persistent admin login and dashboard')
request('/admin/catalogo/save', {'name': 'No CSRF'}, expected=403)
passed('frontend rejects mutations without CSRF')

sku = 'E2E-' + uuid.uuid4().hex
data = {'csrf_token': csrf, 'name': 'Integration ' + sku, 'sku': sku,
        'description': 'Synthetic local integration fixture', 'picture': '/static/img/products/sunglasses.jpg',
        'price': '10.25', 'category': 'accessories', 'active': 'true', 'variant': 'Standard', 'warehouse': 'Principal'}
request('/admin/catalogo/save', data)
product = request('/api/v1/products?q=' + sku, backend=True)['Products'][0]
product_id = product['ID']
passed('product created by frontend and stored in PostgreSQL')
try:
    body, _ = request('/product/' + product_id)
    assert data['name'] in body
    passed('storefront reads the same catalog through gRPC')
    updated = dict(data, id=product_id, version=str(product['Version']), price='12.50')
    request('/admin/catalogo/save', updated)
    product = request('/api/v1/products/' + product_id, backend=True)
    assert product['PriceValue'] == '12.5'
    passed('editing price is visible immediately without restarting catalog')
    request('/admin/inventario/adjust', {'csrf_token': csrf, 'product_id': product_id, 'delta': '5', 'reason': 'Local E2E fixture'})
    inventory = request('/api/v1/inventory?q=' + sku, backend=True)['Items'][0]
    assert inventory['Stock'] == 5 and inventory['Reserved'] == 0
    passed('stock adjustment is persisted')
    request('/cart', {'product_id': product_id, 'quantity': '1'})
    request('/setCurrency', {'currency_code': 'EUR'})
    body, _ = request('/cart')
    key = token(body, 'checkout_key')
    checkout = {'checkout_key': key, 'email': 'e2e@example.invalid', 'street_address': 'Demo 123',
                'zip_code': '94043', 'city': 'Demo', 'state': 'Demo', 'country': 'United States',
                'credit_card_number': '4111111111111111', 'credit_card_expiration_month': '12',
                'credit_card_expiration_year': '2099', 'credit_card_cvv': '123'}
    body, _ = request('/cart/checkout', checkout)
    assert 'Your order is complete!' in body
    order_id = re.findall(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', body)[0]
    order = request('/api/v1/orders/' + order_id, backend=True)
    assert order['State'] == 'confirmed' and order['Items'][0]['SKU'] == sku
    assert order['Total'].startswith('EUR ')
    passed('checkout creates a durable order with product snapshots')
    body, _ = request('/cart/checkout', checkout)
    assert order_id in body
    request('/cart/checkout', dict(checkout, email='different@example.invalid'), expected=409)
    passed('idempotency key cannot be reused with different buyer data')
    orders = request('/api/v1/orders?q=' + order_id, backend=True)
    assert orders['Total'] == 1
    inventory = request('/api/v1/inventory?q=' + sku, backend=True)['Items'][0]
    assert inventory['Stock'] == 4 and inventory['Reserved'] == 0
    passed('repeated checkout does not duplicate order or stock consumption')
    body, _ = request('/admin/pedidos/detail?id=' + order_id)
    assert sku in body
    passed('order detail is available in admin frontend')
    request('/admin/pedidos/status', {'csrf_token': csrf, 'id': order_id, 'status': 'ENTREGADO'})
    assert request('/api/v1/orders/' + order_id, backend=True)['Status'] == 'ENTREGADO'
    passed('delivery status transition is persisted')
    body, _ = request('/admin/export?resource=orders&q=' + order_id)
    assert order_id in body and 'ENTREGADO' in body
    passed('CSV export respects filters')
    for path in ['/admin/catalogo', '/admin/inventario', '/admin/pedidos', '/admin']:
        request(path)
    passed('all administrative pages render with real data')
finally:
    current = request('/api/v1/products/' + product_id, backend=True)
    request('/admin/catalogo/archive', {'csrf_token': csrf, 'id': product_id, 'version': str(current['Version'])})
    request('/cart/empty', {})
# Persistent demo management through the same forms the user operates.
for page in ['/admin', '/admin/catalogo', '/admin/inventario', '/admin/pedidos']:
    assert 'Cargar datos demo' in request(page)[0]
passed('persistent demo controls are available throughout the panel')
request('/admin/demo/seed', {'csrf_token': csrf})
seeded_before = request('/api/v1/inventory?warehouse=Demo%20Central', backend=True)
request('/admin/demo/seed', {'csrf_token': csrf})
assert request('/api/v1/inventory?warehouse=Demo%20Central', backend=True)['Items'] == seeded_before['Items']
assert request('/api/v1/orders?q=ana@example.invalid', backend=True)['Total'] == 1
passed('demo data loads without duplicate orders or resetting stock')

current = request('/api/v1/products/' + product_id, backend=True)
request('/admin/catalogo/activate', {'csrf_token': csrf, 'id': product_id, 'version': current['Version']})
assert request('/api/v1/products/' + product_id, backend=True)['Status'] == 'Active'
passed('archived catalog products can be reactivated')
try:
    request('/admin/inventario/adjust', {'csrf_token': csrf, 'product_id': product_id, 'movement': 'entry', 'delta': '3', 'reason': 'Recepción demo E2E'})
    request('/admin/inventario/adjust', {'csrf_token': csrf, 'product_id': product_id, 'movement': 'exit', 'delta': '1', 'reason': 'Ajuste demo E2E'})
    stock_before = request('/api/v1/inventory/' + product_id, backend=True)['Item']['Stock']
    body, _ = request('/admin/inventario/detail?id=' + product_id)
    assert 'Recepción demo E2E' in body and 'Ajuste demo E2E' in body
    passed('inventory entry, exit and movement history persist')
    request('/admin/inventario/adjust', {'csrf_token': csrf, 'product_id': product_id, 'movement': 'exit', 'delta': '100000', 'reason': 'Invalid stock test'}, expected=409)
    passed('inventory rejects exits beyond available stock')
    body, _ = request('/admin/pedidos/form')
    demo_data = {'csrf_token': csrf, 'order_key': token(body, 'order_key'), 'customer_name': 'Cliente Demo E2E',
                 'email': 'demo-e2e@example.invalid', 'street_address': 'Demo 456', 'city': 'Monterrey',
                 'state': 'Nuevo León', 'country': 'México', 'zip_code': '64000', 'shipping_fee': '5.00',
                 'product_id': [product_id, 'demo-camiseta'], 'quantity': ['2', '1']}
    body, path = request('/admin/pedidos/create-demo', demo_data)
    demo_id = re.search(r'<input type="hidden" name="id" value="([^"]+)"', body).group(1)
    demo_order = request('/api/v1/orders/' + demo_id, backend=True)
    assert demo_order['Demo'] and demo_order['Status'] == 'PENDIENTE' and len(demo_order['Items']) == 2
    assert request('/api/v1/orders?q=Cliente%20Demo%20E2E', backend=True)['Total'] >= 1
    assert demo_order['Total'] == 'USD 55.00'
    assert request('/api/v1/inventory/' + product_id, backend=True)['Item']['Stock'] == stock_before - 2
    passed('multi-product demo order is created from the frontend with exact totals and stock consumption')
    body, _ = request('/admin/pedidos/create-demo', demo_data)
    assert demo_id in body
    assert request('/api/v1/orders?q=' + demo_id, backend=True)['Total'] == 1
    passed('repeating a demo order form does not duplicate the order')
    shipment = {'csrf_token': csrf, 'id': demo_id, 'version': str(demo_order['Shipment']['Version']),
                'status': 'ENVIADO', 'carrier': 'Paquetería demo E2E', 'tracking_id': 'DEMO-E2E-' + uuid.uuid4().hex[:8],
                'estimated_delivery': '2026-12-10', 'notes': 'Salida de almacén demo'}
    request('/admin/pedidos/shipment', dict(shipment, csrf_token=''), expected=403)
    request('/admin/pedidos/shipment', dict(shipment, carrier=''), expected=422)
    passed('shipping forms require CSRF, carrier and tracking details')
    body, path = request('/admin/pedidos/shipment', shipment)
    assert path == '/admin/pedidos/detail' and shipment['tracking_id'] in body
    shipped = request('/api/v1/orders/' + demo_id, backend=True)
    assert shipped['Status'] == 'ENVIADO' and shipped['Shipment']['EstimatedDelivery'] == '2026-12-10'
    passed('shipment carrier, tracking and estimated delivery persist within the order detail')
    request('/admin/pedidos/shipment', shipment, expected=409)
    request('/admin/pedidos/cancel-demo', {'csrf_token': csrf, 'id': demo_id, 'reason': 'Already shipped'}, expected=409)
    passed('stale shipment edits and cancellation after shipping are rejected')
    shipment.update(version=str(shipped['Shipment']['Version']), status='INCIDENCIA', notes='Cliente ausente demo')
    request('/admin/pedidos/shipment', shipment)
    incident = request('/api/v1/orders/' + demo_id, backend=True)
    assert incident['Status'] == 'INCIDENCIA'
    assert request('/api/v1/orders?status=INCIDENCIA&q=' + demo_id, backend=True)['Total'] == 1
    passed('shipping incidents are recorded and searchable')
    shipment.update(version=str(incident['Shipment']['Version']), status='ENTREGADO', notes='Recibido por cliente demo')
    body, _ = request('/admin/pedidos/shipment', shipment)
    delivered = request('/api/v1/orders/' + demo_id, backend=True)
    assert delivered['Status'] == 'ENTREGADO' and len(delivered['ShipmentEvents']) == 4
    assert 'Cliente ausente demo' in body and 'Entrega finalizada' in body
    passed('delivery finalizes the shipment with a persistent event history')
    body, _ = request('/admin/pedidos/form')
    cancel_data = dict(demo_data, order_key=token(body, 'order_key'), product_id=[product_id], quantity=['1'])
    body, _ = request('/admin/pedidos/create-demo', cancel_data)
    cancel_id = re.search(r'<input type="hidden" name="id" value="([^"]+)"', body).group(1)
    request('/admin/pedidos/cancel-demo', {'csrf_token': csrf, 'id': cancel_id, 'reason': 'Cancelación demo E2E'})
    request('/admin/pedidos/cancel-demo', {'csrf_token': csrf, 'id': cancel_id, 'reason': 'Repetir cancelación'}, expected=409)
    assert request('/api/v1/orders/' + cancel_id, backend=True)['Status'] == 'CANCELADO'
    assert request('/api/v1/inventory/' + product_id, backend=True)['Item']['Stock'] == stock_before - 2
    passed('pending demo cancellation restores stock exactly once')
finally:
    current = request('/api/v1/products/' + product_id, backend=True)
    request('/admin/catalogo/archive', {'csrf_token': csrf, 'id': product_id, 'version': str(current['Version'])})

request('/admin/logout', {'csrf_token': csrf})
assert request('/admin')[1] == '/admin/login'
passed('logout invalidates the shared session')
print(json.dumps({'passed': len(checks), 'order_id': order_id, 'sku_archived': sku}))
