import json
import calendar
import re
from collections import defaultdict
from datetime import datetime, timedelta, time

import pytz

from odoo import fields, http
from odoo.http import request

# Giờ hoat đơng: 9:00 - 22:00
OPEN_HOUR  = 9
CLOSE_HOUR = 22
KITCHEN_SLA_SECONDS = 5 * 60
EXPO_SLA_SECONDS = 3 * 60


def _pct(part, total):
    return round((part / total * 100) if total else 0, 1)


def _pct_change(current, previous):
    if previous == 0:
        return None if current == 0 else 100.0
    return round((current - previous) / previous * 100, 1)


def _previous_range(df_utc, dt_utc, tz, period=None, selected_date=None):
    local_from = pytz.utc.localize(df_utc).astimezone(tz)
    local_to = pytz.utc.localize(dt_utc).astimezone(tz)
    if selected_date or period in ('day', 'custom'):
        prev_from = local_from - timedelta(days=1)
        prev_to = local_to - timedelta(days=1)
        return (
            prev_from.astimezone(pytz.utc).replace(tzinfo=None),
            prev_to.astimezone(pytz.utc).replace(tzinfo=None),
        )
    if period == 'week':
        prev_from = local_from - timedelta(days=7)
        prev_to = local_to - timedelta(days=7)
        return (
            prev_from.astimezone(pytz.utc).replace(tzinfo=None),
            prev_to.astimezone(pytz.utc).replace(tzinfo=None),
        )
    if period == 'month':
        first_this_month = local_from.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_last = first_this_month - timedelta(microseconds=1)
        prev_from = prev_month_last.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_to = prev_month_last.replace(hour=23, minute=59, second=59, microsecond=999999)
        return (
            prev_from.astimezone(pytz.utc).replace(tzinfo=None),
            prev_to.astimezone(pytz.utc).replace(tzinfo=None),
        )
    if period == 'year':
        prev_year = local_from.year - 1
        prev_from = tz.localize(datetime.combine(datetime(prev_year, 1, 1).date(), time.min))
        prev_to = tz.localize(datetime.combine(datetime(prev_year, 12, 31).date(), time.max))
        return (
            prev_from.astimezone(pytz.utc).replace(tzinfo=None),
            prev_to.astimezone(pytz.utc).replace(tzinfo=None),
        )
    duration = local_to - local_from
    prev_to = local_from - timedelta(microseconds=1)
    prev_from = prev_to - duration
    return (
        prev_from.astimezone(pytz.utc).replace(tzinfo=None),
        prev_to.astimezone(pytz.utc).replace(tzinfo=None),
    )

def _period_compare_label(period, selected_date=None):
    if selected_date or period in ('day', 'custom'):
        return u'ngày trước'
    if period == 'week':
        return u'kỳ 7 ngày trước'
    if period == 'month':
        return u'tháng trước'
    if period == 'year':
        return u'năm trước'
    return u'kỳ trước'
def _sla_stats(durations, sla_seconds):
    total = len(durations)
    late = len([d for d in durations if d > sla_seconds])
    return {
        'rate': round(((total - late) / total * 100) if total else 100, 1),
        'late': late,
    }


def _duration_split(durations, sla_seconds):
    total = len(durations)
    ok_count = len([d for d in durations if d <= sla_seconds])
    late_count = total - ok_count
    return [
        {'label': 'Đúng hạn', 'count': ok_count, 'pct': _pct(ok_count, total)},
        {'label': 'Qua hn', 'count': late_count, 'pct': _pct(late_count, total)},
    ]


def _aging_buckets(orders, now_utc, specs):
    buckets = [{'label': label, 'count': 0, 'pct': 0} for label, _min, _max in specs]
    for order in orders:
        if not order.order_time:
            continue
        waited = max(0, int((now_utc - order.order_time).total_seconds()))
        for idx, (_label, min_secs, max_secs) in enumerate(specs):
            if waited >= min_secs and (max_secs is None or waited < max_secs):
                buckets[idx]['count'] += 1
                break
    total = sum(b['count'] for b in buckets)
    for bucket in buckets:
        bucket['pct'] = _pct(bucket['count'], total)
    return buckets


def _speed_by_product(lines, sla_seconds):
    product_speed = defaultdict(lambda: {
        'name': '',
        'qty': 0.0,
        'count': 0,
        'total_duration': 0,
        'late_count': 0,
    })
    for line in lines:
        if _skip_kitchen_metric_line(line):
            continue
        order = line.order_id
        if not order.serve_time or not order.order_time:
            continue
        duration = int((order.serve_time - order.order_time).total_seconds())
        if duration <= 0 or duration > 1800:
            continue
        key = line.product_id.id or line.product_name
        item = product_speed[key]
        item['name'] = line.product_name or line.product_id.display_name
        item['qty'] += line.qty or 0
        item['count'] += 1
        item['total_duration'] += duration
        if duration > sla_seconds:
            item['late_count'] += 1

    rows = []
    for item in product_speed.values():
        if not item['count']:
            continue
        rows.append({
            'name': item['name'],
            'qty': round(item['qty'], 1),
            'avg_duration': round(item['total_duration'] / item['count']),
            'late_count': item['late_count'],
        })
    return sorted(rows, key=lambda x: (x['avg_duration'], x['late_count']), reverse=True)[:6]


def _skip_kitchen_metric_line(line):
    product = line.product_id
    name = ((line.product_name or '') or (product.display_name if product else '')).lower()
    default_code = ((product.default_code if product else '') or '').upper()
    category = ((product.categ_id.complete_name if product and product.categ_id else '') or '').lower()
    return (
        'gift card' in name
        or 'thẻ quà' in name
        or 'the qua' in name
        or 'gift card' in category
        or default_code.startswith('MCD-DEMO-')
    )


def _pos_service_mix(env, df_utc, dt_utc):
    orders = env['pos.order'].sudo().search([
        ('state', 'in', ['paid', 'done', 'invoiced']),
        ('date_order', '>=', df_utc),
        ('date_order', '<=', dt_utc),
    ])
    eat_in = 0
    take_out = 0
    for order in orders:
        service_type = (
            getattr(order, 'mcd_service_type', False)
            or getattr(order, 'service_type', False)
            or 'eat_in'
        )
        if service_type == 'take_out':
            take_out += 1
        else:
            eat_in += 1
    return eat_in, take_out


def _get_date_range(period, tz, selected_date=None, selected_month=None, selected_year=None):
    now = datetime.now(tz)
    if selected_date:
        try:
            day = datetime.strptime(selected_date, '%Y-%m-%d').date()
            if day > now.date():
                day = now.date()
            date_from = tz.localize(datetime.combine(day, time.min))
            date_to = tz.localize(datetime.combine(day, time.max))
            if date_to > now:
                date_to = now
            df_utc = date_from.astimezone(pytz.utc).replace(tzinfo=None)
            dt_utc = date_to.astimezone(pytz.utc).replace(tzinfo=None)
            return df_utc, dt_utc, now, tz
        except Exception:
            pass
    if period == 'month' and selected_month:
        try:
            year, month = [int(part) for part in selected_month.split('-', 1)]
            month = max(1, min(month, 12))
            first_day = datetime(year, month, 1).date()
            last_day = datetime(year, month, calendar.monthrange(year, month)[1]).date()
            date_from = tz.localize(datetime.combine(first_day, time.min))
            date_to = tz.localize(datetime.combine(last_day, time.max))
            if date_to > now:
                date_to = now
            df_utc = date_from.astimezone(pytz.utc).replace(tzinfo=None)
            dt_utc = date_to.astimezone(pytz.utc).replace(tzinfo=None)
            return df_utc, dt_utc, now, tz
        except Exception:
            pass
    if period == 'year' and selected_year:
        try:
            year = int(selected_year)
            date_from = tz.localize(datetime.combine(datetime(year, 1, 1).date(), time.min))
            date_to = tz.localize(datetime.combine(datetime(year, 12, 31).date(), time.max))
            if date_to > now:
                date_to = now
            df_utc = date_from.astimezone(pytz.utc).replace(tzinfo=None)
            dt_utc = date_to.astimezone(pytz.utc).replace(tzinfo=None)
            return df_utc, dt_utc, now, tz
        except Exception:
            pass
    if period in ('day', 'custom'):
        date_from = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        date_from = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
    elif period == 'year':
        date_from = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        date_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    df_utc = date_from.astimezone(pytz.utc).replace(tzinfo=None)
    dt_utc = now.astimezone(pytz.utc).replace(tzinfo=None)
    return df_utc, dt_utc, now, tz


def _range_ends_today(dt_utc, tz):
    if not dt_utc:
        return False
    local_to = pytz.utc.localize(dt_utc).astimezone(tz)
    return local_to.date() == datetime.now(tz).date()


def _business_hours_filter(orders_iter, tz, date_field='date_order'):
    """Lọc đơn hàng chỉ trong giờ hoạt động 9h-22h (local time)."""
    result = []
    for order in orders_iter:
        raw = getattr(order, date_field, None)
        if not raw:
            continue
        local_dt = raw.replace(tzinfo=pytz.utc).astimezone(tz)
        if OPEN_HOUR <= local_dt.hour <= CLOSE_HOUR:
            result.append(order)
    return result


def _period_bucket_labels(period, df_utc, dt_utc, tz):
    local_from = pytz.utc.localize(df_utc).astimezone(tz)
    local_to = pytz.utc.localize(dt_utc).astimezone(tz)
    if period in ('day', 'custom'):
        return [f'{h:02d}h' for h in range(OPEN_HOUR, CLOSE_HOUR + 1)]
    if period == 'week':
        return ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN']
    if period == 'year':
        return [f'T{month:02d}' for month in range(1, 13)]
    return [str(day) for day in range(1, local_to.day + 1)]


def _period_bucket_key(period, local_dt, labels):
    if period in ('day', 'custom'):
        return f'{local_dt.hour:02d}h'
    if period == 'week':
        return labels[local_dt.weekday()]
    if period == 'year':
        return f'T{local_dt.month:02d}'
    return str(local_dt.day)


