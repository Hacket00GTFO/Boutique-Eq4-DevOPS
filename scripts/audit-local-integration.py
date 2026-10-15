"""Audit the existing local demo from recommendationservice's Python runtime.

PowerShell (from the repository):
  Get-Content scripts/audit-local-integration.py -Raw | docker compose exec -T recommendationservice python -

Uses existing grpc stubs, a fresh HTTP cookie jar and synthetic data. Mutates
only that new session's cart and admin session. Checkout uses DEMO services.
Do not run against production. Does not restart containers or install packages.
"""
import datetime
import http.cookiejar
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import grpc
import demo_pb2 as pb
import demo_pb2_grpc as rpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

results = []


def check(name, operation):
    started = time.monotonic()
    try:
        detail = operation()
        results.append(dict(name=name, status="pass", detail=detail,
                            duration_ms=round((time.monotonic() - started) * 1000)))
        return detail
    except Exception as exc:
        results.append(dict(name=name, status="fail", detail=str(exc),
                            duration_ms=round((time.monotonic() - started) * 1000)))
        return None


addresses = {
    "productcatalogservice": 3550, "currencyservice": 7000,
    "cartservice": 7070, "recommendationservice": 8080,
    "shippingservice": 50051, "checkoutservice": 5050,
    "adservice": 9555, "paymentservice": 50051, "emailservice": 8080,
}


def health(name, port):
    resolved = socket.gethostbyname(name)
    with grpc.insecure_channel(f"{name}:{port}") as channel:
        response = health_pb2_grpc.HealthStub(channel).Check(
            health_pb2.HealthCheckRequest(), timeout=3)
    assert response.status == health_pb2.HealthCheckResponse.SERVING, response.status
    return dict(ip=resolved, port=port, grpc_health="SERVING")


for service, port in addresses.items():
    check(f"grpc.health.{service}", lambda s=service, p=port: health(s, p))

catalog_channel = grpc.insecure_channel("productcatalogservice:3550")
catalog = rpc.ProductCatalogServiceStub(catalog_channel)
products = check("grpc.catalog.list", lambda: {
    "ids": [p.id for p in catalog.ListProducts(pb.Empty(), timeout=3).products]})

jar = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def http(path, data=None, expected=200, contains=None, expected_path=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        response = client.open("http://frontend:8080" + path, data=body, timeout=12)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        code = response.code
        html = response.read().decode("utf-8", errors="replace")
        final_path = urllib.parse.urlparse(response.url).path
    assert code == expected, f"HTTP {code}, expected {expected} at {path}"
    assert contains is None or contains in html, f"missing expected content at {path}"
    assert expected_path is None or final_path == expected_path, f"unexpected redirect to {final_path}"
    return dict(code=code, final_path=final_path), html


check("http.frontend.health", lambda: http("/_healthz", contains="ok")[0])
check("http.store.home", lambda: http("/")[0])
check("http.admin.unauthenticated", lambda: http("/admin", expected_path="/admin/login")[0])
check("http.admin.demo_login", lambda: http("/admin/login", {
    "email": "admin@boutique.com", "password": "admin123"}, expected_path="/admin")[0])
for path in ("/admin", "/admin/catalogo", "/admin/inventario", "/admin/pedidos"):
    def admin_page(p=path):
        detail, html = http(p)
        assert detail["final_path"] == p, "not authenticated (demo credentials may differ)"
        return detail
    check("http" + path.replace("/", "."), admin_page)

if products and products["ids"]:
    product_id = products["ids"][0]
    def recommendations():
        with grpc.insecure_channel("recommendationservice:8080") as channel:
            ids = rpc.RecommendationServiceStub(channel).ListRecommendations(
                pb.ListRecommendationsRequest(product_ids=[product_id]), timeout=3).product_ids
        assert ids and all(i in products["ids"] and i != product_id for i in ids)
        return dict(products=len(ids))
    check("grpc.recommendations.list", recommendations)

    def ads():
        with grpc.insecure_channel("adservice:9555") as channel:
            response = rpc.AdServiceStub(channel).GetAds(pb.AdRequest(), timeout=3)
        assert response.ads, "no ad returned"
        return dict(ads=len(response.ads))
    check("grpc.ads.get", ads)

    def currency():
        with grpc.insecure_channel("currencyservice:7000") as channel:
            response = rpc.CurrencyServiceStub(channel).Convert(pb.CurrencyConversionRequest(**{
                "from": pb.Money(currency_code="USD", units=10), "to_code": "EUR"}), timeout=3)
        assert response.currency_code == "EUR" and (response.units > 0 or response.nanos > 0)
        return dict(currency=response.currency_code)
    check("grpc.currency.convert", currency)

    def shipping():
        with grpc.insecure_channel("shippingservice:50051") as channel:
            response = rpc.ShippingServiceStub(channel).GetQuote(pb.GetQuoteRequest(
                items=[pb.CartItem(product_id=product_id, quantity=1)]), timeout=3)
        assert response.cost_usd.currency_code == "USD"
        return dict(currency=response.cost_usd.currency_code)
    check("grpc.shipping.quote", shipping)
    check("http.store.product", lambda: http("/product/" + product_id)[0])
    def set_currency():
        detail, _ = http("/setCurrency", {"currency_code": "EUR"})
        assert any(c.name == "shop_currency" and c.value == "EUR" for c in jar)
        return detail
    check("http.store.currency", set_currency)
    check("http.store.add_to_cart", lambda: http("/cart", {
        "product_id": product_id, "quantity": "1"})[0])
    user_id = next((c.value for c in jar if c.name == "shop_session-id"), None)
    cart_channel = grpc.insecure_channel("cartservice:7070")
    cart = rpc.CartServiceStub(cart_channel)

    def verify_cart(expected_count):
        assert user_id, "frontend did not set shop_session-id"
        response = cart.GetCart(pb.GetCartRequest(user_id=user_id), timeout=3)
        assert len(response.items) == expected_count, f"cart has {len(response.items)} items"
        if expected_count:
            assert response.items[0].product_id == product_id and response.items[0].quantity == 1
        return dict(items=len(response.items))

    try:
        check("grpc.cart.after_http_add", lambda: verify_cart(1))
        check("http.store.cart", lambda: http("/cart")[0])
        def checkout():
            detail, html = http("/cart/checkout", {
                "email": "integration-audit@example.invalid", "street_address": "Demo 123",
                "zip_code": "94043", "city": "Demo", "state": "Demo",
                "country": "United States", "credit_card_number": "4111111111111111",
                "credit_card_expiration_month": "12", "credit_card_expiration_year": "2099",
                "credit_card_cvv": "123",
            }, contains="Your order is complete!")
            ids = re.findall(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", html)
            assert ids, "no order identifier rendered"
            detail["order_id"] = ids[0]
            return detail
        check("http.store.demo_checkout", checkout)
        check("grpc.cart.after_checkout", lambda: verify_cart(0))
    finally:
        check("cleanup.cart", lambda: http("/cart/empty", {})[0])
        cart_channel.close()

check("cleanup.admin_session", lambda: http("/admin/logout")[0])
catalog_channel.close()
print(json.dumps({
    "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "scope": "existing local Docker demo; not production; no PostgreSQL deployed",
    "results": results,
    "summary": {status: sum(r["status"] == status for r in results) for status in ("pass", "fail")},
}, indent=2))
raise SystemExit(1 if any(r["status"] == "fail" for r in results) else 0)
