from collections import defaultdict
from datetime import datetime, time, timedelta
import calendar

from odoo import fields, http
from odoo.http import request


class MCDSalesDailyController(http.Controller):
    @http.route('/mcd_kds/sales/daily_products', type='json', auth='user')
    def sales_daily_products(self, period='day', selected_date=None, selected_hour=None, selected_month=None, selected_year=None, demo=False, **kwargs):
        today = fields.Date.context_today(request.env.user)

        if selected_date:
            start_date = fields.Date.from_string(selected_date)
            today = start_date
            if start_date > fields.Date.context_today(request.env.user):
                return []
        elif period == 'custom':
            start_date = today
        elif period == 'week':
            start_date = today - timedelta(days=today.weekday())
        elif period == 'month':
            if selected_month:
                try:
                    year, month = [int(part) for part in selected_month.split('-', 1)]
                    start_date = datetime(year, month, 1).date()
                    today = datetime(year, month, calendar.monthrange(year, month)[1]).date()
                    if today > fields.Date.context_today(request.env.user):
                        today = fields.Date.context_today(request.env.user)
                except Exception:
                    start_date = today.replace(day=1)
            else:
                start_date = today.replace(day=1)
        elif period == 'year':
            if selected_year:
                try:
                    year = int(selected_year)
                    start_date = datetime(year, 1, 1).date()
                    today = datetime(year, 12, 31).date()
                    if today > fields.Date.context_today(request.env.user):
                        today = fields.Date.context_today(request.env.user)
                except Exception:
                    start_date = today.replace(month=1, day=1)
            else:
                start_date = today.replace(month=1, day=1)
        else:
            start_date = today

        if demo:
            return self._demo_sales_daily_products(start_date, today, selected_hour)

        start_dt = datetime.combine(start_date, time.min)
        end_dt = datetime.combine(today, time.max)

        lines = request.env['pos.order.line'].sudo().search([
            ('order_id.date_order', '>=', fields.Datetime.to_string(start_dt)),
            ('order_id.date_order', '<=', fields.Datetime.to_string(end_dt)),
            ('order_id.state', 'in', ['paid', 'done', 'invoiced']),
        ])

        days = defaultdict(lambda: {
            'hours': defaultdict(lambda: defaultdict(lambda: self._empty_product())),
            'products': defaultdict(lambda: self._empty_product()),
        })

        for line in lines:
            order_dt = fields.Datetime.from_string(line.order_id.date_order)
            order_dt = fields.Datetime.context_timestamp(request.env.user, order_dt)
            day_key = order_dt.date().isoformat()
            hour_key = f'{order_dt.hour:02d}:00'
            if selected_hour and hour_key != selected_hour:
                continue
            full_product_name = line.full_product_name if 'full_product_name' in line._fields else ''
            product_key = str(line.product_id.id or line.product_id.display_name or full_product_name or 'product')
            product_name = line.product_id.display_name or full_product_name or 'Sản phẩm'
            qty = line.qty or 0.0
            revenue = line.price_subtotal_incl or line.price_subtotal or 0.0

            hour_product = days[day_key]['hours'][hour_key][product_key]
            self._add_product_sale(hour_product, product_key, product_name, qty, revenue)

            day_product = days[day_key]['products'][product_key]
            self._add_product_sale(day_product, product_key, product_name, qty, revenue)
            hour_bucket = day_product['hours'].setdefault(hour_key, {
                'hour': hour_key,
                'label': hour_key,
                'qty': 0.0,
                'revenue': 0.0,
            })
            hour_bucket['qty'] += qty
            hour_bucket['revenue'] += revenue

        result = []
        for day_key in sorted(days.keys(), reverse=True):
            day = days[day_key]
            hours = []
            for hour_key in sorted(day['hours'].keys()):
                products = self._sorted_products(day['hours'][hour_key].values())
                hours.append({
                    'hour': hour_key,
                    'label': hour_key,
                    'products': products,
                    'total_qty': round(sum(item['qty'] for item in products), 2),
                    'total_revenue': round(sum(item['revenue'] for item in products), 2),
                })

            products = self._sorted_products(day['products'].values())
            for product in products:
                product['hours'] = sorted(
                    [{
                        'hour': item['hour'],
                        'label': item['label'],
                        'qty': round(item['qty'], 2),
                        'revenue': round(item['revenue'], 2),
                    } for item in product.get('hours', {}).values()],
                    key=lambda item: item['hour'],
                )
                peak = max(product['hours'], key=lambda item: item['qty'], default=None)
                product['peak_hour'] = peak['label'] if peak else '-'

            result.append({
                'date': day_key,
                'label': datetime.strptime(day_key, '%Y-%m-%d').strftime('%d/%m'),
                'hours': hours,
                'products': products,
                'total_qty': round(sum(hour['total_qty'] for hour in hours), 2),
                'total_revenue': round(sum(hour['total_revenue'] for hour in hours), 2),
            })

        return result

    def _demo_sales_daily_products(self, start_date, end_date, selected_hour=None):
        products = [
            ('Big Mac', 62000),
            ('McChicken', 52000),
            ('Cheeseburger', 42000),
            ('French Fries', 29000),
            ('Coca-Cola', 18000),
            ('McNuggets 6pcs', 49000),
            ('Filet-O-Fish', 55000),
            ('McFlurry Oreo', 39000),
        ]
        hour_weights = {
            9: 2, 10: 3, 11: 7, 12: 10, 13: 8, 14: 4,
            15: 3, 16: 4, 17: 7, 18: 10, 19: 9, 20: 6, 21: 3,
        }
        result = []
        current = end_date
        while current >= start_date:
            day_seed = current.toordinal()
            day_products = defaultdict(lambda: self._empty_product())
            hours = []

            for hour, weight in hour_weights.items():
                hour_key = f'{hour:02d}:00'
                if selected_hour and hour_key != selected_hour:
                    continue

                hour_products = []
                for index, (name, price) in enumerate(products):
                    qty = ((day_seed + hour + index * 3) % 5) + 1
                    if index > 4 and (day_seed + hour + index) % 3 == 0:
                        qty = 0
                    qty = round(qty * weight / 4, 2)
                    if qty <= 0:
                        continue
                    revenue = round(qty * price, 2)
                    product_key = f'demo-{index}'
                    item = self._empty_product()
                    self._add_product_sale(item, product_key, name, qty, revenue)
                    hour_products.append({
                        'key': item['key'],
                        'name': item['name'],
                        'qty': round(item['qty'], 2),
                        'revenue': round(item['revenue'], 2),
                        'hours': {},
                    })

                    day_product = day_products[product_key]
                    self._add_product_sale(day_product, product_key, name, qty, revenue)
                    day_product['hours'][hour_key] = {
                        'hour': hour_key,
                        'label': hour_key,
                        'qty': round(qty, 2),
                        'revenue': revenue,
                    }

                hour_products = sorted(hour_products, key=lambda item: item['qty'], reverse=True)
                hours.append({
                    'hour': hour_key,
                    'label': hour_key,
                    'products': hour_products,
                    'total_qty': round(sum(item['qty'] for item in hour_products), 2),
                    'total_revenue': round(sum(item['revenue'] for item in hour_products), 2),
                })

            products_by_day = self._sorted_products(day_products.values())
            for product in products_by_day:
                product['hours'] = sorted(
                    [{
                        'hour': item['hour'],
                        'label': item['label'],
                        'qty': round(item['qty'], 2),
                        'revenue': round(item['revenue'], 2),
                    } for item in product.get('hours', {}).values()],
                    key=lambda item: item['hour'],
                )
                peak = max(product['hours'], key=lambda item: item['qty'], default=None)
                product['peak_hour'] = peak['label'] if peak else '-'

            result.append({
                'date': current.isoformat(),
                'label': current.strftime('%d/%m'),
                'hours': hours,
                'products': products_by_day,
                'total_qty': round(sum(hour['total_qty'] for hour in hours), 2),
                'total_revenue': round(sum(hour['total_revenue'] for hour in hours), 2),
            })
            current -= timedelta(days=1)

        return result

    def _empty_product(self):
        return {
            'key': '',
            'name': '',
            'qty': 0.0,
            'revenue': 0.0,
            'hours': {},
        }

    def _add_product_sale(self, item, product_key, product_name, qty, revenue):
        item['key'] = str(product_key)
        item['name'] = product_name
        item['qty'] += qty
        item['revenue'] += revenue

    def _sorted_products(self, products):
        return sorted(
            [{
                'key': item['key'],
                'name': item['name'],
                'qty': round(item['qty'], 2),
                'revenue': round(item['revenue'], 2),
                'hours': item.get('hours', {}),
            } for item in products],
            key=lambda item: item['qty'],
            reverse=True,
        )
