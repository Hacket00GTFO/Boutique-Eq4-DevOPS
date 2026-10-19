"""Keeps a browser session in recommendationservice while peers are restarted."""
import http.cookiejar
import json
import os
import re
import time
import urllib.parse
import urllib.request

jar = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def fetch(path, data=None):
    with client.open('http://frontend:8080' + path,
                     data=urllib.parse.urlencode(data).encode() if data else None, timeout=10) as response:
        return response.read().decode(), urllib.parse.urlparse(response.url).path


def api(path):
    session = next(cookie.value for cookie in jar if cookie.name == 'admin_session')
    req = urllib.request.Request('http://catalogservice:8080/api/v1/' + path,
                                 headers={'Authorization': 'Bearer ' + os.environ['ADMIN_API_TOKEN'],
                                          'X-Admin-Session': session})
    with client.open(req, timeout=10) as response:
        return json.load(response)


login, _ = fetch('/admin/login')
csrf = re.search(r'name="csrf_token" value="([^"]+)"', login).group(1)
fetch('/admin/login', {'email': os.environ['ADMIN_EMAIL'], 'password': os.environ['ADMIN_PASSWORD'], 'csrf_token': csrf})
orders, path = fetch('/admin/pedidos?q=e2e%40example.invalid')
assert path == '/admin/pedidos'
order_id = re.findall(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', orders)[0]
demo_orders = api('orders?q=demo-e2e%40example.invalid&status=ENTREGADO')['Orders']
assert demo_orders, 'Run test-storefront-integration.ps1 first to create the shipment fixture'
demo_before = api('orders/' + demo_orders[0]['ID'])
inventory_before = api('inventory?q=' + urllib.parse.quote(demo_before['Items'][0]['SKU']))['Items'][0]
movements_before = api('inventory/' + inventory_before['ID'])
print('PERSISTENCE_READY', flush=True)
# The PowerShell parent restarts frontend, backend and PostgreSQL in this window.
time.sleep(20)
for _ in range(30):
    try:
        _, path = fetch('/_readyz')
        if path == '/_readyz':
            break
    except Exception:
        time.sleep(1)
else:
    raise RuntimeError('Services failed to recover')
detail, path = fetch('/admin/pedidos/detail?id=' + order_id)
assert path == '/admin/pedidos/detail' and order_id in detail, 'Session/order lost after restart'
demo_after = api('orders/' + demo_before['ID'])
for field in ('Shipment', 'ShipmentEvents', 'Items', 'Status', 'Total'):
    assert demo_after[field] == demo_before[field], 'Demo shipment data changed after restart: ' + field
assert api('inventory/' + inventory_before['ID']) == movements_before, 'Inventory/movements lost after restart'
csrf = re.search(r'name="csrf_token" value="([^"]+)"', detail).group(1)
fetch('/admin/logout', {'csrf_token': csrf})
print('PASS order, session, demo shipment history and inventory movements survive PostgreSQL/backend/frontend restarts', flush=True)
