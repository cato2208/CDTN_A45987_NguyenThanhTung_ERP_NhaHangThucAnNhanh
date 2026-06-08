from collections import defaultdict
from datetime import datetime, time, timedelta

from odoo import fields, http
from odoo.http import request


class MCDManagerFilteredController(http.Controller):
    @http.route('/mcd_kds/dashboard/by_date', type='json', auth='user')
    def dashboard_by_date(self, selected_date=None, selected_hour=None, **kwargs):
        if not selected_date:
            return self._empty_dashboard()

        day = fields.Date.from_string(selected_date)
        if day > fields.Date.context_today(request.env.user):
            return self._empty_dashboard()

        start_dt = datetime.combine(day, time.min)
        end_dt = datetime.combine(day, time.max)
        prev_start_dt = start_dt - timedelta(days=1)
        prev_end_dt = start_dt - timedelta(seconds=1)

        orders = request.env['pos.order'].sudo().search([
            ('date_order', '>=', fields.Datetime.to_string(start_dt)),
            ('date_order', '<=', fields.Datetime.to_string(end_dt)),
            ('state', 'in', ['paid', 'done', 'invoiced']),
        ])
        previous_orders = request.env['pos.order'].sudo().search([
            ('date_order', '>=', fields.Datetime.to_string(prev_start_dt)),
            ('date_order', '<=', fields.Datetime.to_string(prev_end_dt)),
            ('state', 'in', ['paid', 'done', 'invoiced']),
        ])

        if selected_hour:
            hour_int = int(selected_hour.split(':')[0])
            orders = orders.filtered(lambda order: fields.Datetime.context_timestamp(
                request.env.user,
                fields.Datetime.from_string(order.date_order),
            ).hour == hour_int)
            previous_orders = previous_orders.filtered(lambda order: fields.Datetime.context_timestamp(
                request.env.user,
                fields.Datetime.from_string(order.date_order),
            ).hour == hour_int)

        total_orders = len(orders)
        total_revenue = sum(orders.mapped('amount_total'))
        avg_order_value = total_revenue / total_orders if total_orders else 0

        previous_total_orders = len(previous_orders)
        previous_total_revenue = sum(previous_orders.mapped('amount_total'))
        previous_avg_order_value = previous_total_revenue / previous_total_orders if previous_total_orders else 0

        def pct_change(current, previous):
            if previous == 0:
                return None if current == 0 else 100.0
            return round((current - previous) / previous * 100, 1)

        revenue_growth = pct_change(total_revenue, previous_total_revenue)
        orders_growth = pct_change(total_orders, previous_total_orders)
        avg_order_value_growth = pct_change(avg_order_value, previous_avg_order_value)

        hour_revenue = defaultdict(float)
        hour_orders = defaultdict(int)
        for order in orders:
            order_dt = fields.Datetime.context_timestamp(
                request.env.user,
                fields.Datetime.from_string(order.date_order),
            )
            hour_label = f'{order_dt.hour:02d}h'
            hour_revenue[hour_label] += order.amount_total or 0
            hour_orders[hour_label] += 1

        chart_hours = [f'{hour:02d}h' for hour in range(9, 23)]
        if selected_hour:
            chart_hours = [selected_hour.replace(':00', 'h')]
        chart_values = [round(hour_revenue.get(hour, 0) / 1000000, 2) for hour in chart_hours]
        peak_hour = max(hour_orders.items(), key=lambda item: item[1])[0] if hour_orders else '-'

        lines = request.env['pos.order.line'].sudo().search([('order_id', 'in', orders.ids)])
        product_qty = defaultdict(float)
        for line in lines:
            full_product_name = line.full_product_name if 'full_product_name' in line._fields else ''
            product_qty[line.product_id.display_name or full_product_name or 'Sản phẩm'] += line.qty or 0

        top_products = sorted(
            [{'name': name, 'qty': round(qty)} for name, qty in product_qty.items()],
            key=lambda item: item['qty'],
            reverse=True,
        )[:8]

        kitchen_done = request.env['kitchen.order'].sudo().search([
            ('state', '=', 'done'),
            ('serve_time', '>=', fields.Datetime.to_string(start_dt)),
            ('serve_time', '<=', fields.Datetime.to_string(end_dt)),
        ])
        kitchen_durations = [int((o.serve_time - o.order_time).total_seconds())
                             for o in kitchen_done if o.serve_time and o.order_time]
        kitchen_avg = round(sum(kitchen_durations) / len(kitchen_durations)) if kitchen_durations else 0

        expo_done = request.env['expo.order'].sudo().search([
            ('state', '=', 'done'),
            ('serve_time', '>=', fields.Datetime.to_string(start_dt)),
            ('serve_time', '<=', fields.Datetime.to_string(end_dt)),
        ])
        expo_durations = [int((o.serve_time - o.order_time).total_seconds())
                          for o in expo_done if o.serve_time and o.order_time]
        expo_avg = round(sum(expo_durations) / len(expo_durations)) if expo_durations else 0

        return {
            'total_orders': total_orders,
            'total_revenue': total_revenue,
            'kitchen_avg_duration': kitchen_avg,
            'expo_avg_duration': expo_avg,
            'peak_hour': peak_hour,
            'avg_order_value': avg_order_value,
            'previous_total_orders': previous_total_orders,
            'previous_total_revenue': previous_total_revenue,
            'previous_avg_order_value': previous_avg_order_value,
            'revenue_growth': revenue_growth,
            'orders_growth': orders_growth,
            'avg_order_value_growth': avg_order_value_growth,
            'chart_revenue': {
                'labels': chart_hours,
                'values': chart_values,
            },
            'top_products': top_products,
        }

    def _empty_dashboard(self):
        return {
            'total_orders': 0,
            'total_revenue': 0,
            'kitchen_avg_duration': 0,
            'expo_avg_duration': 0,
            'peak_hour': '-',
            'avg_order_value': 0,
            'chart_revenue': {
                'labels': [f'{hour:02d}h' for hour in range(9, 23)],
                'values': [0 for _ in range(9, 23)],
            },
            'top_products': [],
        }
