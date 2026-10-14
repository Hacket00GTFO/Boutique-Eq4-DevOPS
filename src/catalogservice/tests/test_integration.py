"""Real PostgreSQL contract/transaction tests. Use only the isolated test DB."""
import concurrent.futures
import os
import unittest
import uuid
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


if __name__ == '__main__':
    unittest.main()
