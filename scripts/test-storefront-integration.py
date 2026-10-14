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
        raw = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=raw, headers=headers, method=method)
    try:
        res = client.open(req, timeout=25)
    except urllib.error.HTTPError as exc:
        res = exc
    with res:
        body = res.read().decode()
        status, final_path = res.code, urllib.parse.urlparse(res.url).path
    assert status == expected, f'{path}: HTTP {status}, expected {expected}'
    return json.loads(body) if backend and status == 200 else (body, final_path)


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
request('/admin/logout', {'csrf_token': csrf})
assert request('/admin')[1] == '/admin/login'
passed('logout invalidates the shared session')
print(json.dumps({'passed': len(checks), 'order_id': order_id, 'sku_archived': sku}))