class KDSDashboardController(http.Controller):

    def _get_product_vendor(self, product):
        sellers = product.seller_ids.filtered(
            lambda seller: not seller.company_id or seller.company_id == request.env.company
        )
        if not sellers:
            sellers = product.seller_ids
        return sellers[:1]

    def _pos_stats(self, env, tz, df_utc, dt_utc):
        pos_orders_all = env['pos.order'].sudo().search([
            ('state', 'in', ['paid', 'done', 'invoiced']),
            ('date_order', '>=', df_utc),
            ('date_order', '<=', dt_utc),
        ])
        pos_orders = _business_hours_filter(pos_orders_all, tz, 'date_order')
        lines = env['pos.order.line'].sudo().search([
            ('order_id', 'in', [order.id for order in pos_orders]),
        ]) if pos_orders else env['pos.order.line'].sudo().browse([])
        total_revenue = sum(order.amount_total for order in pos_orders)
        total_orders = len(pos_orders)
        total_items = sum(line.qty for line in lines)
        return {
            'orders': pos_orders,
            'lines': lines,
            'total_revenue': total_revenue,
            'total_orders': total_orders,
            'total_items': total_items,
            'avg_order_value': round(total_revenue / total_orders) if total_orders else 0,
        }

    def _period_bucket_sql_expr(self, period, alias='o'):
        local_expr = "timezone(%s, timezone('UTC', %s.date_order))" % ('%s', alias)
        if period in ('day', 'custom'):
            return "to_char(%s, 'HH24') || 'h'" % local_expr
        if period == 'week':
            return """case extract(isodow from %s)::int
                when 1 then 'T2' when 2 then 'T3' when 3 then 'T4'
                when 4 then 'T5' when 5 then 'T6' when 6 then 'T7'
                else 'CN' end""" % local_expr
        if period == 'year':
            return "'T' || to_char(%s, 'MM')" % local_expr
        return "extract(day from %s)::int::text" % local_expr

    def _pos_order_rows_sql(self, env, tz, df_utc, dt_utc, period):
        bucket_expr = self._period_bucket_sql_expr(period, 'o')
        query = """
            select o.id,
                   o.date_order,
                   o.amount_total,
                   o.partner_id,
                   %s as bucket
              from pos_order o
             where o.state in ('paid', 'done', 'invoiced')
               and o.date_order >= %%s
               and o.date_order <= %%s
               and extract(hour from timezone(%%s, timezone('UTC', o.date_order))) >= %%s
               and extract(hour from timezone(%%s, timezone('UTC', o.date_order))) <= %%s
        """ % bucket_expr
        env.cr.execute(query, [tz.zone, df_utc, dt_utc, tz.zone, OPEN_HOUR, tz.zone, CLOSE_HOUR])
        return [
            {
                'id': row[0],
                'date_order': row[1],
                'amount_total': row[2] or 0.0,
                'partner_id': row[3],
                'bucket': row[4],
            }
            for row in env.cr.fetchall()
        ]

    def _pos_line_bucket_rows_sql(self, env, tz, df_utc, dt_utc, period):
        bucket_expr = self._period_bucket_sql_expr(period, 'o')
        query = """
            select l.product_id,
                   %s as bucket,
                   sum(l.qty) as qty,
                   sum(l.price_subtotal_incl) as revenue
              from pos_order_line l
              join pos_order o on o.id = l.order_id
             where o.state in ('paid', 'done', 'invoiced')
               and o.date_order >= %%s
               and o.date_order <= %%s
               and extract(hour from timezone(%%s, timezone('UTC', o.date_order))) >= %%s
               and extract(hour from timezone(%%s, timezone('UTC', o.date_order))) <= %%s
             group by l.product_id, bucket
        """ % bucket_expr
        env.cr.execute(query, [tz.zone, df_utc, dt_utc, tz.zone, OPEN_HOUR, tz.zone, CLOSE_HOUR])
        return [
            {
                'product_id': row[0],
                'bucket': row[1],
                'qty': row[2] or 0.0,
                'revenue': row[3] or 0.0,
            }
            for row in env.cr.fetchall()
            if row[0]
        ]

    def _pos_stats_sql(self, env, tz, df_utc, dt_utc, period):
        order_rows = self._pos_order_rows_sql(env, tz, df_utc, dt_utc, period)
        line_bucket_rows = self._pos_line_bucket_rows_sql(env, tz, df_utc, dt_utc, period)
        product_rows = {}
        total_items = 0.0
        for row in line_bucket_rows:
            pid = row['product_id']
            item = product_rows.setdefault(pid, {'product_id': pid, 'qty': 0.0, 'revenue': 0.0})
            item['qty'] += row['qty']
            item['revenue'] += row['revenue']
            total_items += row['qty']
        total_revenue = sum(row['amount_total'] for row in order_rows)
        total_orders = len(order_rows)
        return {
            'order_rows': order_rows,
            'line_bucket_rows': line_bucket_rows,
            'product_rows': list(product_rows.values()),
            'total_revenue': total_revenue,
            'total_orders': total_orders,
            'total_items': total_items,
            'avg_order_value': round(total_revenue / total_orders) if total_orders else 0,
        }

    def _find_bom(self, env, product, cache=None):
        if not product or 'mrp.bom' not in env.registry:
            return False
        if cache is not None and product.id in cache:
            return cache[product.id]
        company_domain = ['|', ('company_id', '=', False), ('company_id', '=', env.company.id)]
        Bom = env['mrp.bom'].sudo()
        bom = Bom.search([
            ('product_id', '=', product.id),
            ('type', 'in', ['normal', 'phantom']),
            *company_domain,
        ], limit=1)
        if not bom:
            bom = Bom.search([
                ('product_id', '=', False),
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
                ('type', 'in', ['normal', 'phantom']),
                *company_domain,
            ], limit=1)
        if cache is not None:
            cache[product.id] = bom
        return bom

    def _modifier_selections(self, line):
        if 'modifier_json' not in line._fields or not line.modifier_json:
            return {}
        try:
            data = json.loads(line.modifier_json) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _is_drink_product(self, product):
        text = ' '.join([
            product.display_name or '',
            product.categ_id.complete_name or '',
            product.categ_id.name or '',
        ]).lower()
        return any(token in text for token in ('drink', 'coke', 'sprite', 'fanta', 'cola', 'nước', 'nuoc'))

    def _clean_product_modifier_label(self, label, product=None):
        label = re.sub(r'\s+', ' ', (label or '').replace('→', '').strip())
        if not label:
            return None
        lower = label.lower()
        fixed_terms = ('bun', 'buns', 'cup', 'bag', 'wrapper', 'hộp', 'hop', 'ly ')
        drop_terms = (
            '#source', ' source', 'kiosk', 'pos', 'eat in', 'take out',
            'large combo', 'medium combo', 'small combo', 'combo',
            'service', 'mang di', 'ăn tại', 'an tai',
        )
        drop_exact = {'s', 'm', 'l', 'r', 'size s', 'size m', 'size l', 'size r'}
        if lower in drop_exact or any(term in lower for term in drop_terms):
            return None
        if any(term in lower for term in fixed_terms):
            return None
        if product and not self._is_drink_product(product) and ('ice' in lower or 'đá' in lower or 'da' == lower):
            return None

        aliases = {
            'no onion': 'Bỏ hành tây',
            'without onion': 'Bỏ hành tây',
            'no pickle': 'Bỏ dưa chuột muối',
            'without pickle': 'Bỏ dưa chuột muối',
            'no lettuce': 'Bỏ xà lách',
            'extra cheese': 'Thêm phô mai',
            'extra sauce': 'Thêm sốt',
            'less ice': 'Ít đá',
            'no ice': 'Không đá',
        }
        return aliases.get(lower, label)

    def _modifier_label_from_bom_action(self, env, bom_line_id, action):
        action_labels = {
            'remove': 'Bỏ',
            'extra': 'Thêm',
            'only': 'Chỉ',
        }
        try:
            bom_line = env['mrp.bom.line'].sudo().browse(int(bom_line_id))
        except Exception:
            bom_line = env['mrp.bom.line'].sudo().browse([])
        material_name = bom_line.product_id.display_name if bom_line and bom_line.exists() else ''
        prefix = action_labels.get(action, action or '')
        return ('%s %s' % (prefix, material_name)).strip()

    def _line_bom_usage(self, env, line, raw_category_ids=None, bom_cache=None):
        product = line.product_id
        sale_qty = line.qty or 0.0
        if not product or sale_qty <= 0:
            return [], 0.0
        bom = self._find_bom(env, product, bom_cache)
        if not bom:
            return [], sale_qty * (product.standard_price or 0.0)

        selections = self._modifier_selections(line)
        only_bom_line_id = next(
            (line_id for line_id, action in selections.items() if action == 'only'),
            None
        )
        finished_factor = sale_qty / (bom.product_qty or 1.0)
        usage_rows = []
        total_cost = 0.0
        for bom_line in bom.bom_line_ids:
            material = bom_line.product_id
            if not material or (raw_category_ids and material.categ_id.id not in raw_category_ids):
                continue
            action = selections.get(str(bom_line.id))
            if only_bom_line_id:
                multiplier = 1 if str(bom_line.id) == only_bom_line_id else 0
            elif action == 'remove':
                multiplier = 0
            elif action == 'extra':
                multiplier = 2
            else:
                multiplier = 1
            raw_qty = finished_factor * (bom_line.product_qty or 0.0) * multiplier
            if raw_qty <= 0:
                continue
            try:
                consumed_qty = bom_line.product_uom_id._compute_quantity(raw_qty, material.uom_id)
                uom_name = material.uom_id.name
            except Exception:
                consumed_qty = raw_qty
                uom_name = bom_line.product_uom_id.name or material.uom_id.name
            value = consumed_qty * (material.standard_price or 0.0)
            total_cost += value
            usage_rows.append({
                'product_id': material.id,
                'name': material.display_name,
                'qty': consumed_qty,
                'uom': uom_name,
                'value': value,
                'category': material.categ_id.complete_name,
            })
        return usage_rows, total_cost

    def _profitability_summary(self, env, lines, total_revenue):
        bom_cache = {}
        total_cost = 0.0
        product_map = defaultdict(lambda: {
            'name': '',
            'revenue': 0.0,
            'cost': 0.0,
            'qty': 0.0,
        })
        for line in lines:
            revenue = line.price_subtotal_incl or 0.0
            _usage_rows, line_cost = self._line_bom_usage(env, line, bom_cache=bom_cache)
            total_cost += line_cost
            item = product_map[line.product_id.id]
            item['name'] = line.product_id.display_name
            item['revenue'] += revenue
            item['cost'] += line_cost
            item['qty'] += line.qty or 0.0

        gross_profit = total_revenue - total_cost
        food_cost_rate = _pct(total_cost, total_revenue)
        gross_margin = _pct(gross_profit, total_revenue)
        low_margin_products = []
        for item in product_map.values():
            if item['revenue'] <= 0:
                continue
            item_profit = item['revenue'] - item['cost']
            item['profit'] = round(item_profit)
            item['food_cost_rate'] = _pct(item['cost'], item['revenue'])
            item['gross_margin'] = _pct(item_profit, item['revenue'])
            item['revenue'] = round(item['revenue'])
            item['cost'] = round(item['cost'])
            item['qty'] = round(item['qty'], 1)
            low_margin_products.append(item)
        low_margin_products = sorted(
            low_margin_products,
            key=lambda row: (row['gross_margin'], -row['revenue']),
        )[:5]
        return {
            'food_cost': round(total_cost),
            'gross_profit': round(gross_profit),
            'food_cost_rate': food_cost_rate,
            'gross_margin': gross_margin,
            'low_margin_products': low_margin_products,
        }

    def _product_cost_from_bom(self, env, product, qty, bom_cache=None):
        if not product or qty <= 0:
            return 0.0
        bom = self._find_bom(env, product, bom_cache)
        if not bom:
            return qty * (product.standard_price or 0.0)
        factor = qty / (bom.product_qty or 1.0)
        total_cost = 0.0
        for bom_line in bom.bom_line_ids:
            material = bom_line.product_id
            if not material:
                continue
            raw_qty = factor * (bom_line.product_qty or 0.0)
            if raw_qty <= 0:
                continue
            try:
                consumed_qty = bom_line.product_uom_id._compute_quantity(raw_qty, material.uom_id)
            except Exception:
                consumed_qty = raw_qty
            total_cost += consumed_qty * (material.standard_price or 0.0)
        return total_cost

    def _profitability_summary_from_product_rows(self, env, product_rows, total_revenue, products_by_id):
        bom_cache = {}
        total_cost = 0.0
        low_margin_products = []
        for row in product_rows:
            product = products_by_id.get(row['product_id'])
            if not product:
                continue
            revenue = row.get('revenue') or 0.0
            qty = row.get('qty') or 0.0
            line_cost = self._product_cost_from_bom(env, product, qty, bom_cache)
            total_cost += line_cost
            if revenue <= 0:
                continue
            profit = revenue - line_cost
            low_margin_products.append({
                'name': product.display_name,
                'revenue': round(revenue),
                'cost': round(line_cost),
                'qty': round(qty, 1),
                'profit': round(profit),
                'food_cost_rate': _pct(line_cost, revenue),
                'gross_margin': _pct(profit, revenue),
            })
        gross_profit = total_revenue - total_cost
        return {
            'food_cost': round(total_cost),
            'gross_profit': round(gross_profit),
            'food_cost_rate': _pct(total_cost, total_revenue),
            'gross_margin': _pct(gross_profit, total_revenue),
            'low_margin_products': sorted(
                low_margin_products,
                key=lambda row: (row['gross_margin'], -row['revenue']),
            )[:5],
        }

    def _customer_mix(self, env, pos_orders, df_utc):
        partner_ids = [order.partner_id.id for order in pos_orders if order.partner_id]
        return self._customer_mix_from_partner_ids(partner_ids, len(pos_orders))

    def _customer_mix_from_partner_ids(self, partner_ids, total_orders):
        unique_partner_ids = set(partner_ids)
        anonymous_orders = max(0, total_orders - len(partner_ids))
        if not unique_partner_ids:
            return {
                'known_customers': 0,
                'anonymous_orders': anonymous_orders,
                'new_customers': 0,
                'returning_customers': 0,
                'new_customer_orders': 0,
                'returning_customer_orders': 0,
                'returning_rate': 0,
            }

        order_count_by_partner = defaultdict(int)
        for partner_id in partner_ids:
            order_count_by_partner[partner_id] += 1
        returning_partner_ids = {
            partner_id
            for partner_id, order_count in order_count_by_partner.items()
            if order_count >= 2
        }
        new_partner_ids = unique_partner_ids - returning_partner_ids
        new_orders = sum(
            order_count
            for partner_id, order_count in order_count_by_partner.items()
            if partner_id in new_partner_ids
        )
        returning_orders = sum(
            order_count
            for partner_id, order_count in order_count_by_partner.items()
            if partner_id in returning_partner_ids
        )
        return {
            'known_customers': len(unique_partner_ids),
            'anonymous_orders': anonymous_orders,
            'new_customers': len(new_partner_ids),
            'returning_customers': len(returning_partner_ids),
            'new_customer_orders': new_orders,
            'returning_customer_orders': returning_orders,
            'returning_rate': _pct(len(returning_partner_ids), len(unique_partner_ids)),
            'returning_order_rate': _pct(returning_orders, new_orders + returning_orders),
        }

    def _action_alerts(self, profitability, customer_mix, low_stock_count, peak_label,
                       kitchen_sla, expo_sla, waste_ratio):
        actions = []
        if profitability['food_cost_rate'] >= 45:
            actions.append({
                'type': 'warning',
                'title': 'Kiểm tra food cost',
                'body': 'Food cost đang ở %s%%. Cần rà BOM, giá nhập và các món biên lợi nhuận thấp.' % profitability['food_cost_rate'],
                'target': 'inventory',
            })
        elif profitability['gross_margin'] >= 50:
            actions.append({
                'type': 'success',
                'title': 'Biên lợi nhuận tốt',
                'body': 'Gross margin đạt %s%%. Có thể ưu tiên đẩy các món đang có biên tốt trong giờ cao điểm.' % profitability['gross_margin'],
                'target': 'sales',
            })
        if customer_mix['returning_rate'] < 55 and customer_mix['known_customers']:
            actions.append({
                'type': 'warning',
                'title': 'Tăng khách quay lại',
                'body': 'Tỷ lệ đơn từ khách quay lại là %s%%. Nên chạy ưu đãi combo hoặc tích điểm cho khách mới.' % customer_mix['returning_rate'],
                'target': 'sales',
            })
        if low_stock_count:
            actions.append({
                'type': 'danger' if low_stock_count >= 3 else 'warning',
                'title': 'Cần nhập hàng',
                'body': '%s nguyên liệu dưới định mức. Ưu tiên kiểm tra tồn kho trước ca cao điểm.' % low_stock_count,
                'target': 'inventory',
            })
        if peak_label != u'\u2014':
            actions.append({
                'type': 'info',
                'title': 'Chuẩn bị giờ cao điểm',
                'body': 'Khung %s có đơn cao nhất. Nên tăng nhân sự bếp/giao món và chuẩn bị nguyên liệu trước khung này.' % peak_label,
                'target': 'kitchen',
            })
        if kitchen_sla['rate'] < 90 or expo_sla['rate'] < 90:
            actions.append({
                'type': 'warning',
                'title': 'Theo dõi SLA vận hành',
                'body': 'SLA bếp %s%%, SLA giao món %s%%. Cần kiểm tra nghẽn ở khu chuẩn bị hoặc giao món.' % (
                    kitchen_sla['rate'], expo_sla['rate']),
                'target': 'kitchen',
            })
        if waste_ratio > 5:
            actions.append({
                'type': 'warning',
                'title': 'Giảm hàng huỷ',
                'body': 'Tỷ lệ hàng huỷ %s%%. Cần xem nhóm nguyên nhân huỷ và điều chỉnh định mức chuẩn bị.' % waste_ratio,
                'target': 'waste',
            })
        return actions[:6]

    # 
    # INVENTORY PRODUCTS (cho Waste Entry dropdown)
    # 
    @http.route('/mcd_kds/inventory_products', type='json', auth='user', methods=['POST'])
    def get_inventory_products(self):
        """Trả về danh sách nguyên liệu từ stock.warehouse.orderpoint."""
        env = request.env
        orderpoints = env['stock.warehouse.orderpoint'].sudo().search([
            ('company_id', '=', env.company.id),
        ], order='product_id asc')

        products = []
        seen = set()
        for op in orderpoints:
            pid = op.product_id.id
            if pid in seen:
                continue
            seen.add(pid)
            qty = getattr(op, 'qty_on_hand', 0.0)
            products.append({
                'id': pid,
                'name': op.product_id.display_name,
                'uom': op.product_id.uom_id.name,
                'qty_on_hand': round(qty, 2),
                'min_qty': round(op.product_min_qty or 0, 2),
                'low': qty < (op.product_min_qty or 0),
            })
        return products

    @http.route('/mcd_kds/products/search', type='json', auth='user', methods=['POST'])
    def search_products(self, query='', limit=80, **kwargs):
        """Search product.product like an Odoo many2one dropdown."""
        Product = request.env['product.product'].sudo()
        query = (query or '').strip()
        try:
            limit = max(20, min(int(limit or 80), 500))
        except Exception:
            limit = 80

        domain = [
            ('active', '=', True),
            ('sale_ok', '=', True),
            ('product_tmpl_id.available_in_pos', '=', True),
        ]
        if query:
            domain += ['|', '|',
                ('name', 'ilike', query),
                ('default_code', 'ilike', query),
                ('barcode', 'ilike', query),
            ]

        records = Product.search(domain, order='name asc, default_code asc', limit=limit + 1)
        products = []
        for product in records[:limit]:
            products.append({
                'id': product.id,
                'key': str(product.id),
                'name': product.display_name,
                'default_code': product.default_code or '',
                'barcode': product.barcode or '',
            })
        return {
            'products': products,
            'has_more': len(records) > limit,
        }

    # 
    # MANAGER DASHBOARD
    # 
    @http.route('/mcd_kds/dashboard', type='json', auth='user', methods=['POST'])
    def get_dashboard(self, period='day', selected_date=None, selected_hour=None, selected_month=None, selected_year=None, **kwargs):
        env = request.env
        tz  = pytz.timezone(env.user.tz or 'UTC')
        df_utc, dt_utc, now, tz = _get_date_range(period, tz, selected_date, selected_month, selected_year)

        current_stats = self._pos_stats_sql(env, tz, df_utc, dt_utc, period)
        order_rows = current_stats['order_rows']
        product_rows = current_stats['product_rows']
        line_bucket_rows = current_stats['line_bucket_rows']
        total_revenue = current_stats['total_revenue']
        total_orders = current_stats['total_orders']
        total_items = round(current_stats['total_items'], 1)
        avg_order_value = current_stats['avg_order_value']

        labels = _period_bucket_labels(period, df_utc, dt_utc, tz)

        buckets       = {l: 0.0 for l in labels}
        order_buckets = {l: 0   for l in labels}

        for order in order_rows:
            key = order['bucket']
            if key in buckets:
                buckets[key]       += order['amount_total']
                order_buckets[key] += 1

        chart_revenue = {
            'labels': labels,
            'values': [round(buckets[l] / 1_000_000, 1) for l in labels],
        }

        peak_sorted = sorted(order_buckets.items(), key=lambda x: x[1], reverse=True)
        peak_hours  = [{'label': l, 'orders': c} for l, c in peak_sorted if c][:5]
        peak_label  = peak_hours[0]['label'] if peak_hours else u'\u2014'
        peak_label_title = 'Giờ cao điểm'
        peak_display = peak_label
        if period == 'year':
            peak_label_title = 'Tháng cao điểm'
            peak_display = 'Tháng %s' % peak_label[1:] if peak_label.startswith('T') else peak_label
            for row in peak_hours:
                row['display_label'] = 'Tháng %s' % row['label'][1:] if row['label'].startswith('T') else row['label']
        elif period == 'month':
            peak_label_title = 'Ngày cao điểm'
            peak_display = 'Ngày %s' % peak_label if peak_label != u'\u2014' else peak_label
            for row in peak_hours:
                row['display_label'] = 'Ngày %s' % row['label']
        elif period == 'week':
            peak_label_title = 'Ngày cao điểm'

        product_ids = [row['product_id'] for row in product_rows if row.get('product_id')]
        products_by_id = {
            product.id: product
            for product in env['product.product'].sudo().browse(product_ids).exists()
        }
        product_map = {}
        for row in product_rows:
            product = products_by_id.get(row['product_id'])
            if not product:
                continue
            product_map[row['product_id']] = {
                'key': str(row['product_id']),
                'id': row['product_id'],
                'name': product.display_name,
                'qty': row.get('qty') or 0.0,
                'revenue': row.get('revenue') or 0.0,
            }
        top_products = sorted(product_map.values(), key=lambda x: x['qty'], reverse=True)[:6]
        for p in top_products:
            p['qty']     = round(p['qty'], 1)
            p['revenue'] = round(p['revenue'])
            p['key']     = p.get('key') or str(p['name'])

        bucket_products = {
            label: defaultdict(lambda: {'name': '', 'qty': 0.0, 'revenue': 0.0})
            for label in labels
        }
        for row in line_bucket_rows:
            key = row['bucket']
            if key in bucket_products:
                product = products_by_id.get(row['product_id'])
                if not product:
                    continue
                item = bucket_products[key][row['product_id']]
                item['name'] = product.display_name
                item['qty'] += row.get('qty') or 0.0
                item['revenue'] += row.get('revenue') or 0.0

        chart_revenue['details'] = []
        for label in labels:
            top_bucket_products = sorted(
                bucket_products[label].values(),
                key=lambda p: p['revenue'],
                reverse=True,
            )[:5]
            chart_revenue['details'].append({
                'label': label,
                'revenue': round(buckets[label]),
                'orders': order_buckets[label],
                'avg_order_value': round(buckets[label] / order_buckets[label]) if order_buckets[label] else 0,
                'top_products': [
                    {
                        'name': p['name'],
                        'qty': round(p['qty'], 1),
                        'revenue': round(p['revenue']),
                    }
                    for p in top_bucket_products
                ],
            })

        # Kitchen avg: filter theo serve_time (la luc bam Serve/Done)
        KO = env['kitchen.order'].sudo()
        k_done = KO.search([
            ('state', '=', 'done'),
            ('serve_time', '>=', df_utc),
            ('serve_time', '<=', dt_utc),
        ])
        k_durs = [int((o.serve_time - o.order_time).total_seconds())
                  for o in k_done if o.serve_time and o.order_time
                  and 0 < (o.serve_time - o.order_time).total_seconds() <= 1800]
        kitchen_avg = round(sum(k_durs) / len(k_durs)) if k_durs else 0

        # Expo avg: filter theo serve_time
        EO = env['expo.order'].sudo()
        e_done = EO.search([
            ('state', '=', 'done'),
            ('serve_time', '>=', df_utc),
            ('serve_time', '<=', dt_utc),
        ])
        e_durs = [int((o.serve_time - o.order_time).total_seconds())
                  for o in e_done if o.serve_time and o.order_time
                  and 0 < (o.serve_time - o.order_time).total_seconds() <= 1800]
        expo_avg = round(sum(e_durs) / len(e_durs)) if e_durs else 0

        kitchen_sla = _sla_stats(k_durs, KITCHEN_SLA_SECONDS)
        expo_sla = _sla_stats(e_durs, EXPO_SLA_SECONDS)

        now_utc = datetime.utcnow()
        is_live_report = _range_ends_today(dt_utc, tz)
        k_waiting = KO.search([
            ('state', '=', 'waiting'),
            ('order_time', '>=', df_utc),
            ('order_time', '<=', dt_utc),
        ]) if is_live_report else KO.browse([])
        e_waiting = EO.search([
            ('state', '=', 'waiting'),
            ('order_time', '>=', df_utc),
            ('order_time', '<=', dt_utc),
        ]) if is_live_report else EO.browse([])
        kitchen_overdue_count = len([
            o for o in k_waiting
            if o.order_time and int((now_utc - o.order_time).total_seconds()) > KITCHEN_SLA_SECONDS
        ])
        expo_overdue_count = len([
            o for o in e_waiting
            if o.order_time and int((now_utc - o.order_time).total_seconds()) > EXPO_SLA_SECONDS
        ])

        waste_entries = env['mcd.waste.entry'].sudo().search([
            ('date', '>=', df_utc),
            ('date', '<=', dt_utc),
        ])
        waste_qty = sum(entry.qty for entry in waste_entries)
        waste_loss = round(sum(entry.total_loss for entry in waste_entries))
        waste_ratio = _pct(waste_qty, total_items)

        orderpoints = env['stock.warehouse.orderpoint'].sudo().search([
            ('company_id', '=', env.company.id),
        ])
        low_stock_count = len([
            op for op in orderpoints
            if getattr(op, 'qty_on_hand', 0.0) < (op.product_min_qty or 0.0)
        ])

        profitability = self._profitability_summary_from_product_rows(
            env, product_rows, total_revenue, products_by_id)
        customer_mix = self._customer_mix_from_partner_ids(
            [row['partner_id'] for row in order_rows if row.get('partner_id')],
            total_orders)

        prev_df_utc, prev_dt_utc = _previous_range(df_utc, dt_utc, tz, period, selected_date)
        previous_stats = self._pos_stats_sql(env, tz, prev_df_utc, prev_dt_utc, period)
        previous_total_orders = previous_stats['total_orders']
        previous_total_revenue = round(previous_stats['total_revenue'])
        previous_avg_order_value = previous_stats['avg_order_value']
        revenue_growth = _pct_change(total_revenue, previous_total_revenue)
        orders_growth = _pct_change(total_orders, previous_total_orders)
        avg_order_value_growth = _pct_change(avg_order_value, previous_avg_order_value)
        items_growth = _pct_change(total_items, previous_stats['total_items'])

        top_product = top_products[0] if top_products else {}
        top_product_share = _pct(top_product.get('revenue', 0), total_revenue)
        top3_revenue = sum(p.get('revenue', 0) for p in top_products[:3])
        top3_share = _pct(top3_revenue, total_revenue)
        product_mix = []
        for p in top_products[:5]:
            product_mix.append({
                'name': p['name'],
                'revenue': p['revenue'],
                'qty': p['qty'],
                'revenue_share': _pct(p['revenue'], total_revenue),
                'qty_share': _pct(p['qty'], total_items),
            })

        statistical_insights = []
        if peak_label != u'\u2014':
            statistical_insights.append({
                'type': 'info',
                'title': u'Doanh thu tập trung theo khung giờ',
                'body': u'Khung giờ %s có số đơn cao nhất trong kỳ báo cáo.' % peak_label,
                'target': 'sales',
            })
        if top_products:
            statistical_insights.append({
                'type': 'success',
                'title': u'C cu sản phẩm',
                'body': u'%s chiếm %s%% doanh thu; Top 3 món chiếm %s%% doanh thu.' % (
                    top_products[0]['name'], top_product_share, top3_share),
                'target': 'sales',
            })
        if revenue_growth is not None:
            trend_text = u'tng' if revenue_growth >= 0 else u'gim'
            statistical_insights.append({
                'type': 'info' if revenue_growth >= 0 else 'warning',
                'title': u'So sánh doanh thu',
                'body': u'Doanh thu %s %s%% so với %s.' % (
                    trend_text, abs(revenue_growth), _period_compare_label(period, selected_date)),
                'target': 'sales',
            })
        if waste_ratio > 5:
            statistical_insights.append({
                'type': 'warning',
                'title': u'T l hàng huỷ cao',
                'body': u'Hàng huỷ chiếm %s%% số lượng bán ra, cần kiểm tra nguyên nhân thất thoát.' % waste_ratio,
                'target': 'waste',
            })

        statistical_insights = []
        if peak_label != u'\u2014':
            statistical_insights.append({
                'type': 'info',
                'title': 'Doanh thu tập trung theo khung giờ',
                'body': 'Khung giờ %s có số đơn cao nhất trong kỳ báo cáo.' % peak_label,
                'target': 'sales',
            })
        if top_products:
            statistical_insights.append({
                'type': 'success',
                'title': 'Cơ cấu sản phẩm',
                'body': '%s chiếm %s%% doanh thu; Top 3 món chiếm %s%% doanh thu.' % (
                    top_products[0]['name'], top_product_share, top3_share),
                'target': 'sales',
            })
        if revenue_growth is not None:
            trend_text = 'tăng' if revenue_growth >= 0 else 'giảm'
            statistical_insights.append({
                'type': 'info' if revenue_growth >= 0 else 'warning',
                'title': 'So sánh doanh thu',
                'body': 'Doanh thu %s %s%% so với %s.' % (
                    trend_text, abs(revenue_growth), _period_compare_label(period, selected_date)),
                'target': 'sales',
            })
        if waste_ratio > 5:
            statistical_insights.append({
                'type': 'warning',
                'title': 'Tỉ lệ hàng hủy cao',
                'body': 'Hàng hủy chiếm %s%% số lượng bán ra, cần kiểm tra nguyên nhân thất thoát.' % waste_ratio,
                'target': 'waste',
            })

        alerts = []
        if kitchen_sla['rate'] < 85 or kitchen_overdue_count:
            alerts.append({
                'type': 'danger' if kitchen_overdue_count else 'warning',
                'title': 'Kitchen cần xử lý',
                'body': f"SLA {kitchen_sla['rate']}%, {kitchen_overdue_count} đơn bếp quá 5 phút.",
                'target': 'kitchen',
            })
        if expo_sla['rate'] < 85 or expo_overdue_count:
            alerts.append({
                'type': 'danger' if expo_overdue_count else 'warning',
                'title': 'Expo đang chậm',
                'body': f"SLA {expo_sla['rate']}%, {expo_overdue_count} đơn giao quá 3 phút.",
                'target': 'expo',
            })
        if waste_ratio > 5 or waste_loss > 0:
            alerts.append({
                'type': 'warning' if waste_ratio <= 8 else 'danger',
                'title': 'Waste cn theo di',
                'body': f"Đã huỷ {round(waste_qty, 1)} đơn vị, thất thoát {waste_loss:,} VND.",
                'target': 'waste',
            })
        if low_stock_count:
            alerts.append({
                'type': 'warning',
                'title': 'Inventory thấp',
                'body': f"{low_stock_count} nguyên liệu đang thấp hơn định mức tối thiểu.",
                'target': 'inventory',
            })
        if peak_label != u'\u2014':
            alerts.append({
                'type': 'info',
                'title': 'Khung gi cao điểm',
                'body': f"Khung giờ cao điểm hiện tại là {peak_label}. Nên kiểm tra nhân sự khu bếp và khu giao món.",
                'target': 'sales',
            })
        if not alerts:
            alerts.append({
                'type': 'success',
                'title': 'Vận hành ổn định',
                'body': 'SLA, hàng huỷ và đơn quá hạn đều trong ngưỡng tốt.',
                'target': '',
            })

        for alert in alerts:
            target = alert.get('target')
            if target == 'kitchen':
                alert['title'] = 'Kitchen cần xử lý'
                alert['body'] = f"SLA {kitchen_sla['rate']}%, {kitchen_overdue_count} đơn bếp quá 5 phút."
            elif target == 'expo':
                alert['title'] = 'Expo đang chậm'
                alert['body'] = f"SLA {expo_sla['rate']}%, {expo_overdue_count} đơn giao quá 3 phút."
            elif target == 'waste':
                alert['title'] = 'Hàng hủy cần theo dõi'
                alert['body'] = f"Đã hủy {round(waste_qty, 1)} đơn vị, thất thoát {waste_loss:,} VND."
            elif target == 'inventory':
                alert['title'] = 'Tồn kho thấp'
                alert['body'] = f"{low_stock_count} nguyên liệu đang thấp hơn định mức tối thiểu."
            elif target == 'sales':
                alert['title'] = 'Khung giờ cao điểm'
                alert['body'] = f"Khung giờ cao điểm hiện tại là {peak_label}. Nên kiểm tra nhân sự khu bếp và khu giao món."
            elif not target:
                alert['title'] = 'Vận hành ổn định'
                alert['body'] = 'SLA, hàng hủy và đơn quá hạn đều trong ngưỡng tốt.'

        action_alerts = self._action_alerts(
            profitability,
            customer_mix,
            low_stock_count,
            peak_label,
            kitchen_sla,
            expo_sla,
            waste_ratio,
        )

        health_score = 100
        health_score -= min(20, max(0, 95 - kitchen_sla['rate']) * 0.7)
        health_score -= min(20, max(0, 95 - expo_sla['rate']) * 0.7)
        health_score -= min(20, kitchen_overdue_count * 4 + expo_overdue_count * 3)
        health_score -= min(15, waste_ratio * 2)
        health_score -= min(10, low_stock_count * 2)
        health_score = int(max(0, min(100, round(health_score))))
        if health_score >= 85:
            health_status = 'Tốt'
        elif health_score >= 70:
            health_status = 'Cần theo dõi'
        else:
            health_status = 'Cần xử lý ngay'

        return {
            'total_orders':         total_orders,
            'total_revenue':        round(total_revenue),
            'total_items':          total_items,
            'avg_order_value':      avg_order_value,
            'kitchen_avg_duration': kitchen_avg,
            'expo_avg_duration':    expo_avg,
            'kitchen_sla_rate':      kitchen_sla['rate'],
            'expo_sla_rate':         expo_sla['rate'],
            'waste_ratio':           waste_ratio,
            'waste_loss':            waste_loss,
            'kitchen_overdue_count': kitchen_overdue_count,
            'expo_overdue_count':    expo_overdue_count,
            'low_stock_count':       low_stock_count,
            'profitability':          profitability,
            'customer_mix':           customer_mix,
            'action_alerts':          action_alerts,
            'health_score':          health_score,
            'health_status':         health_status,
            'alerts':                alerts,
            'statistical_insights':   statistical_insights,
            'peak_hour':            peak_label,
            'peak_display':         peak_display,
            'peak_label_title':     peak_label_title,
            'top_products':         top_products,
            'chart_revenue':        chart_revenue,
            'comparison_label':      _period_compare_label(period, selected_date),
            'comparison': {
                'revenue': {
                    'current': round(total_revenue),
                    'previous': previous_total_revenue,
                    'delta': round(total_revenue - previous_total_revenue),
                    'growth': revenue_growth,
                },
                'orders': {
                    'current': total_orders,
                    'previous': previous_total_orders,
                    'delta': total_orders - previous_total_orders,
                    'growth': orders_growth,
                },
                'avg_order_value': {
                    'current': avg_order_value,
                    'previous': previous_avg_order_value,
                    'delta': round(avg_order_value - previous_avg_order_value),
                    'growth': avg_order_value_growth,
                },
                'items': {
                    'current': total_items,
                    'previous': round(previous_stats['total_items'], 1),
                    'delta': round(total_items - previous_stats['total_items'], 1),
                    'growth': items_growth,
                },
            },
            'composition': {
                'top_product_share': top_product_share,
                'top3_revenue_share': top3_share,
                'top_product': top_product.get('name') or u'\u2014',
                'product_mix': product_mix,
                'waste_ratio': waste_ratio,
            },
            'previous_total_orders': previous_total_orders,
            'previous_total_revenue': previous_total_revenue,
            'previous_avg_order_value': previous_avg_order_value,
            'revenue_growth':        revenue_growth,
            'orders_growth':         orders_growth,
            'avg_order_value_growth': avg_order_value_growth,
            'open_hour':            OPEN_HOUR,
            'close_hour':           CLOSE_HOUR,
        }

    # 
    # KITCHEN DASHBOARD
    # 
    @http.route('/mcd_kds/kitchen', type='json', auth='user', methods=['POST'])
    def get_kitchen(self, period='day', selected_date=None, selected_month=None, selected_year=None, **kwargs):
        env = request.env
        tz  = pytz.timezone(env.user.tz or 'UTC')
        df_utc, dt_utc, now, tz = _get_date_range(period, tz, selected_date, selected_month, selected_year)

        KO = env['kitchen.order'].sudo()

        # Filter theo serve_time (luc bam Serve)
        k_done = KO.search([
            ('state', '=', 'done'),
            ('serve_time', '>=', df_utc),
            ('serve_time', '<=', dt_utc),
        ])
        k_done_filtered = k_done

        durations = [int((o.serve_time - o.order_time).total_seconds())
                     for o in k_done_filtered if o.serve_time and o.order_time
                     and 0 < (o.serve_time - o.order_time).total_seconds() <= 1800]
        avg_duration = round(sum(durations) / len(durations)) if durations else 0
        fastest = min(durations) if durations else 0
        slowest = max(durations) if durations else 0
        sla = _sla_stats(durations, KITCHEN_SLA_SECONDS)
        duration_buckets = _duration_split(durations, KITCHEN_SLA_SECONDS)

        is_live_report = _range_ends_today(dt_utc, tz)
        k_waiting = KO.search([
            ('state', '=', 'waiting'),
            ('order_time', '>=', df_utc),
            ('order_time', '<=', dt_utc),
        ]) if is_live_report else KO.browse([])
        now_utc   = datetime.utcnow()
        overdue   = []
        for o in k_waiting:
            if o.order_time:
                waited = int((now_utc - o.order_time).total_seconds())
                if waited > KITCHEN_SLA_SECONDS:
                    overdue.append({'name': o.name, 'waited': waited, 'service_type': o.service_type})
        aging_buckets = _aging_buckets(k_waiting, now_utc, [
            ('0-2p', 0, 2 * 60),
            ('2-5p', 2 * 60, KITCHEN_SLA_SECONDS),
            ('>5p', KITCHEN_SLA_SECONDS, None),
        ])

        eat_in, take_out = _pos_service_mix(env, df_utc, dt_utc)

        # Top món tu kitchen.order.line (nguyên liệu thuc su che bien) (món thuc su che bien)
        k_lines = env['kitchen.order.line'].sudo().search([
            ('order_id', 'in', [o.id for o in k_done_filtered])
        ]) if k_done_filtered else env['kitchen.order.line'].sudo().browse([])
        prod_cnt = defaultdict(int)
        for line in k_lines:
            if _skip_kitchen_metric_line(line):
                continue
            prod_cnt[line.product_name] += line.qty
        top_items = [{'name': n, 'qty': q}
                     for n, q in sorted(prod_cnt.items(), key=lambda x: x[1], reverse=True)[:8]]
        slow_products = _speed_by_product(k_lines, KITCHEN_SLA_SECONDS)

        hour_b = defaultdict(int)
        for o in k_done_filtered:
            dt_ref = o.order_time or o.serve_time
            if dt_ref:
                h = dt_ref.replace(tzinfo=pytz.utc).astimezone(tz).hour
                hour_b[f'{h:02d}h'] += 1
        peak_hours = [{'label': l, 'orders': c}
                      for l, c in sorted(hour_b.items(), key=lambda x: x[1], reverse=True)[:5] if c]

        insights = []
        if sla['rate'] < 85:
            insights.append({
                'type': 'warning',
                'title': 'SLA bếp thấp',
                'body': f"{sla['late']} đơn hoàn thành quá 5 phút.",
            })
        if overdue:
            insights.append({
                'type': 'danger',
                'title': 'Dang co đơn tre',
                'body': f"{len(overdue)} đơn đang chờ quá 5 phút, cần ưu tiên xử lý.",
            })
        if slow_products:
            top_slow = slow_products[0]
            insights.append({
                'type': 'info',
                'title': 'Món gây bottleneck',
                'body': f"{top_slow['name']} đang có thời gian trung bình {round(top_slow['avg_duration'] / 60, 1)} phút.",
            })
        if not insights:
            insights.append({
                'type': 'success',
                'title': 'Bếp vận hành ổn định',
                'body': 'Không có đơn trễ và tốc độ chế biến đang trong ngưỡng tốt.',
            })

        return {
            'avg_duration': avg_duration, 'fastest': fastest, 'slowest': slowest,
            'waiting_count': len(k_waiting), 'overdue_orders': overdue, 'overdue_count': len(overdue),
            'sla_rate': sla['rate'], 'late_done_count': sla['late'],
            'aging_buckets': aging_buckets, 'duration_buckets': duration_buckets,
            'eat_in': eat_in, 'take_out': take_out,
            'top_items': top_items, 'slow_products': slow_products, 'peak_hours': peak_hours,
            'insights': insights,
            'total_done': len(k_done_filtered),
        }

    # 
    # EXPO DASHBOARD
    # 
    @http.route('/mcd_kds/expo', type='json', auth='user', methods=['POST'])
    def get_expo(self, period='day', selected_date=None, selected_month=None, selected_year=None, **kwargs):
        env = request.env
        tz  = pytz.timezone(env.user.tz or 'UTC')
        df_utc, dt_utc, now, tz = _get_date_range(period, tz, selected_date, selected_month, selected_year)

        EO = env['expo.order'].sudo()

        # Filter theo serve_time (luc bam Serve)
        e_done = EO.search([
            ('state', '=', 'done'),
            ('serve_time', '>=', df_utc),
            ('serve_time', '<=', dt_utc),
        ])
        e_done_filtered = e_done

        durations = [int((o.serve_time - o.order_time).total_seconds())
                     for o in e_done_filtered if o.serve_time and o.order_time
                     and 0 < (o.serve_time - o.order_time).total_seconds() <= 1800]
        avg_duration = round(sum(durations) / len(durations)) if durations else 0
        fastest = min(durations) if durations else 0
        slowest = max(durations) if durations else 0
        sla = _sla_stats(durations, EXPO_SLA_SECONDS)
        duration_buckets = _duration_split(durations, EXPO_SLA_SECONDS)

        kitchen_done = request.env['kitchen.order'].sudo().search([
            ('state', '=', 'done'),
            ('serve_time', '>=', df_utc),
            ('serve_time', '<=', dt_utc),
        ])
        kitchen_serve_by_name = {
            order.name: order.serve_time
            for order in kitchen_done
            if order.name and order.serve_time
        }
        handoff_durations = []
        for order in e_done_filtered:
            kitchen_serve_time = kitchen_serve_by_name.get(order.name)
            if not kitchen_serve_time or not order.serve_time:
                continue
            handoff = int((order.serve_time - kitchen_serve_time).total_seconds())
            if 0 < handoff <= 1800:
                handoff_durations.append(handoff)
        avg_handoff_delay = round(sum(handoff_durations) / len(handoff_durations)) if handoff_durations else avg_duration

        is_live_report = _range_ends_today(dt_utc, tz)
        e_waiting = EO.search([
            ('state', '=', 'waiting'),
            ('order_time', '>=', df_utc),
            ('order_time', '<=', dt_utc),
        ]) if is_live_report else EO.browse([])
        now_utc   = datetime.utcnow()
        overdue   = []
        for o in e_waiting:
            if o.order_time:
                waited = int((now_utc - o.order_time).total_seconds())
                if waited > EXPO_SLA_SECONDS:
                    overdue.append({'name': o.name, 'waited': waited, 'service_type': o.service_type})
        aging_buckets = _aging_buckets(e_waiting, now_utc, [
            ('0-1p', 0, 60),
            ('1-3p', 60, EXPO_SLA_SECONDS),
            ('>3p', EXPO_SLA_SECONDS, None),
        ])

        eat_in, take_out = _pos_service_mix(env, df_utc, dt_utc)

        e_lines = env['expo.order.line'].sudo().search([
            ('order_id', 'in', [o.id for o in e_done_filtered])
        ]) if e_done_filtered else env['expo.order.line'].sudo().browse([])
        prod_cnt = defaultdict(int)
        for line in e_lines:
            if _skip_kitchen_metric_line(line):
                continue
            prod_cnt[line.product_name] += line.qty
        top_items = [{'name': n, 'qty': q}
                     for n, q in sorted(prod_cnt.items(), key=lambda x: x[1], reverse=True)[:8]]
        slow_products = _speed_by_product(e_lines, EXPO_SLA_SECONDS)

        hour_b = defaultdict(int)
        for order in e_done_filtered:
            dt_ref = order.order_time or order.serve_time
            if dt_ref:
                h = dt_ref.replace(tzinfo=pytz.utc).astimezone(tz).hour
                hour_b[f'{h:02d}h'] += 1
        peak_hours = [{'label': l, 'orders': c}
                      for l, c in sorted(hour_b.items(), key=lambda x: x[1], reverse=True)[:5] if c]

        insights = []
        if sla['rate'] < 85:
            insights.append({
                'type': 'warning',
                'title': 'SLA giao món thấp',
                'body': f"{sla['late']} đơn giao vượt quá 3 phút.",
            })
        if overdue:
            insights.append({
                'type': 'danger',
                'title': 'Đơn giao bị trễ',
                'body': f"{len(overdue)} đơn đang chờ giao quá 3 phút.",
            })
        if avg_handoff_delay > EXPO_SLA_SECONDS:
            insights.append({
                'type': 'info',
                'title': 'Handoff cham',
                'body': f"Thời gian từ bếp sang giao món trung bình {round(avg_handoff_delay / 60, 1)} phút.",
            })
        if not insights:
            insights.append({
                'type': 'success',
                'title': 'Khu giao món ổn định',
                'body': 'Đơn giao đang trong ngưỡng SLA tốt và số đơn quá hạn không lớn.',
            })

        return {
            'avg_duration': avg_duration, 'fastest': fastest, 'slowest': slowest,
            'sla_rate': sla['rate'], 'late_done_count': sla['late'],
            'avg_handoff_delay': avg_handoff_delay,
            'aging_buckets': aging_buckets, 'duration_buckets': duration_buckets,
            'waiting_count': len(e_waiting), 'overdue_orders': overdue, 'overdue_count': len(overdue),
            'eat_in': eat_in, 'take_out': take_out,
            'top_items': top_items, 'slow_products': slow_products, 'peak_hours': peak_hours,
            'insights': insights,
            'total_done': len(e_done_filtered),
        }

    # 
    # SALES DASHBOARD
    # 
    @http.route('/mcd_kds/sales', type='json', auth='user', methods=['POST'])
    def get_sales(self, period='day', selected_date=None, selected_month=None, selected_year=None, **kwargs):
        env = request.env
        tz  = pytz.timezone(env.user.tz or 'UTC')
        df_utc, dt_utc, now, tz = _get_date_range(period, tz, selected_date, selected_month, selected_year)

        current_stats = self._pos_stats(env, tz, df_utc, dt_utc)
        pos_orders = current_stats['orders']
        all_lines = current_stats['lines']
        total_revenue = current_stats['total_revenue']
        total_orders = current_stats['total_orders']
        avg_order_value = current_stats['avg_order_value']
        total_items = current_stats['total_items']

        product_map = {}
        for line in all_lines:
            pid = line.product_id.id
            if pid not in product_map:
                product_map[pid] = {
                    'key': str(pid),
                    'id': pid,
                    'name': line.product_id.display_name,
                    'qty': 0.0,
                    'revenue': 0.0,
                    'order_ids': set(),
                }
            product_map[pid]['qty']     += line.qty
            product_map[pid]['revenue'] += line.price_subtotal_incl
            product_map[pid]['order_ids'].add(line.order_id.id)
        top_products = sorted(product_map.values(), key=lambda x: x['revenue'], reverse=True)
        for p in top_products:
            p['qty']     = round(p['qty'], 1)
            p['revenue'] = round(p['revenue'])
            p['order_count'] = len(p.pop('order_ids', []))
            p['revenue_share'] = _pct(p['revenue'], total_revenue)
            p['qty_share'] = _pct(p['qty'], total_items)
            p['order_share'] = _pct(p['order_count'], total_orders)
            p['key']     = p.get('key') or str(p['name'])

        sales_labels = _period_bucket_labels(period, df_utc, dt_utc, tz)
        revenue_buckets = {label: 0.0 for label in sales_labels}
        qty_buckets = {label: 0.0 for label in sales_labels}
        order_buckets = {label: 0 for label in sales_labels}
        for order in pos_orders:
            local_dt = order.date_order.replace(tzinfo=pytz.utc).astimezone(tz)
            bucket_key = _period_bucket_key(period, local_dt, sales_labels)
            if bucket_key in revenue_buckets:
                revenue_buckets[bucket_key] += order.amount_total
                order_buckets[bucket_key] += 1

        bucket_products = {
            label: defaultdict(lambda: {'name': '', 'qty': 0.0, 'revenue': 0.0})
            for label in revenue_buckets
        }
        for line in all_lines:
            local_dt = line.order_id.date_order.replace(tzinfo=pytz.utc).astimezone(tz)
            bucket_key = _period_bucket_key(period, local_dt, sales_labels)
            if bucket_key in bucket_products:
                item = bucket_products[bucket_key][line.product_id.id]
                item['name'] = line.product_id.display_name
                item['qty'] += line.qty
                item['revenue'] += line.price_subtotal_incl
                qty_buckets[bucket_key] += line.qty

        monthly_details = []
        for label, revenue in revenue_buckets.items():
            top_bucket_products = sorted(
                bucket_products[label].values(),
                key=lambda p: p['revenue'],
                reverse=True,
            )[:5]
            monthly_details.append({
                'label': label,
                'revenue': round(revenue),
                'qty': round(qty_buckets[label], 1),
                'orders': order_buckets[label],
                'avg_order_value': round(revenue / order_buckets[label]) if order_buckets[label] else 0,
                'top_products': [
                    {
                        'name': p['name'],
                        'qty': round(p['qty'], 1),
                        'revenue': round(p['revenue']),
                    }
                    for p in top_bucket_products
                ],
            })

        monthly_chart = {
            'labels': list(revenue_buckets.keys()),
            'values': [round(v / 1_000_000, 2) for v in revenue_buckets.values()],
            'revenue_values': [round(v / 1_000_000, 2) for v in revenue_buckets.values()],
            'qty_values': [round(qty_buckets[label], 2) for label in revenue_buckets.keys()],
            'order_values': [order_buckets[label] for label in revenue_buckets.keys()],
            'details': monthly_details,
        }
        product_chart = {
            'labels': [p['name'] for p in top_products[:8]],
            'values': [round(p['revenue'] / 1_000_000, 2) for p in top_products[:8]],
        }

        prev_df_utc, prev_dt_utc = _previous_range(df_utc, dt_utc, tz, period, selected_date)
        previous_stats = self._pos_stats(env, tz, prev_df_utc, prev_dt_utc)
        top3_revenue = sum(p.get('revenue', 0) for p in top_products[:3])
        top3_share = _pct(top3_revenue, total_revenue)
        best_product = top_products[0] if top_products else {}
        composition = {
            'top_product': best_product.get('name') or u'\u2014',
            'top_product_share': best_product.get('revenue_share', 0),
            'top3_revenue_share': top3_share,
            'product_count': len(product_map),
        }
        insights = []
        if best_product:
            insights.append({
                'type': 'success',
                'title': 'Sản phẩm chủ lực',
                'body': '%s đóng góp %s%% doanh thu trong kỳ.' % (
                    best_product['name'], best_product['revenue_share']),
            })
        if top3_share:
            insights.append({
                'type': 'info',
                'title': 'Muc do tap trung doanh thu',
                'body': 'Top 3 sản phẩm chiếm %s%% doanh thu, dùng để đánh giá phụ thuộc vào nhóm món chủ lực.' % top3_share,
            })
        if total_orders:
            peak_hour = max(order_buckets.items(), key=lambda item: item[1])[0]
            insights.append({
                'type': 'info',
                'title': 'Khung giờ bán mạnh',
                'body': '%s là khung giờ có nhiều đơn nhất trong kỳ.' % peak_hour,
            })

        return {
            'total_revenue': round(total_revenue), 'total_orders': total_orders,
            'avg_order_value': avg_order_value,
            'total_items': round(total_items, 1),
            'top_products': top_products,
            'monthly_chart': monthly_chart, 'product_chart': product_chart,
            'comparison_label': _period_compare_label(period, selected_date),
            'comparison': {
                'revenue': {
                    'current': round(total_revenue),
                    'previous': round(previous_stats['total_revenue']),
                    'delta': round(total_revenue - previous_stats['total_revenue']),
                    'growth': _pct_change(total_revenue, previous_stats['total_revenue']),
                },
                'orders': {
                    'current': total_orders,
                    'previous': previous_stats['total_orders'],
                    'delta': total_orders - previous_stats['total_orders'],
                    'growth': _pct_change(total_orders, previous_stats['total_orders']),
                },
                'avg_order_value': {
                    'current': avg_order_value,
                    'previous': previous_stats['avg_order_value'],
                    'delta': round(avg_order_value - previous_stats['avg_order_value']),
                    'growth': _pct_change(avg_order_value, previous_stats['avg_order_value']),
                },
                'items': {
                    'current': round(total_items, 1),
                    'previous': round(previous_stats['total_items'], 1),
                    'delta': round(total_items - previous_stats['total_items'], 1),
                    'growth': _pct_change(total_items, previous_stats['total_items']),
                },
            },
            'composition': composition,
            'statistical_insights': insights,
        }

    # 
    # INVENTORY DASHBOARD
    # 
    @http.route('/mcd_kds/inventory', type='json', auth='user', methods=['POST'])
    def get_inventory(self, period='day', selected_date=None, selected_month=None, selected_year=None, **kwargs):
        env = request.env
        tz  = pytz.timezone(env.user.tz or 'UTC')
        df_utc, dt_utc, now, tz = _get_date_range(period, tz, selected_date, selected_month, selected_year)

        orderpoints = env['stock.warehouse.orderpoint'].sudo().search([
            ('company_id', '=', env.company.id),
        ])
        low_stock = []
        reorder   = []
        for op in orderpoints:
            product = op.product_id
            qty     = getattr(op, 'qty_on_hand', 0.0)
            min_qty = op.product_min_qty or 0.0
            max_qty = op.product_max_qty or 0.0
            if qty < min_qty:
                pct     = qty / min_qty if min_qty else 0
                urgency = 'critical' if pct < 0.3 else 'warning'
                suggest_qty = round(max(max_qty - qty, 0.0), 2)
                vendor = self._get_product_vendor(product)
                low_stock.append({
                    'product_id': product.id,
                    'name': product.display_name,
                    'qty_on_hand': round(qty, 2),
                    'min_qty': round(min_qty, 2),
                    'max_qty': round(max_qty, 2),
                    'uom': product.uom_id.name,
                    'urgency': urgency,
                    'pct': round(pct * 100),
                    'suggest_qty': suggest_qty,
                })
                if suggest_qty > 0:
                    reorder.append({
                        'product_id': product.id,
                        'name': product.display_name,
                        'qty_on_hand': round(qty, 2),
                        'min_qty': round(min_qty, 2),
                        'max_qty': round(max_qty, 2),
                        'suggest_qty': suggest_qty,
                        'uom': product.uom_id.name,
                        'vendor_id': vendor.partner_id.id if vendor else False,
                        'vendor_name': vendor.partner_id.display_name if vendor else '',
                        'vendor_price': round(vendor.price or 0, 2) if vendor else 0,
                        'can_create_po': bool(vendor),
                        'status': 'ready' if vendor else 'missing_vendor',
                    })
        low_stock.sort(key=lambda x: (x['urgency'] != 'critical', x['qty_on_hand']))
        reorder.sort(key=lambda x: (not x['can_create_po'], x['name']))

        # Inventory dashboard must show raw material consumption, not menu items.
        # We compute it from sold POS lines by expanding each product's BoM and
        # keeping only products in Raw Materials / ...
        top_sold = []
        if 'mrp.bom' in env.registry:
            raw_categories = env['product.category'].sudo().search([
                ('complete_name', 'ilike', 'Raw Materials'),
            ])
            raw_category_ids = set(raw_categories.ids)
            usage = {}

            all_pos = env['pos.order'].sudo().search([
                ('state', 'in', ['paid', 'done', 'invoiced']),
                ('date_order', '>=', df_utc),
                ('date_order', '<=', dt_utc),
            ])
            pos_orders = _business_hours_filter(all_pos, tz, 'date_order')
            sale_lines = env['pos.order.line'].sudo().search([
                ('order_id', 'in', [order.id for order in pos_orders]),
            ]) if pos_orders else env['pos.order.line'].sudo().browse([])

            Bom = env['mrp.bom'].sudo()
            bom_cache = {}

            def find_bom(product):
                if not product:
                    return False
                if product.id in bom_cache:
                    return bom_cache[product.id]

                company_domain = ['|', ('company_id', '=', False), ('company_id', '=', env.company.id)]
                bom = Bom.search([
                    ('product_id', '=', product.id),
                    ('type', 'in', ['normal', 'phantom']),
                    *company_domain,
                ], limit=1)
                if not bom:
                    bom = Bom.search([
                        ('product_id', '=', False),
                        ('product_tmpl_id', '=', product.product_tmpl_id.id),
                        ('type', 'in', ['normal', 'phantom']),
                        *company_domain,
                    ], limit=1)
                bom_cache[product.id] = bom
                return bom

            for sale_line in sale_lines:
                product = sale_line.product_id
                sale_qty = sale_line.qty or 0.0
                if not product or sale_qty <= 0:
                    continue

                bom = find_bom(product)
                if not bom:
                    continue

                selections = {}
                if 'modifier_json' in sale_line._fields and sale_line.modifier_json:
                    try:
                        selections = json.loads(sale_line.modifier_json) or {}
                    except Exception:
                        selections = {}
                only_bom_line_id = next(
                    (line_id for line_id, action in selections.items() if action == 'only'),
                    None
                )

                finished_factor = sale_qty / (bom.product_qty or 1.0)
                for bom_line in bom.bom_line_ids:
                    material = bom_line.product_id
                    if not material or (raw_category_ids and material.categ_id.id not in raw_category_ids):
                        continue

                    action = selections.get(str(bom_line.id))
                    if only_bom_line_id:
                        multiplier = 1 if str(bom_line.id) == only_bom_line_id else 0
                    elif action == 'remove':
                        multiplier = 0
                    elif action == 'extra':
                        multiplier = 2
                    else:
                        multiplier = 1

                    raw_qty = finished_factor * (bom_line.product_qty or 0.0) * multiplier
                    if raw_qty <= 0:
                        continue
                    try:
                        consumed_qty = bom_line.product_uom_id._compute_quantity(raw_qty, material.uom_id)
                        uom_name = material.uom_id.name
                    except Exception:
                        consumed_qty = raw_qty
                        uom_name = bom_line.product_uom_id.name or material.uom_id.name

                    item = usage.setdefault(material.id, {
                        'product_id': material.id,
                        'name': material.display_name,
                        'qty': 0.0,
                        'usage_value': 0.0,
                        'uom': uom_name,
                        'category': material.categ_id.complete_name,
                    })
                    item['qty'] += consumed_qty
                    item['usage_value'] += consumed_qty * (material.standard_price or 0.0)

            total_usage_value = sum(row['usage_value'] for row in usage.values())
            sort_key = 'usage_value' if total_usage_value else 'qty'
            top_sold = sorted(usage.values(), key=lambda row: row[sort_key], reverse=True)[:8]
            for item in top_sold:
                item['qty'] = round(item['qty'], 1)
                item['usage_value'] = round(item['usage_value'])
                item['usage_share'] = round(
                    (item['usage_value'] / total_usage_value * 100) if total_usage_value else 0,
                    1
                )
                item['chart_value'] = item['usage_share'] if total_usage_value else item['qty']
                item['chart_metric'] = 'Tỷ trọng giá trị tiêu hao' if total_usage_value else 'Số lượng tiêu thụ'
                item['uom'] = item.get('uom') or 'đơn vị'

        inventory_insights = []
        if top_sold:
            inventory_insights.append({
                'type': 'success',
                'title': u'Nguyên liệu tiêu hao trọng tâm',
                'body': u'%s chiếm %s%% giá trị tiêu hao nguyên liệu trong kỳ.' % (
                    top_sold[0]['name'], top_sold[0].get('usage_share', 0)),
            })
            top3_usage_share = round(sum(item.get('usage_share', 0) for item in top_sold[:3]), 1)
            inventory_insights.append({
                'type': 'info',
                'title': u'Cơ cấu tiêu hao',
                'body': u'Top 3 nguyên liệu chiếm %s%% tổng giá trị tiêu hao, nên ưu tiên theo dõi tồn kho nhóm này.' % top3_usage_share,
            })
        if low_stock:
            inventory_insights.append({
                'type': 'warning',
                'title': u'Nguyên liệu dưới định mức',
                'body': u'%s nguyên liệu thấp hơn mức tối thiểu, cần xem gợi ý nhập hàng.' % len(low_stock),
            })

        return {
            'low_stock': low_stock, 'low_stock_count': len(low_stock),
            'reorder_suggestions': reorder, 'top_sold': top_sold,
            'composition': {
                'top_material': top_sold[0]['name'] if top_sold else u'\u2014',
                'top_material_share': top_sold[0].get('usage_share', 0) if top_sold else 0,
                'top3_usage_share': round(sum(item.get('usage_share', 0) for item in top_sold[:3]), 1),
            },
            'statistical_insights': inventory_insights,
        }

    @http.route('/mcd_kds/inventory/create_purchase_order', type='json', auth='user', methods=['POST'])
    def create_inventory_purchase_order(self, product_id=None, qty=None, **kwargs):
        env = request.env
        if 'purchase.order' not in env.registry:
            return {'success': False, 'error': 'Module Mua hàng chưa được cài đặt.'}

        try:
            product_id = int(product_id or 0)
        except Exception:
            product_id = 0
        product = env['product.product'].sudo().browse(product_id).exists()
        if not product:
            return {'success': False, 'error': 'Không tìm thấy nguyên liệu cần nhập.'}

        orderpoint = env['stock.warehouse.orderpoint'].sudo().search([
            ('company_id', '=', env.company.id),
            ('product_id', '=', product.id),
        ], limit=1)
        qty_on_hand = getattr(orderpoint, 'qty_on_hand', product.qty_available)
        suggested_qty = max((orderpoint.product_max_qty or 0.0) - qty_on_hand, 0.0) if orderpoint else 0.0
        try:
            order_qty = float(qty or suggested_qty)
        except Exception:
            order_qty = suggested_qty
        if order_qty <= 0:
            return {'success': False, 'error': 'Số lượng gợi ý nhập phải lớn hơn 0.'}

        vendor = self._get_product_vendor(product)
        if not vendor:
            return {
                'success': False,
                'error': 'Nguyên liệu này chưa có nhà cung cấp. Hãy mở sản phẩm và thêm nhà cung cấp trước.',
            }

        purchase_order = env['purchase.order'].sudo().create({
            'partner_id': vendor.partner_id.id,
            'company_id': env.company.id,
            'origin': 'McDonald Dashboard - Gợi ý nhập hàng',
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': product.display_name,
                'product_qty': order_qty,
                'product_uom': (product.uom_po_id or product.uom_id).id,
                'price_unit': vendor.price or product.standard_price or 0.0,
                'date_planned': fields.Datetime.now(),
            })],
        })
        return {
            'success': True,
            'purchase_order_id': purchase_order.id,
            'purchase_order_name': purchase_order.name,
            'action': {
                'type': 'ir.actions.act_window',
                'name': 'Đơn mua hàng',
                'res_model': 'purchase.order',
                'res_id': purchase_order.id,
                'views': [[False, 'form']],
                'target': 'current',
            },
        }

    # 
    # WASTE SUBMIT
    # 
    def _get_waste_source_location(self, env):
        warehouse = env['stock.warehouse'].sudo().search([
            ('company_id', '=', env.company.id),
        ], limit=1)
        if warehouse and warehouse.lot_stock_id:
            return warehouse.lot_stock_id
        return env['stock.location'].sudo().search([
            ('usage', '=', 'internal'),
            ('company_id', 'in', [env.company.id, False]),
        ], limit=1)

    def _get_waste_scrap_location(self, env):
        Location = env['stock.location'].sudo()
        domain = [('usage', '=', 'inventory')]
        if 'scrap_location' in Location._fields:
            domain.insert(0, ('scrap_location', '=', True))
        location = Location.search(domain + [('company_id', 'in', [env.company.id, False])], limit=1)
        if location:
            return location
        return Location.search([('usage', '=', 'inventory')], limit=1)

    def _prepare_waste_scrap(self, env, product, qty, source_location, scrap_location, note):
        values = {
            'product_id': product.id,
            'scrap_qty': qty,
            'product_uom_id': product.uom_id.id,
            'location_id': source_location.id,
            'company_id': env.company.id,
            'origin': note or 'McDonald Dashboard - Waste',
        }
        if scrap_location:
            values['scrap_location_id'] = scrap_location.id
        return values

    @http.route('/mcd_kds/waste/submit', type='json', auth='user', methods=['POST'])
    def submit_waste(self, entries, waste_date=None):
        env = request.env
        WE  = env['mcd.waste.entry'].sudo()
        Product = env['product.product'].sudo()
        Scrap = env['stock.scrap'].sudo()
        source_location = self._get_waste_source_location(env)
        scrap_location = self._get_waste_scrap_location(env)
        tz = pytz.timezone(env.user.tz or 'UTC')
        now_local = datetime.now(tz)
        waste_datetime = datetime.utcnow()
        if waste_date:
            try:
                waste_day = datetime.strptime(waste_date, '%Y-%m-%d').date()
                if waste_day > now_local.date():
                    waste_day = now_local.date()
                local_waste_datetime = tz.localize(datetime.combine(waste_day, time(hour=18)))
                waste_datetime = local_waste_datetime.astimezone(pytz.utc).replace(tzinfo=None)
            except Exception:
                waste_datetime = datetime.utcnow()
        if not source_location:
            return {
                'success': False,
                'error': u'Không tìm thấy kho nguồn để trừ hàng huỷ.',
            }

        prepared = []
        for e in entries:
            product = Product.browse(int(e.get('product_id') or 0)).exists()
            if not product:
                return {
                    'success': False,
                    'error': u'Vui lòng chọn nguyên liệu có trong kho trước khi lưu hàng huỷ.',
                }
            try:
                qty = float(e.get('qty') or 0.0)
            except Exception:
                qty = 0.0
            if qty <= 0:
                return {
                    'success': False,
                    'error': u'Số lượng hàng huỷ phải lớn hơn 0.',
                }
            if product.tracking != 'none':
                return {
                    'success': False,
                    'error': u'%s đang quản lý theo lô/serial, cần xử lý huỷ trong màn hình kho.' % product.display_name,
                }
            prepared.append((e, product, qty))

        created = []
        for e, product, qty in prepared:
            scrap = Scrap.create(self._prepare_waste_scrap(
                env,
                product,
                qty,
                source_location,
                scrap_location,
                e.get('note') or 'McDonald Dashboard - Waste',
            ))
            scrap.action_validate()
            rec = WE.create({
                'product_id':   product.id,
                'scrap_id':     scrap.id,
                'qty':          qty,
                'unit_cost':    product.standard_price or 0.0,
                'reason':       e.get('reason', 'other'),
                'note':         e.get('note', ''),
                'employee_name': env.user.name,
                'date':         waste_datetime,
            })
            created.append({'waste_id': rec.id, 'scrap_id': scrap.id})
        return {'success': True, 'created': created}

    # 
    # WASTE REPORT
    # 
    @http.route('/mcd_kds/waste/report', type='json', auth='user', methods=['POST'])
    def get_waste_report(self, period='week', selected_date=None, selected_month=None, selected_year=None):
        env = request.env
        tz  = pytz.timezone(env.user.tz or 'UTC')
        df_utc, dt_utc, now, tz = _get_date_range(period, tz, selected_date, selected_month, selected_year)

        entries = env['mcd.waste.entry'].sudo().search([
            ('date', '>=', df_utc), ('date', '<=', dt_utc),
        ])

        total_loss = round(sum(e.total_loss for e in entries))
        total_qty  = sum(e.qty for e in entries)

        reason_map  = defaultdict(lambda: {'qty': 0, 'loss': 0.0})
        product_map = defaultdict(lambda: {'qty': 0, 'loss': 0.0})
        for e in entries:
            reason_map[e.reason]['qty']         += e.qty
            reason_map[e.reason]['loss']        += e.total_loss
            product_map[e.product_name]['qty']  += e.qty
            product_map[e.product_name]['loss'] += e.total_loss

        LABELS = {
            'expired': u'H\u1ebft h\u1ea1n',
            'wrong_order': u'L\u00e0m sai \u0111\u01a1n',
            'dropped': u'R\u01a1i v\u1ee1',
            'cancelled': u'Kh\u00e1ch h\u1ee7y',
            'cooking_error': u'H\u1ecfng ch\u1ebf bi\u1ebfn',
            'other': u'Kh\u00e1c',
        }
        by_reason  = sorted(
            [{'reason': LABELS.get(k, k), 'qty': v['qty'], 'loss': round(v['loss'])}
             for k, v in reason_map.items()], key=lambda x: x['qty'], reverse=True)
        by_product = sorted(
            [{'name': k, 'qty': v['qty'], 'loss': round(v['loss'])}
             for k, v in product_map.items()], key=lambda x: x['qty'], reverse=True)[:10]
        for row in by_reason:
            row['qty_share'] = _pct(row['qty'], total_qty)
            row['loss_share'] = _pct(row['loss'], total_loss)
        for row in by_product:
            row['qty_share'] = _pct(row['qty'], total_qty)
            row['loss_share'] = _pct(row['loss'], total_loss)

        prev_df_utc, prev_dt_utc = _previous_range(df_utc, dt_utc, tz, period, selected_date)
        prev_entries = env['mcd.waste.entry'].sudo().search([
            ('date', '>=', prev_df_utc), ('date', '<=', prev_dt_utc),
        ])
        prev_total_loss = round(sum(e.total_loss for e in prev_entries))
        prev_total_qty = sum(e.qty for e in prev_entries)
        insights = []
        if by_reason:
            insights.append({
                'type': 'warning' if by_reason[0]['qty_share'] >= 40 else 'info',
                'title': u'Lý do huỷ nổi bật',
                'body': u'%s chiếm %s%% tổng số lượng huỷ.' % (
                    by_reason[0]['reason'], by_reason[0]['qty_share']),
            })
        if total_loss:
            insights.append({
                'type': 'info',
                'title': u'Giá trị thất thoát',
                'body': u'Tổng thất thoát kỳ này là %s VND, thay đổi %s%% so với %s.' % (
                    f'{total_loss:,}', _pct_change(total_loss, prev_total_loss), _period_compare_label(period, selected_date)),
            })

        return {
            'total_loss': total_loss, 'total_qty': total_qty,
            'top_reason':  by_reason[0]['reason']  if by_reason  else u'\u2014',
            'top_product': by_product[0]['name']   if by_product else u'\u2014',
            'by_reason': by_reason, 'by_product': by_product,
            'comparison_label': _period_compare_label(period, selected_date),
            'comparison': {
                'qty': {
                    'current': total_qty,
                    'previous': prev_total_qty,
                    'delta': round(total_qty - prev_total_qty, 1),
                    'growth': _pct_change(total_qty, prev_total_qty),
                },
                'loss': {
                    'current': total_loss,
                    'previous': prev_total_loss,
                    'delta': total_loss - prev_total_loss,
                    'growth': _pct_change(total_loss, prev_total_loss),
                },
            },
            'statistical_insights': insights,
        }

    # 
    # PRODUCT DETAIL ANALYTICS
    # 
    @http.route('/mcd_kds/product_detail', type='json', auth='user', methods=['POST'])
    def get_product_detail(self, product_id, period='day', selected_date=None, selected_month=None, selected_year=None, **kwargs):
        """Chi tiet sản phẩm: doanh thu, so luong bán theo giờ/ngay, 
        tieu co, modifier, thời gian che bien, ty le huy, xu huong."""
        env = request.env
        tz = pytz.timezone(env.user.tz or 'UTC')
        df_utc, dt_utc, now, tz = _get_date_range(period, tz, selected_date, selected_month, selected_year)

        try:
            product_id = int(product_id)
        except:
            return {'error': 'Invalid product_id'}

        product = env['product.product'].sudo().browse(product_id)
        if not product:
            return {'error': 'Product not found'}

        #  OVERVIEW: Doanh thu & Loi nhuan 
        all_pos = env['pos.order'].sudo().search([
            ('state', 'in', ['paid', 'done', 'invoiced']),
            ('date_order', '>=', df_utc),
            ('date_order', '<=', dt_utc),
        ])
        pos_orders = _business_hours_filter(all_pos, tz, 'date_order')

        lines = env['pos.order.line'].sudo().search([
            ('product_id', '=', product_id),
            ('order_id', 'in', [o.id for o in pos_orders])
        ])

        total_qty = sum(l.qty for l in lines)
        total_revenue = sum(l.price_subtotal_incl for l in lines)
        order_count = len(lines.mapped('order_id'))
        raw_categories = env['product.category'].sudo().search([
            ('complete_name', 'ilike', 'Raw Materials'),
        ])
        raw_category_ids = set(raw_categories.ids)
        bom_cache = {}
        ingredient_usage = {}
        total_cost = 0.0
        for line in lines:
            usage_rows, line_cost = self._line_bom_usage(env, line, raw_category_ids, bom_cache)
            total_cost += line_cost
            for row in usage_rows:
                bucket = ingredient_usage.setdefault(row['product_id'], {
                    'product_id': row['product_id'],
                    'name': row['name'],
                    'qty': 0.0,
                    'uom': row['uom'],
                    'value': 0.0,
                    'category': row['category'],
                })
                bucket['qty'] += row['qty']
                bucket['value'] += row['value']
        profit = total_revenue - total_cost
        profit_margin = round((profit / total_revenue * 100) if total_revenue else 0, 1)

        # Sales trend follows the same dashboard date range.
        trend_labels = []
        trend_values = []

        date_from_local = pytz.utc.localize(df_utc).astimezone(tz)
        date_to_local = pytz.utc.localize(dt_utc).astimezone(tz)

        if period in ('day', 'custom') or selected_date:
            for h in range(OPEN_HOUR, CLOSE_HOUR + 1):
                trend_labels.append(f'{h:02d}h')
                trend_values.append(0)

            for line in lines:
                local_dt = line.order_id.date_order.replace(tzinfo=pytz.utc).astimezone(tz)
                hour_idx = local_dt.hour - OPEN_HOUR
                if 0 <= hour_idx < len(trend_values):
                    trend_values[hour_idx] += line.qty

        elif period == 'year':
            trend_labels = [f'T{month:02d}' for month in range(1, 13)]
            trend_values = [0 for _month in trend_labels]

            for line in lines:
                local_dt = line.order_id.date_order.replace(tzinfo=pytz.utc).astimezone(tz)
                month_key = f'T{local_dt.month:02d}'
                if month_key in trend_labels:
                    idx = trend_labels.index(month_key)
                    trend_values[idx] += line.qty

        else:
            current_day = date_from_local.date()
            last_day = date_to_local.date()
            while current_day <= last_day:
                trend_labels.append(current_day.strftime('%d/%m'))
                trend_values.append(0)
                current_day += timedelta(days=1)

            for line in lines:
                local_dt = line.order_id.date_order.replace(tzinfo=pytz.utc).astimezone(tz)
                day_key = local_dt.strftime('%d/%m')
                if day_key in trend_labels:
                    idx = trend_labels.index(day_key)
                    trend_values[idx] += line.qty

        sales_trend = {'labels': trend_labels, 'values': trend_values}

        hourly_values = {h: 0 for h in range(OPEN_HOUR, CLOSE_HOUR + 1)}
        for line in lines:
            local_dt = line.order_id.date_order.replace(tzinfo=pytz.utc).astimezone(tz)
            if OPEN_HOUR <= local_dt.hour <= CLOSE_HOUR:
                hourly_values[local_dt.hour] += line.qty
        sales_by_hour = {
            'labels': [f'{h:02d}h' for h in range(OPEN_HOUR, CLOSE_HOUR + 1)],
            'values': [round(hourly_values[h], 2) for h in range(OPEN_HOUR, CLOSE_HOUR + 1)],
        }

        #  INGREDIENTS: Nguyen lieu su dung 
        kitchen_lines = env['kitchen.order.line'].sudo().search([
            ('product_id', '=', product_id),
            ('order_id.state', '=', 'done'),
            ('order_id.serve_time', '>=', df_utc),
            ('order_id.serve_time', '<=', dt_utc),
        ])
        ingredient_total_value = sum(row['value'] for row in ingredient_usage.values())
        ingredients_consumed = sorted(
            ingredient_usage.values(),
            key=lambda row: row['value'] if ingredient_total_value else row['qty'],
            reverse=True,
        )[:8]
        for row in ingredients_consumed:
            row['qty'] = round(row['qty'], 1)
            row['value'] = round(row['value'])
            row['share'] = _pct(row['value'], ingredient_total_value)

        #  PERFORMANCE: Thoi gian che bien 
        k_durations = []
        for ko in kitchen_lines.mapped('order_id'):
            if ko.serve_time and ko.order_time:
                dur = int((ko.serve_time - ko.order_time).total_seconds())
                if 0 < dur <= 1800:
                    k_durations.append(dur)
        avg_cook_time = round(sum(k_durations) / len(k_durations)) if k_durations else 0

        # Modifier analytics from POS modifier fields.
        modifiers_map = defaultdict(int)
        for line in lines:
            modifier_note = (getattr(line, 'modifier_note', '') or '').strip()
            if modifier_note:
                for item in modifier_note.split(','):
                    label = self._clean_product_modifier_label(item, product)
                    if label:
                        modifiers_map[label] += 1
                continue

            modifier_json = (getattr(line, 'modifier_json', '') or '').strip()
            if modifier_json:
                try:
                    modifier_data = json.loads(modifier_json)
                except Exception:
                    modifier_data = {}
                if isinstance(modifier_data, dict):
                    for bom_line_id, action in modifier_data.items():
                        if action:
                            label = self._modifier_label_from_bom_action(env, bom_line_id, action)
                            label = self._clean_product_modifier_label(label, product)
                            if label:
                                modifiers_map[label] += 1
                continue

            if getattr(line, 'attribute_value_ids', None):
                for attr_val in line.attribute_value_ids:
                    label = self._clean_product_modifier_label(attr_val.name, product)
                    if label:
                        modifiers_map[label] += 1
        top_modifiers = sorted(
            [{'modifier': k, 'count': v} for k, v in modifiers_map.items()],
            key=lambda x: x['count'], reverse=True)[:5]

        #  INVENTORY: Kho sản phẩm 
        stock_qty = product.qty_available or 0
        stock_virtual = product.virtual_available or 0

        #  OFTEN BOUGHT TOGETHER 
        often_with = defaultdict(int)
        for order in lines.mapped('order_id'):
            co_products = set()
            for other_line in order.lines:
                if other_line.product_id.id != product_id:
                    co_products.add(other_line.product_id.display_name)
            for product_name in co_products:
                often_with[product_name] += 1

        often_with_list = sorted(
            [{'product': k, 'count': v} for k, v in often_with.items()],
            key=lambda x: x['count'], reverse=True)[:5]

        prev_df_utc, prev_dt_utc = _previous_range(df_utc, dt_utc, tz, period, selected_date)
        prev_stats = self._pos_stats(env, tz, prev_df_utc, prev_dt_utc)
        prev_lines = env['pos.order.line'].sudo().search([
            ('product_id', '=', product_id),
            ('order_id', 'in', [order.id for order in prev_stats['orders']]),
        ]) if prev_stats['orders'] else env['pos.order.line'].sudo().browse([])
        prev_qty = sum(line.qty for line in prev_lines)
        prev_revenue = sum(line.price_subtotal_incl for line in prev_lines)
        product_revenue_share = _pct(total_revenue, sum(order.amount_total for order in pos_orders))
        product_order_share = _pct(order_count, len(pos_orders))
        product_insights = []
        if total_revenue:
            product_insights.append({
                'type': 'success',
                'title': u'Đóng góp doanh thu',
                'body': u'%s chiếm %s%% doanh thu trong kỳ.' % (product.display_name, product_revenue_share),
            })
        if ingredients_consumed:
            product_insights.append({
                'type': 'info',
                'title': u'Nguyên liệu ảnh hưởng giá vốn',
                'body': u'%s là nguyên liệu chiếm tỷ trọng giá vốn cao nhất của món.' % ingredients_consumed[0]['name'],
            })
        if often_with_list:
            product_insights.append({
                'type': 'info',
                'title': u'Mua kèm phổ biến',
                'body': u'%s thường được mua kèm với %s.' % (product.display_name, often_with_list[0]['product']),
            })
        if top_modifiers:
            product_insights.append({
                'type': 'info',
                'title': u'Tùy chỉnh phổ biến',
                'body': u'Tùy chỉnh xuất hiện nhiều nhất là %s.' % top_modifiers[0]['modifier'],
            })

        return {
            'product_name': product.display_name,
            'product_id': product_id,
            'overview': {
                'revenue': round(total_revenue),
                'cost': round(total_cost),
                'profit': round(profit),
                'profit_margin': profit_margin,
                'total_qty': round(total_qty, 1),
                'order_count': order_count,
                'avg_qty_per_order': round(total_qty / order_count, 2) if order_count else 0,
                'avg_revenue_per_order': round(total_revenue / order_count) if order_count else 0,
                'revenue_share': product_revenue_share,
                'order_share': product_order_share,
            },
            'comparison_label': _period_compare_label(period, selected_date),
            'comparison': {
                'revenue': {
                    'current': round(total_revenue),
                    'previous': round(prev_revenue),
                    'delta': round(total_revenue - prev_revenue),
                    'growth': _pct_change(total_revenue, prev_revenue),
                },
                'qty': {
                    'current': round(total_qty, 1),
                    'previous': round(prev_qty, 1),
                    'delta': round(total_qty - prev_qty, 1),
                    'growth': _pct_change(total_qty, prev_qty),
                },
            },
            'sales_trend': sales_trend,
            'sales_by_hour': sales_by_hour,
            'ingredients': ingredients_consumed,
            'ingredient_total_value': round(ingredient_total_value),
            'performance': {
                'avg_cook_time': avg_cook_time,
            },
            'modifiers': top_modifiers,
            'inventory': {
                'qty_on_hand': round(stock_qty, 2),
                'qty_virtual': round(stock_virtual, 2),
            },
            'often_bought_together': often_with_list,
            'statistical_insights': product_insights,
        }

    # 
    # DEMO DATA ROUTES
    # 
    @http.route('/mcd_kds/demo/dashboard', type='json', auth='user', methods=['POST'])
    def demo_dashboard(self, period='day', selected_date=None, selected_hour=None, **kwargs):
        """Demo data cho Manager Dashboard"""
        return {
            'total_orders': 45,
            'total_revenue': 15_750_000,
            'avg_order_value': 350_000,
            'kitchen_avg_duration': 480,
            'expo_avg_duration': 240,
            'peak_hour': '12h',
            'top_products': [
                {'name': 'Big Mac', 'qty': 12.0, 'revenue': 3_600_000},
                {'name': 'Chicken Burger', 'qty': 10.0, 'revenue': 2_500_000},
                {'name': 'Fries M', 'qty': 15.0, 'revenue': 1_500_000},
                {'name': 'Cola L', 'qty': 18.0, 'revenue': 900_000},
                {'name': 'Chicken Nuggets', 'qty': 8.0, 'revenue': 1_200_000},
                {'name': 'Ice Cream', 'qty': 22.0, 'revenue': 1_650_000},
            ],
            'chart_revenue': {
                'labels': ['09h', '10h', '11h', '12h', '13h', '14h', '15h', '16h', '17h', '18h', '19h', '20h', '21h'],
                'values': [0.2, 0.4, 0.6, 2.8, 3.2, 1.5, 0.8, 0.9, 1.2, 2.1, 2.5, 1.6, 0.0],
            },
            'open_hour': OPEN_HOUR,
            'close_hour': CLOSE_HOUR,
        }

    @http.route('/mcd_kds/demo/kitchen', type='json', auth='user', methods=['POST'])
    def demo_kitchen(self, period='day'):
        """Demo data cho Kitchen Dashboard"""
        return {
            'avg_duration': 480,
            'fastest': 180,
            'slowest': 1200,
            'waiting_count': 5,
            'overdue_orders': [
                {'name': '#001', 'waited': 320, 'service_type': 'eat_in'},
                {'name': '#005', 'waited': 380, 'service_type': 'take_out'},
                {'name': '#012', 'waited': 520, 'service_type': 'eat_in'},
            ],
            'overdue_count': 3,
            'eat_in': 28,
            'take_out': 17,
            'top_items': [
                {'name': 'Big Mac', 'qty': 12},
                {'name': 'Chicken Burger', 'qty': 10},
                {'name': 'Fries M', 'qty': 15},
                {'name': 'Chicken Nuggets', 'qty': 8},
                {'name': 'McChicken', 'qty': 7},
                {'name': 'Double Cheeseburger', 'qty': 6},
                {'name': 'Filet-O-Fish', 'qty': 5},
                {'name': 'Apple Pie', 'qty': 4},
            ],
            'peak_hours': [
                {'label': '12h', 'orders': 18},
                {'label': '13h', 'orders': 15},
                {'label': '18h', 'orders': 12},
                {'label': '19h', 'orders': 10},
                {'label': '20h', 'orders': 8},
            ],
            'total_done': 45,
        }

    @http.route('/mcd_kds/demo/expo', type='json', auth='user', methods=['POST'])
    def demo_expo(self, period='day'):
        """Demo data cho Expo Dashboard"""
        return {
            'avg_duration': 240,
            'fastest': 60,
            'slowest': 540,
            'waiting_count': 3,
            'overdue_orders': [
                {'name': '#001', 'waited': 200, 'service_type': 'eat_in'},
            ],
            'overdue_count': 1,
            'eat_in': 28,
            'take_out': 17,
            'total_done': 45,
        }

    @http.route('/mcd_kds/demo/sales', type='json', auth='user', methods=['POST'])
    def demo_sales(self, period='day'):
        """Demo data cho Sales Dashboard"""
        return {
            'total_revenue': 15_750_000,
            'total_orders': 45,
            'avg_order_value': 350_000,
            'top_products': [
                {'name': 'Big Mac', 'qty': 12.0, 'revenue': 3_600_000},
                {'name': 'Chicken Burger', 'qty': 10.0, 'revenue': 2_500_000},
                {'name': 'Fries M', 'qty': 15.0, 'revenue': 1_500_000},
                {'name': 'Cola L', 'qty': 18.0, 'revenue': 900_000},
                {'name': 'Chicken Nuggets', 'qty': 8.0, 'revenue': 1_200_000},
                {'name': 'Ice Cream', 'qty': 22.0, 'revenue': 1_650_000},
                {'name': 'McFlurry', 'qty': 6.0, 'revenue': 600_000},
                {'name': 'Iced Coffee', 'qty': 14.0, 'revenue': 700_000},
                {'name': 'Orange Juice', 'qty': 10.0, 'revenue': 400_000},
                {'name': 'Hot Coffee', 'qty': 12.0, 'revenue': 300_000},
            ],
            'monthly_chart': {
                'labels': ['01/2026', '02/2026', '03/2026', '04/2026', '05/2026'],
                'values': [45.2, 52.8, 48.5, 61.3, 58.7],
            },
            'product_chart': {
                'labels': ['Big Mac', 'Chicken Burger', 'Fries M', 'Cola L', 'Chicken Nuggets', 'Ice Cream', 'McFlurry', 'Iced Coffee'],
                'values': [3.6, 2.5, 1.5, 0.9, 1.2, 1.65, 0.6, 0.7],
            },
        }

    @http.route('/mcd_kds/demo/inventory', type='json', auth='user', methods=['POST'])
    def demo_inventory(self, period='day'):
        """Demo data cho Inventory Dashboard"""
        return {
            'low_stock': [
                {'name': 'Beef Patty 10g', 'qty_on_hand': 2.5, 'min_qty': 20.0, 'uom': 'kg', 'urgency': 'critical', 'pct': 12},
                {'name': 'Chicken Fillet', 'qty_on_hand': 5.2, 'min_qty': 30.0, 'uom': 'kg', 'urgency': 'critical', 'pct': 17},
                {'name': 'Fries Frozen', 'qty_on_hand': 15.0, 'min_qty': 50.0, 'uom': 'kg', 'urgency': 'warning', 'pct': 30},
                {'name': 'Cola Syrup', 'qty_on_hand': 8.5, 'min_qty': 25.0, 'uom': 'L', 'urgency': 'warning', 'pct': 34},
            ],
            'low_stock_count': 4,
            'reorder_suggestions': [
                {'name': 'Beef Patty 10g', 'suggest_qty': 47.5, 'uom': 'kg'},
                {'name': 'Chicken Fillet', 'suggest_qty': 44.8, 'uom': 'kg'},
                {'name': 'Fries Frozen', 'suggest_qty': 35.0, 'uom': 'kg'},
                {'name': 'Cola Syrup', 'suggest_qty': 16.5, 'uom': 'L'},
                {'name': 'Lettuce Fresh', 'suggest_qty': 12.0, 'uom': 'kg'},
            ],
            'top_sold': [
                {'name': 'Beef Patty 10g', 'qty': 18.5},
                {'name': 'Fries Frozen', 'qty': 15.2},
                {'name': 'Chicken Fillet', 'qty': 12.8},
                {'name': 'Cheese Slice', 'qty': 22.0},
                {'name': 'Lettuce Fresh', 'qty': 11.5},
                {'name': 'Tomato Fresh', 'qty': 10.2},
                {'name': 'Cola Syrup', 'qty': 18.0},
                {'name': 'Pickle', 'qty': 8.5},
            ],
        }

    @http.route('/mcd_kds/demo/waste', type='json', auth='user', methods=['POST'])
    def demo_waste(self, period='day'):
        """Demo data cho Waste Report"""
        return {
            'total_loss': 1_250_000,
            'total_qty': 48,
            'top_reason': 'Ht hn',
            'top_product': 'Beef Patty',
            'by_reason': [
                {'reason': 'Ht hn', 'qty': 18, 'loss': 450_000},
                {'reason': 'Lam sai n', 'qty': 12, 'loss': 380_000},
                {'reason': 'Hỏng chế biến', 'qty': 10, 'loss': 250_000},
                {'reason': 'Ri vng', 'qty': 5, 'loss': 125_000},
                {'reason': 'Khach huy', 'qty': 3, 'loss': 45_000},
            ],
            'by_product': [
                {'name': 'Beef Patty', 'qty': 15, 'loss': 375_000},
                {'name': 'Chicken Fillet', 'qty': 10, 'loss': 300_000},
                {'name': 'Fries', 'qty': 8, 'loss': 160_000},
                {'name': 'Bun', 'qty': 12, 'loss': 120_000},
                {'name': 'Cheese Slice', 'qty': 3, 'loss': 150_000},
            ],
        }
