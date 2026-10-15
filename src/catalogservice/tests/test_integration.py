"""Real PostgreSQL contract/transaction tests. Use only the isolated test DB."""
import concurrent.futures
import os
import unittest
import uuid
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from main import app
from db import pool


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if '/boutique_test' not in os.environ['DATABASE_URL']:
            raise RuntimeError('Tests require an isolated boutique_test database')
        cls.admin_headers = {'Authorization': 'Bearer ' + os.environ['ADMIN_API_TOKEN']}
        with app.test_client() as client:
            response = client.post('/api/v1/auth/login', headers=cls.admin_headers,
                                   json={'email': os.environ['ADMIN_EMAIL'], 'password': os.environ['ADMIN_PASSWORD']})
            assert response.status_code == 200, response.json
            cls.admin_headers['X-Admin-Session'] = response.json['token']
        cls.internal = {'Authorization': 'Bearer ' + os.environ['CHECKOUT_API_TOKEN']}
        cls.read = {'Authorization': 'Bearer ' + os.environ['CATALOG_READ_TOKEN']}

    def setUp(self):
        self.client = app.test_client()
        self.sku = 'TEST-' + uuid.uuid4().hex
        response = self.client.post('/api/v1/products', headers=self.admin_headers,
                                    json={'name': '=Test product', 'sku': self.sku, 'price': '10.25', 'category': 'accessories'})
        self.assertEqual(response.status_code, 201, response.json)
        self.product = response.json

    def stock(self, quantity):
        return self.client.post('/api/v1/inventory/adjustments', headers=self.admin_headers,
                                json={'product_id': self.product['ID'], 'delta': quantity, 'reason': 'Test fixture'})

    def order(self, key=None):
        return {'key': key or uuid.uuid4().hex, 'user_id': 'test-user', 'email': 'test@example.invalid', 'currency': 'USD',
                'last_four': '1111', 'address': {'city': 'Test'}, 'shipping_cost': {'currencyCode': 'USD', 'units': '1'},
                'shipping_cost_usd': {'currencyCode': 'USD', 'units': '1'},
                'items': [{'item': {'productId': self.product['ID'], 'quantity': 1},
                           'cost': {'currencyCode': 'USD', 'units': '10', 'nanos': 250000000},
                           'price_usd': {'currencyCode': 'USD', 'units': '10', 'nanos': 250000000}}]}

    def test_permissions_and_durable_sessions(self):
        self.assertEqual(self.client.get('/api/v1/products').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/products', headers={'Authorization': self.admin_headers['Authorization']}).status_code, 401)
        with pool.connection() as conn:
            conn.execute('INSERT INTO boutique.admin_users(id,email,password_hash,role) VALUES (%s,%s,%s,%s)',
                         (uuid.uuid4(), self.sku.lower() + '@example.invalid', generate_password_hash('viewer-password-123'), 'viewer'))
        response = self.client.post('/api/v1/auth/login', headers={'Authorization': self.admin_headers['Authorization']},
                                    json={'email': self.sku + '@example.invalid', 'password': 'viewer-password-123'})
        headers = dict(self.admin_headers, **{'X-Admin-Session': response.json['token']})
        self.assertEqual(self.client.get('/api/v1/products', headers=headers).status_code, 200)
        self.assertEqual(self.client.post('/api/v1/products', headers=headers, json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/demo/seed', headers=headers, json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/auth/logout', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=headers).status_code, 401)

    def test_product_visibility_validation_and_optimistic_lock(self):
        response = self.client.get('/internal/products', headers=self.read)
        self.assertIn(self.product['ID'], [p['id'] for p in response.json['products']])
        data = {'name': 'Changed', 'sku': self.sku, 'price': '11.00', 'category': 'accessories', 'version': 1}
        self.assertEqual(self.client.patch('/api/v1/products/' + self.product['ID'], headers=self.admin_headers, json=data).status_code, 200)
        self.assertEqual(self.client.patch('/api/v1/products/' + self.product['ID'], headers=self.admin_headers, json=data).status_code, 409)
        self.assertEqual(self.client.post('/api/v1/products', headers=self.admin_headers, json=dict(data, price='NaN')).status_code, 422)
        self.assertEqual(self.client.delete('/api/v1/products/' + self.product['ID'], headers=self.admin_headers, json={'version': 2}).status_code, 200)
        self.assertNotIn(self.product['ID'], [p['id'] for p in self.client.get('/internal/products', headers=self.read).json['products']])

    def test_atomic_reservations_for_last_unit(self):
        self.assertEqual(self.stock(1).status_code, 200)
        def reserve(_):
            with app.test_client() as client:
                return client.post('/internal/orders', headers=self.internal, json=self.order()).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(reserve, range(2)))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(self.stock(-1).status_code, 409)

    def test_idempotent_order_payment_and_stock_consumption(self):
        self.stock(3)
        data = self.order()
        first = self.client.post('/internal/orders', headers=self.internal, json=data)
        second = self.client.post('/internal/orders', headers=self.internal, json=data)
        self.assertEqual(first.json['id'], second.json['id'])
        self.assertEqual(self.client.post('/internal/orders', headers=self.internal, json=dict(data,email='different@example.invalid')).status_code,409)
        path = '/internal/orders/' + first.json['id']
        self.assertEqual(self.client.post(path + '/claim', headers=self.internal).status_code, 200)
        self.assertEqual(self.client.post(path + '/claim', headers=self.internal).status_code, 409)
        self.assertEqual(self.client.post(path + '/payment', headers=self.internal, json={'state': 'paid', 'transaction_id': 'demo-test'}).status_code, 200)
        for _ in range(2):
            self.assertEqual(self.client.post(path + '/confirm', headers=self.internal, json={'tracking_id': 'TEST'}).status_code, 200)
        inventory = self.client.get('/api/v1/inventory?q=' + self.sku, headers=self.admin_headers).json['Items'][0]
        self.assertEqual((inventory['Stock'], inventory['Reserved']), (2, 0))
        self.assertEqual(self.client.patch('/api/v1/orders/' + first.json['id'] + '/status', headers=self.admin_headers, json={'status': 'ENTREGADO'}).status_code, 200)
        self.assertEqual(self.client.patch('/api/v1/orders/' + first.json['id'] + '/status', headers=self.admin_headers, json={'status': 'CANCELADO'}).status_code, 409)
        detail = self.client.get('/api/v1/orders/' + first.json['id'], headers=self.admin_headers).json
        self.assertEqual(detail['Items'][0]['Price'], 'USD 10.25')
        self.assertEqual(detail['Total'], 'USD 11.25')

    def test_failure_releases_but_unknown_payment_keeps_reservation(self):
        self.stock(2)
        for state, expected in [('failed', 2), ('payment_unknown', 1)]:
            order = self.client.post('/internal/orders', headers=self.internal, json=self.order()).json
            path = '/internal/orders/' + order['id']
            self.client.post(path + '/claim', headers=self.internal)
            self.assertEqual(self.client.post(path + '/payment', headers=self.internal, json={'state': state}).status_code, 200)
            available = self.client.get('/api/v1/inventory?q=' + self.sku, headers=self.admin_headers).json['Items'][0]['Stock']
            self.assertEqual(available, expected)

    def test_pagination_filters_and_safe_exports(self):
        response = self.client.get('/api/v1/products?q=' + self.sku + '&page=1&page_size=1', headers=self.admin_headers)
        self.assertEqual((response.json['Total'], len(response.json['Products'])), (1, 1))
        response = self.client.get('/api/v1/exports/products?q=' + self.sku, headers=self.admin_headers)
        self.assertIn("'=Test product", response.text)
        self.assertEqual(self.client.get('/api/v1/orders?from=bad-date', headers=self.admin_headers).status_code, 422)

    def test_dashboard_queries(self):
        response = self.client.get('/api/v1/dashboard', headers=self.admin_headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json['MonthlySales']), 12)
        with pool.connection() as conn:
            count = conn.execute("SELECT count(*) n FROM boutique.orders WHERE state='confirmed'").fetchone()['n']
        self.assertEqual(response.json['Stats'][1]['Value'], str(count))

    def test_manual_reconciliation_requires_verified_stale_payment(self):
        self.stock(1)
        order = self.client.post('/internal/orders', headers=self.internal, json=self.order()).json
        path = '/internal/orders/' + order['id']
        self.client.post(path + '/claim', headers=self.internal)
        data = {'state':'failed', 'verified':True, 'reason':'Verified test payment failed'}
        endpoint = '/api/v1/orders/' + order['id'] + '/reconcile'
        self.assertEqual(self.client.post(endpoint,headers=self.admin_headers,json=data).status_code,409)
        with pool.connection() as conn:
            conn.execute("UPDATE boutique.orders SET updated_at=now()-interval '6 minutes' WHERE id=%s",(order['id'],))
        self.assertEqual(self.client.post(endpoint,headers=self.admin_headers,json=data).status_code,200)
        self.assertEqual(self.client.get('/api/v1/inventory?q='+self.sku,headers=self.admin_headers).json['Items'][0]['Stock'],1)

    def test_existing_multicategory_catalog_can_be_edited(self):
        product = self.client.get('/api/v1/products/66VCHSJNUP',headers=self.admin_headers).json
        data = {'name':product['Name'],'sku':product['SKU'],'price':product['PriceValue'],
                'category':product['Category'],'version':product['Version'],'description':product['Description'],
                'picture':product['ImgURL'],'variant':product['Variant'],'warehouse':product['Location'],'active':True}
        response = self.client.patch('/api/v1/products/66VCHSJNUP',headers=self.admin_headers,json=data)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['Categories'],['clothing','tops'])


    def demo_order(self, key=None, quantity=1):
        return {'key': key or uuid.uuid4().hex, 'customer_name': 'Cliente Demo', 'email': 'demo@example.invalid',
                'shipping_fee': '5.00', 'address': {'streetAddress': 'Demo 123', 'city': 'Demo',
                'state': 'Demo', 'country': 'México', 'zipCode': '64000'},
                'items': [{'product_id': self.product['ID'], 'quantity': quantity}]}

    def test_demo_seed_is_idempotent_and_disabled_by_default(self):
        with patch.dict(os.environ, {'DEMO_DATA_ENABLED': 'false'}):
            self.assertEqual(self.client.post('/api/v1/demo/seed', headers=self.admin_headers, json={}).status_code, 403)
        first = self.client.post('/api/v1/demo/seed', headers=self.admin_headers, json={})
        self.assertEqual(first.status_code, 200, first.json)
        before = self.client.get('/api/v1/inventory?warehouse=Demo%20Central', headers=self.admin_headers).json
        second = self.client.post('/api/v1/demo/seed', headers=self.admin_headers, json={})
        self.assertEqual(second.json, {'ProductsCreated': 0, 'OrdersCreated': 0})
        after = self.client.get('/api/v1/inventory?warehouse=Demo%20Central', headers=self.admin_headers).json
        self.assertEqual(before['Items'], after['Items'])

    def test_demo_creation_cancellation_and_movements(self):
        self.stock(4)
        data = self.demo_order(quantity=2)
        first = self.client.post('/api/v1/demo/orders', headers=self.admin_headers, json=data)
        self.assertEqual(first.status_code, 201, first.json)
        path = '/api/v1/orders/' + first.json['ID']
        second = self.client.post('/api/v1/demo/orders', headers=self.admin_headers, json=data)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json['ID'], second.json['ID'])
        self.assertEqual(first.json['Total'], 'USD 25.50')
        self.assertEqual(first.json['Status'], 'PENDIENTE')
        self.assertEqual(self.client.post('/api/v1/demo/orders', headers=self.admin_headers, json=dict(data, email='other@example.invalid')).status_code, 409)
        self.assertEqual(self.client.get('/api/v1/inventory/' + self.product['ID'], headers=self.admin_headers).json['Item']['Stock'], 2)
        self.assertEqual(self.client.post(path + '/cancel-demo', headers=self.admin_headers, json={'reason': 'Cancelación de prueba'}).status_code, 200)
        self.assertEqual(self.client.post(path + '/cancel-demo', headers=self.admin_headers, json={'reason': 'Reintento'}).status_code, 409)
        detail = self.client.get('/api/v1/inventory/' + self.product['ID'], headers=self.admin_headers).json
        self.assertEqual(detail['Item']['Stock'], 4)
        self.assertEqual([m['Delta'] for m in detail['Movements']], [2, -2, 4])
        self.assertEqual(self.client.get(path, headers=self.admin_headers).json['Status'], 'CANCELADO')
        self.assertEqual(self.client.get('/api/v1/orders?status=CANCELADO&q=' + first.json['ID'], headers=self.admin_headers).json['Total'], 1)

    def test_demo_order_rejects_invalid_quantities_and_partial_stock_changes(self):
        self.stock(4)
        data = self.demo_order()
        for quantity in (0, -1, 1.5, 101, None, True):
            data['items'][0]['quantity'] = quantity
            response = self.client.post('/api/v1/demo/orders', headers=self.admin_headers, json=data)
            self.assertEqual(response.status_code, 422, response.json)
        data = self.demo_order()
        data['items'].append({'product_id': 'zz-missing-product', 'quantity': 1})
        self.assertEqual(self.client.post('/api/v1/demo/orders', headers=self.admin_headers, json=data).status_code, 409)
        self.assertEqual(self.client.get('/api/v1/inventory/' + self.product['ID'], headers=self.admin_headers).json['Item']['Stock'], 4)

    def test_shipment_transitions_versions_and_history(self):
        self.stock(2)
        order = self.client.post('/api/v1/demo/orders', headers=self.admin_headers, json=self.demo_order()).json
        path = '/api/v1/orders/' + order['ID']
        data = {'version': order['Shipment']['Version'], 'status': 'ENVIADO', 'carrier': 'Demo Parcel',
                'tracking_id': 'DEMO-TEST', 'estimated_delivery': '2026-12-10', 'notes': 'Salida de almacén'}
        self.assertEqual(self.client.patch(path + '/shipment', headers=self.admin_headers, json=dict(data, carrier='')).status_code, 422)
        self.assertEqual(self.client.patch(path + '/shipment', headers=self.admin_headers, json=dict(data, status='ENTREGADO')).status_code, 409)
        shipped = self.client.patch(path + '/shipment', headers=self.admin_headers, json=data)
        self.assertEqual(shipped.status_code, 200, shipped.json)
        self.assertEqual(self.client.patch(path + '/shipment', headers=self.admin_headers, json=data).status_code, 409)
        self.assertEqual(self.client.post(path + '/cancel-demo', headers=self.admin_headers, json={'reason': 'Ya enviado'}).status_code, 409)
        data.update(version=shipped.json['Shipment']['Version'], status='INCIDENCIA', notes='Cliente ausente')
        incident = self.client.patch(path + '/shipment', headers=self.admin_headers, json=data)
        self.assertEqual(incident.status_code, 200, incident.json)
        data.update(version=incident.json['Shipment']['Version'], status='ENTREGADO', notes='Recibido por cliente')
        delivered = self.client.patch(path + '/shipment', headers=self.admin_headers, json=data)
        self.assertEqual(delivered.status_code, 200, delivered.json)
        detail = self.client.get(path, headers=self.admin_headers).json
        self.assertEqual([e['Status'] for e in detail['ShipmentEvents']], ['ENTREGADO', 'INCIDENCIA', 'ENVIADO', 'PENDIENTE'])
        self.assertEqual(detail['Shipment']['TrackingID'], 'DEMO-TEST')
        self.assertEqual(detail['Items'][0]['Subtotal'], 'USD 10.25')
        self.assertEqual(self.client.patch(path + '/shipment', headers=self.admin_headers, json=dict(data, version=delivered.json['Shipment']['Version'], status='ENVIADO')).status_code, 409)

    def test_manual_demo_orders_compete_for_last_unit(self):
        self.stock(1)
        def create(_):
            with app.test_client() as client:
                return client.post('/api/v1/demo/orders', headers=self.admin_headers, json=self.demo_order()).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(create, range(2))), [201, 409])

if __name__ == '__main__':
    unittest.main()
