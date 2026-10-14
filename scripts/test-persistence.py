"""Keeps a browser session in recommendationservice while peers are restarted."""
import http.cookiejar
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


login, _ = fetch('/admin/login')
csrf = re.search(r'name="csrf_token" value="([^"]+)"', login).group(1)
fetch('/admin/login', {'email': os.environ['ADMIN_EMAIL'], 'password': os.environ['ADMIN_PASSWORD'], 'csrf_token': csrf})
orders, path = fetch('/admin/pedidos?q=e2e%40example.invalid')
assert path == '/admin/pedidos'
order_id = re.findall(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', orders)[0]
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
csrf = re.search(r'name="csrf_token" value="([^"]+)"', detail).group(1)
fetch('/admin/logout', {'csrf_token': csrf})
print('PASS order and admin session survive PostgreSQL/backend/frontend restarts', flush=True)
