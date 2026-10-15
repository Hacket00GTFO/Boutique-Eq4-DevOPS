import csv
import io
from flask import Response, abort, request
from auth import admin
from catalog import filtered_products, money
from orders import orders_page, order_view
from db import pool


def register_reports(app):
    @app.get('/api/v1/dashboard')
    @admin()
    def dashboard():
        with pool.connection() as conn:
            stats = conn.execute("""SELECT coalesce(sum(total_usd_nanos),0) revenue,count(*) orders,count(DISTINCT email) customers
                                    FROM boutique.orders WHERE state='confirmed'""").fetchone()
            months = conn.execute("""SELECT extract(month FROM created_at)::int AS month_number,sum(total_usd_nanos) revenue FROM boutique.orders
                                     WHERE state='confirmed' AND extract(year FROM created_at)=extract(year FROM now()) GROUP BY 1""").fetchall()
            top = conn.execute("""SELECT i.product_id,i.name,i.sku,sum(i.quantity)::int sales,max(p.picture) picture FROM boutique.order_items i
                                  JOIN boutique.orders o ON o.id=i.order_id JOIN boutique.products p ON p.id=i.product_id
                                  WHERE o.state='confirmed' GROUP BY 1,2,3 ORDER BY sales DESC LIMIT 5""").fetchall()
            recent = conn.execute('SELECT * FROM boutique.orders ORDER BY created_at DESC LIMIT 5').fetchall()
        values = [0] * 12
        for m in months:
            values[m['month_number'] - 1] = float(m['revenue'] / 1000000000) # visualization only; ledger remains exact numeric
        return {'Stats': [{'Label': 'VENTAS CONFIRMADAS', 'Value': money(stats['revenue']), 'Icon': 'payments'},
                          {'Label': 'PEDIDOS CONFIRMADOS', 'Value': str(stats['orders']), 'Icon': 'assignment'},
                          {'Label': 'CLIENTES CON COMPRA', 'Value': str(stats['customers']), 'Icon': 'group_add'}],
                'MonthlySales': values, 'TopProducts': [{'Name': p['name'], 'SKU': p['sku'], 'Sales': p['sales'], 'ImgURL': p['picture']} for p in top],
                'RecentOrders': [order_view(o) for o in recent]}

    @app.get('/api/v1/exports/<resource>')
    @admin()
    def export(resource):
        if resource == 'products':
            rows, columns = filtered_products(all_rows=True)['Products'], ['ID', 'SKU', 'Name', 'Category', 'Price', 'Status']
        elif resource == 'inventory':
            rows, columns = filtered_products(True, True)['Items'], ['SKU', 'Product', 'Variant', 'Stock', 'Reserved', 'Location']
        elif resource == 'orders':
            rows, columns = orders_page(True)['Orders'], ['ID', 'Date', 'Customer', 'Total', 'Status', 'State']
        else:
            abort(404)
        if len(rows) > 10000:
            abort(422, 'Exportación demasiado grande; acota los filtros')
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for row in rows:
            # Neutralize spreadsheet formula injection in user-controlled text.
            writer.writerow([("'" + str(row[c])) if str(row[c]).startswith(('=', '+', '-', '@', '\t', '\r')) else row[c] for c in columns])
        return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="{resource}.csv"'})
