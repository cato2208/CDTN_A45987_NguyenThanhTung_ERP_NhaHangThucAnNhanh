import json
import math
import random
import unicodedata
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError


class McdDashboardDemoDataWizard(models.TransientModel):
    _name = 'mcd.dashboard.demo.data.wizard'
    _description = 'Generate MCD Dashboard Demo Data'

    months = fields.Integer(string='Months', default=13, required=True)
    clear_existing = fields.Boolean(string='Clear existing MCD demo data', default=True)
    weekday_orders = fields.Integer(string='Weekday orders/day', default=24, required=True)
    weekend_orders = fields.Integer(string='Weekend orders/day', default=42, required=True)
    holiday_orders = fields.Integer(string='Holiday orders/day', default=64, required=True)
    kiosk_ratio = fields.Float(string='Kiosk ratio', default=0.72, required=True)

    def action_generate(self):
        self.ensure_one()
        generator = self.env['mcd.dashboard.demo.data.generator'].sudo()
        summary = generator.generate(
            months=max(self.months, 1),
            clear_existing=self.clear_existing,
            weekday_orders=max(self.weekday_orders, 1),
            weekend_orders=max(self.weekend_orders, 1),
            holiday_orders=max(self.holiday_orders, 1),
            kiosk_ratio=min(max(self.kiosk_ratio, 0.0), 1.0),
        )
        message = (
            'Generated %(orders)s POS orders, %(kiosk)s kiosk orders, '
            '%(customers)s customers, %(products)s products from %(start_date)s to %(end_date)s.'
        ) % summary
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Dashboard demo data',
                'message': message,
                'type': 'success',
                'sticky': False,
            },
        }


class McdDashboardDemoDataGenerator(models.AbstractModel):
    _name = 'mcd.dashboard.demo.data.generator'
    _description = 'MCD Dashboard Demo Data Generator'

    DEMO_PREFIX = 'MCDDEMO'
    KIOSK_PREFIX = 'KIOSKDEMO'

    PRODUCT_CATALOG = [
        ('Big Mac', 99000, 1.45),
        ('McChicken', 79000, 1.2),
        ('Cheeseburger', 69000, 1.05),
        ('Double Cheeseburger', 95000, 1.05),
        ('Spicy Chicken Burger', 89000, 0.95),
        ('Chicken McNuggets 6pcs', 75000, 1.15),
        ('Chicken McNuggets 10pcs', 119000, 0.85),
        ('French Fries Medium', 45000, 1.45),
        ('French Fries Large', 59000, 1.25),
        ('Coca Cola Medium', 35000, 1.3),
        ('Coca Cola Large', 42000, 0.95),
        ('McCafe Iced Coffee', 55000, 0.75),
        ('Sundae Chocolate', 49000, 0.82),
        ('Apple Pie', 39000, 0.55),
        ('Happy Meal', 99000, 0.72),
        ('McSpicy Deluxe Meal', 145000, 0.88),
        ('Family Share Box', 249000, 0.38),
    ]

    VIETNAMESE_FAMILY_NAMES = [
        'Nguyen', 'Tran', 'Le', 'Pham', 'Hoang', 'Huynh', 'Phan', 'Vu',
        'Vo', 'Dang', 'Bui', 'Do', 'Ho', 'Ngo', 'Duong', 'Ly',
    ]
    VIETNAMESE_GIVEN_NAMES = [
        'Minh Anh', 'Hoang Nam', 'Gia Han', 'Bao Ngoc', 'Tuấn Kiệt', 'Phuong Linh',
        'Quang Huy', 'Khanh Vy', 'Minh Quan', 'Thao Nhi', 'Duc Anh', 'Mai Chi',
        'Thanh Dat', 'Ngoc Tram', 'Anh Thu', 'Gia Bao', 'Quoc Viet', 'Nhat Linh',
        'Minh Chau', 'Bao Tran', 'Hai Dang', 'Thu Ha', 'Minh Khoi', 'Yen Nhi',
        'Gia Huy', 'Kim Ngan', 'Anh Khoa', 'Bao Han', 'Hoang Phuc', 'Thanh Truc',
    ]
    ENGLISH_FIRST_NAMES = [
        'James', 'Olivia', 'William', 'Emma', 'Benjamin', 'Sophia', 'Lucas', 'Ava',
        'Henry', 'Mia', 'Alexander', 'Charlotte', 'Daniel', 'Amelia', 'Matthew',
        'Grace', 'Ethan', 'Lily', 'Logan', 'Emily', 'Noah', 'Chloe', 'Jack', 'Ella',
    ]
    ENGLISH_LAST_NAMES = [
        'Smith', 'Johnson', 'Brown', 'Taylor', 'Anderson', 'Wilson', 'Martin',
        'Thompson', 'Lee', 'Clark', 'Walker', 'Hall', 'Allen', 'Young', 'King',
    ]
    VIETNAMESE_MIDDLE_NAMES = [
        'Van', 'Thi', 'Minh', 'Gia', 'Bao', 'Hoang', 'Thanh', 'Ngoc',
        'Quoc', 'Anh', 'Duc', 'Nhat',
    ]
    ENGLISH_MIDDLE_NAMES = [
        'John', 'Rose', 'Michael', 'Anne', 'David', 'Kate', 'Thomas', 'Jane',
    ]

    MODIFIER_NOTES = [
        '',
        '',
        '',
        'No onion',
        'Extra cheese',
        'Less ice',
        'Extra sauce',
        'No pickle',
        'Large combo',
    ]

    @api.model
    def generate(self, months=6, clear_existing=True, weekday_orders=24, weekend_orders=42,
                 holiday_orders=64, kiosk_ratio=0.72):
        session = self._get_or_open_session()
        if clear_existing:
            self._clear_existing_demo_data()
            self.env.cr.commit()
        products = self._get_or_create_products()
        customers = self._get_or_create_customers()
        payment_methods = self._get_payment_methods(session)

        tz = pytz.timezone(self.env.user.tz or 'Asia/Bangkok')
        today = fields.Date.context_today(self)
        start_date = self._start_date(today, months)
        end_date = (today.replace(day=1) - timedelta(days=1)) if months >= 12 else today
        dates = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        holidays = set()
        for year in range(start_date.year, end_date.year + 1):
            holidays |= self._holiday_dates(year)
        month_day_weights = self._month_day_weights(dates, holidays)
        month_targets = self._month_revenue_targets(start_date, end_date)

        rng = random.Random(20260531)
        customer_queue = self._build_customer_queue(customers, rng)
        order_count = 0
        kiosk_count = 0
        sequence = max(session.sequence_number or 1, 1)
        monthly_revenue = defaultdict(float)
        monthly_orders = defaultdict(int)
        payment_stats = defaultdict(int)
        source_stats = defaultdict(int)
        display_cutoff = end_date - timedelta(days=21)

        for current_date in dates:
            month_key = current_date.strftime('%Y-%m')
            daily_revenue_target = self._daily_revenue_target(
                current_date, holidays, month_targets[month_key], month_day_weights[month_key], rng
            )
            daily_revenue = 0.0
            daily_index = 0
            while daily_revenue < daily_revenue_target:
                source = self._weighted_source(current_date, kiosk_ratio, rng)
                if source == 'kiosk':
                    kiosk_count += 1
                local_dt = self._weighted_order_datetime(current_date, rng, tz)
                order_products = self._pick_order_lines(products, rng, local_dt.hour)
                partner = False
                if customers and rng.random() < self._known_customer_ratio(source, local_dt.hour):
                    partner = customer_queue.pop() if customer_queue else rng.choice(customers)
                service_type = self._weighted_service_type(source, local_dt.hour, rng)
                payment_method = self._weighted_payment_method(payment_methods, rng)
                pos_order = self._create_pos_order(
                    session=session,
                    products=order_products,
                    partner=partner,
                    service_type=service_type,
                    payment_method=payment_method,
                    local_dt=local_dt,
                    sequence=sequence,
                    source=source,
                    daily_index=daily_index,
                    create_display=(current_date >= display_cutoff or rng.random() < 0.03),
                )
                if source == 'kiosk':
                    self._create_kiosk_order(pos_order, order_products, partner, service_type, payment_method)
                daily_revenue += pos_order.amount_total
                monthly_revenue[month_key] += pos_order.amount_total
                monthly_orders[month_key] += 1
                payment_stats[self._payment_key(payment_method)] += 1
                source_stats[source] += 1
                sequence += 1
                order_count += 1
                daily_index += 1
                if order_count % 500 == 0:
                    self.env.cr.commit()

        session.write({'sequence_number': sequence})
        self._recompute_demo_customer_stats(start_date, end_date)
        self.env.cr.commit()
        return {
            'orders': order_count,
            'kiosk': kiosk_count,
            'customers': len(customers),
            'products': len(products),
            'months': months,
            'start_date': start_date,
            'end_date': end_date,
            'monthly_revenue': dict(monthly_revenue),
            'monthly_orders': dict(monthly_orders),
            'payment_stats': dict(payment_stats),
            'source_stats': dict(source_stats),
        }

    def _clear_existing_demo_data(self):
        KioskOrder = self.env['mcd.kiosk.order']
        while True:
            kiosk_orders = KioskOrder.search([('name', '=like', self.KIOSK_PREFIX + '/%')], limit=1000)
            if not kiosk_orders:
                break
            kiosk_orders.unlink()
            self.env.cr.commit()

        PosOrder = self.env['pos.order']
        while True:
            demo_orders = PosOrder.search([('pos_reference', '=like', self.DEMO_PREFIX + '/%')], limit=1000)
            if not demo_orders:
                break
            demo_orders.mapped('payment_ids').sudo().unlink()
            demo_orders.write({'state': 'cancel'})
            demo_orders.unlink()
            self.env.cr.commit()

        demo_products = self.env['product.product'].search([('default_code', '=like', 'MCD-DEMO-%')])
        if demo_products:
            try:
                demo_products.unlink()
            except Exception:
                demo_products.write({
                    'active': False,
                    'available_in_pos': False,
                    'sale_ok': False,
                })
        Partner = self.env['res.partner']
        demo_partners = Partner.search([
            '|',
            ('ref', '=like', self.DEMO_PREFIX + '-CUSTOMER-%'),
            '&', ('phone', '=like', '0977%'), ('mcd_is_customer', '=', True),
        ])
        demo_partners |= Partner.search([('phone', '=like', '09260000%'), ('mcd_is_customer', '=', True)])
        demo_partners.write({
            'mcd_order_count': 0,
            'mcd_total_spent': 0.0,
            'mcd_loyalty_points': 0,
            'mcd_first_order': False,
            'mcd_last_order': False,
        })

    def _get_or_open_session(self):
        PosSession = self.env['pos.session']
        session = PosSession.search([('state', 'in', ('opened', 'opening_control'))], order='id desc', limit=1)
        if session:
            if session.state == 'opening_control':
                session.action_pos_session_open()
            return session

        config = self.env['pos.config'].search([], order='id asc', limit=1)
        if not config:
            raise UserError('Create a POS configuration before generating dashboard demo data.')

        session = PosSession.create({'config_id': config.id})
        session.action_pos_session_open()
        return session

    def _get_or_create_products(self):
        Product = self.env['product.product']
        existing_products = Product.search([
            ('active', '=', True),
            ('sale_ok', '=', True),
            ('available_in_pos', '=', True),
            '|', ('default_code', '=', False), ('default_code', 'not ilike', 'MCD-DEMO-'),
        ], order='sequence, name, id')
        existing_products = existing_products.filtered(
            lambda product: product.lst_price > 0 and product.product_tmpl_id.active
        )
        if existing_products:
            return list(existing_products)

        category = self.env['product.category'].search([('name', '=', 'MCD Demo Menu')], limit=1)
        if not category:
            category = self.env['product.category'].create({'name': 'MCD Demo Menu'})

        products = []
        for name, price, _weight in self.PRODUCT_CATALOG:
            product = Product.search([('default_code', '=', 'MCD-DEMO-' + self._slug(name))], limit=1)
            values = {
                'name': name,
                'default_code': 'MCD-DEMO-' + self._slug(name),
                'list_price': price,
                'standard_price': price * 0.45,
                'type': 'consu',
                'sale_ok': True,
                'available_in_pos': True,
                'categ_id': category.id,
                'taxes_id': [(6, 0, [])],
            }
            if product:
                product.write(values)
            else:
                product = Product.create(values)
            products.append(product)
        return products

    def _get_or_create_customers(self):
        Partner = self.env['res.partner']
        rng = random.Random(20260530)
        partners = []
        customers = self._customer_profiles(rng, target_count=3500)
        for index, profile in enumerate(customers, start=1):
            phone = '0977%06d' % index
            ref = '%s-CUSTOMER-%04d' % (self.DEMO_PREFIX, index)
            partner = Partner.search(['|', ('ref', '=', ref), ('phone', '=', phone)], limit=1)
            values = {
                'name': profile['name'],
                'ref': ref,
                'phone': phone,
                'mobile': phone,
                'email': profile['email'],
                'street': profile['street'],
                'city': profile['city'],
                'customer_rank': 1,
                'mcd_is_customer': True,
                'mcd_birthdate': profile['birthdate'],
            }
            if partner:
                partner.write(values)
            else:
                partner = Partner.create(values)
            partners.append(partner)
        return partners

    def _customer_profiles(self, rng, target_count=3500):
        profiles = []
        for family in self.VIETNAMESE_FAMILY_NAMES:
            for given in self.VIETNAMESE_GIVEN_NAMES:
                profiles.append({'name': '%s %s' % (family, given)})
                for middle in self.VIETNAMESE_MIDDLE_NAMES[:4]:
                    profiles.append({'name': '%s %s %s' % (family, middle, given)})

        for first in self.ENGLISH_FIRST_NAMES:
            for last in self.ENGLISH_LAST_NAMES:
                profiles.append({'name': '%s %s' % (first, last)})
                for middle in self.ENGLISH_MIDDLE_NAMES[:3]:
                    profiles.append({'name': '%s %s %s' % (first, middle, last)})

        rng.shuffle(profiles)
        profiles = profiles[:target_count]
        districts = [
            'Quan 1', 'Quan 3', 'Quan 7', 'Binh Thanh', 'Phu Nhuan',
            'Thu Duc', 'Tan Binh', 'Go Vap', 'District 2', 'District 4',
        ]
        for index, profile in enumerate(profiles, start=1):
            year = rng.randint(1975, 2007)
            month = rng.randint(1, 12)
            day = rng.randint(1, 28)
            profile['birthdate'] = fields.Date.to_date('%04d-%02d-%02d' % (year, month, day))
            slug = self._slug(profile['name']).lower().replace('-', '.')
            profile['email'] = '%s.%04d@example.com' % (slug[:32], index)
            profile['street'] = '%s Nguyen Trai' % rng.randint(1, 299)
            profile['city'] = rng.choice(districts)
        return profiles

    def _start_date(self, today, months):
        if months >= 12:
            return date(today.year - 1, 6, 1)
        return today - timedelta(days=months * 30)

    def _month_revenue_targets(self, start_date, end_date):
        targets = {}
        current = date(start_date.year, start_date.month, 1)
        while current <= end_date:
            base = 1_280_000_000
            if current.month in (6, 7, 8):
                base = 1_420_000_000
            if current.month in (9, 10):
                base = 1_180_000_000
            if current.month == 11:
                base = 1_520_000_000
            if current.month == 12:
                base = 2_250_000_000
            if current.month in (1, 2):
                base = 2_080_000_000
            if current.month in (4, 5):
                base = 1_680_000_000
            targets[current.strftime('%Y-%m')] = base
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
        return targets

    def _month_day_weights(self, dates, holidays):
        weights = defaultdict(float)
        for day in dates:
            weights[day.strftime('%Y-%m')] += self._day_weight(day, holidays)
        return weights

    def _day_weight(self, day, holidays):
        if day in holidays:
            return 2.15
        if day.weekday() >= 5:
            return 1.55
        if day.weekday() == 4:
            return 1.18
        return 1.0

    def _daily_revenue_target(self, day, holidays, month_target, month_weight, rng):
        ratio = self._day_weight(day, holidays) / max(month_weight, 1.0)
        noise = rng.uniform(0.94, 1.08)
        return month_target * ratio * noise

    def _build_customer_queue(self, customers, rng):
        queue = []
        for index, partner in enumerate(customers):
            pct = index / max(len(customers), 1)
            if pct < 0.15:
                count = 1
            elif pct < 0.40:
                count = rng.randint(2, 4)
            elif pct < 0.75:
                count = rng.randint(6, 12)
            elif pct < 0.95:
                count = rng.randint(14, 24)
            else:
                count = rng.randint(35, 68)
            queue.extend([partner] * count)
        rng.shuffle(queue)
        return queue

    def _get_payment_methods(self, session):
        methods = session.payment_method_ids
        if not methods:
            raise UserError('The opened POS session has no payment methods.')

        def find_method(keys):
            found = methods.filtered(lambda method: any(key in self._ascii_lower(method.name or '') for key in keys))
            return found[:1] or methods[:1]

        return {
            'cash': find_method(['cash', 'tien mat']),
            'card': find_method(['card', 'the']),
            'qr': find_method(['qr', 'ma qr', 'bank', 'ngan hang', 'transfer', 'chuyen khoan']),
        }

    def _orders_for_day(self, day, holidays, weekday_orders, weekend_orders, holiday_orders, rng):
        if day in holidays:
            base = holiday_orders
        elif day.weekday() >= 5:
            base = weekend_orders
        else:
            base = weekday_orders

        seasonal = 1 + 0.12 * math.sin(day.toordinal() / 9.0)
        noise = rng.uniform(0.84, 1.18)
        return max(8, int(base * seasonal * noise))

    def _weighted_source(self, day, kiosk_ratio, rng):
        ratio = kiosk_ratio
        if day.weekday() >= 5:
            ratio += 0.05
        return 'kiosk' if rng.random() < min(max(ratio, 0.45), 0.82) else 'pos'

    def _known_customer_ratio(self, source, hour):
        ratio = 0.74 if source == 'kiosk' else 0.68
        if hour in (11, 12, 18, 19, 20):
            ratio += 0.08
        return min(ratio, 0.88)

    def _weighted_service_type(self, source, hour, rng):
        takeout_ratio = 0.64 if source == 'kiosk' else 0.54
        if hour in (11, 12, 13):
            takeout_ratio -= 0.08
        if hour in (18, 19, 20):
            takeout_ratio += 0.07
        return 'take_out' if rng.random() < takeout_ratio else 'eat_in'

    def _weighted_order_datetime(self, day, rng, tz):
        hours = list(range(8, 23))
        weights = []
        for hour in hours:
            lunch_peak = math.exp(-((hour - 12) ** 2) / 2.0)
            dinner_peak = math.exp(-((hour - 19) ** 2) / 2.0)
            base = 0.16
            weights.append(base + 3.8 * lunch_peak + 4.3 * dinner_peak)
        hour = rng.choices(hours, weights=weights, k=1)[0]
        minute = int(max(0, min(59, rng.gauss(30, 17))))
        second = rng.randint(0, 59)
        local_dt = tz.localize(datetime.combine(day, time(hour, minute, second)))
        return local_dt

    def _pick_order_lines(self, products, rng, hour):
        weighted = []
        for product in products:
            weight = self._product_order_weight(product, hour)
            if weight > 0:
                weighted.append((product, weight))
        if not weighted:
            raise UserError('No POS products are available for dashboard demo orders.')

        line_count = rng.choices([2, 3, 4, 5, 6], weights=[0.18, 0.34, 0.28, 0.14, 0.06], k=1)[0]
        chosen = rng.choices([item[0] for item in weighted], weights=[item[1] for item in weighted], k=line_count)
        result = []
        for product in chosen:
            qty = rng.choices([1, 2, 3, 4], weights=[0.68, 0.24, 0.06, 0.02], k=1)[0]
            note = rng.choice(self.MODIFIER_NOTES)
            extra = 8000 if note in ('Extra cheese', 'Extra sauce', 'Large combo') else 0
            price_unit = product.lst_price or product.list_price or product.standard_price or 1000
            result.append({
                'product': product,
                'qty': qty,
                'price_unit': price_unit,
                'modifier_note': note,
                'modifier_price_extra': extra,
            })
        return result

    def _product_order_weight(self, product, hour):
        name = self._ascii_lower(product.display_name or product.name or '')
        price = product.lst_price or product.list_price or 0.0
        if price <= 0:
            return 0.0

        weight = 1.0
        if any(key in name for key in ['combo', 'meal', 'set', 'phan an']):
            weight = 1.18
        elif any(key in name for key in ['burger', 'big mac', 'chicken', 'ga', 'beef', 'bo']):
            weight = 1.32
        elif any(key in name for key in ['fries', 'khoai']):
            weight = 1.22
        elif any(key in name for key in ['coca', 'cola', 'pepsi', 'sprite', 'fanta', 'coffee', 'cafe', 'tra', 'drink', 'nuoc']):
            weight = 1.08
        elif any(key in name for key in ['ice', 'cream', 'sundae', 'flurry', 'kem', 'pie']):
            weight = 0.82

        if price >= 180000:
            weight *= 0.45
        elif price >= 120000:
            weight *= 0.75
        elif price <= 35000:
            weight *= 1.14

        if hour in (11, 12, 13, 18, 19, 20) and any(key in name for key in ['combo', 'meal', 'burger', 'chicken', 'ga']):
            weight *= 1.35
        return weight

    def _weighted_payment_method(self, payment_methods, rng):
        key = rng.choices(['cash', 'card', 'qr'], weights=[0.27, 0.34, 0.39], k=1)[0]
        return payment_methods[key]

    def _create_pos_order(self, session, products, partner, service_type, payment_method,
                          local_dt, sequence, source, daily_index, create_display=False):
        utc_dt = local_dt.astimezone(pytz.utc).replace(tzinfo=None)
        order_name = '%s/%s/%05d' % (self.DEMO_PREFIX, source.upper(), sequence)
        line_commands = []
        total_excluded = 0
        total_included = 0

        for line in products:
            product = line['product'].with_company(session.company_id)
            qty = line['qty']
            price_unit = line['price_unit'] + line['modifier_price_extra']
            taxes = product.taxes_id.filtered(lambda tax: tax.company_id == session.company_id)
            tax_result = taxes.compute_all(
                price_unit,
                session.currency_id,
                qty,
                product=product,
                partner=partner,
            )
            total_excluded += tax_result['total_excluded']
            total_included += tax_result['total_included']
            modifier_payload = {
                'source': source,
                'note': line['modifier_note'],
                'price_extra': line['modifier_price_extra'],
            }
            line_commands.append((0, 0, {
                'name': 'MCD-DEMO-LINE-%s-%s' % (sequence, len(line_commands) + 1),
                'product_id': product.id,
                'full_product_name': product.display_name,
                'qty': qty,
                'price_unit': price_unit,
                'price_subtotal': tax_result['total_excluded'],
                'price_subtotal_incl': tax_result['total_included'],
                'discount': 0,
                'tax_ids': [(6, 0, taxes.ids)],
                'uuid': str(uuid.uuid4()),
                'modifier_note': line['modifier_note'],
                'modifier_json': json.dumps(modifier_payload, ensure_ascii=False),
                'modifier_price_extra': line['modifier_price_extra'],
            }))

        amount_tax = total_included - total_excluded
        vals = {
            'name': order_name,
            'pos_reference': order_name,
            'session_id': session.id,
            'company_id': session.company_id.id,
            'user_id': self.env.user.id,
            'partner_id': partner.id if partner else False,
            'date_order': fields.Datetime.to_string(utc_dt),
            'sequence_number': sequence,
            'pricelist_id': session.config_id.pricelist_id.id,
            'amount_tax': amount_tax,
            'amount_total': total_included,
            'amount_paid': total_included,
            'amount_return': 0,
            'state': 'paid',
            'lines': line_commands,
            'last_order_preparation_change': '{}',
        }
        if 'mcd_service_type' in self.env['pos.order']._fields:
            vals['mcd_service_type'] = service_type
        if 'service_type' in self.env['pos.order']._fields:
            vals['service_type'] = service_type

        order = self.env['pos.order'].create(vals)
        self.env['pos.payment'].create({
            'name': payment_method.name,
            'pos_order_id': order.id,
            'amount': total_included,
            'payment_method_id': payment_method.id,
            'payment_date': fields.Datetime.to_string(utc_dt),
            'transaction_id': '%s-%05d-%02d' % (source.upper(), sequence, daily_index),
            'payment_status': 'done',
        })

        if create_display and hasattr(order, '_send_to_kitchen'):
            kitchen_order = order._send_to_kitchen()
            self._age_display_order(kitchen_order, utc_dt, source, 'kitchen')
        if create_display and hasattr(order, '_send_to_expo'):
            expo_order = order._send_to_expo()
            self._age_display_order(expo_order, utc_dt, source, 'expo')
        return order

    def _recompute_demo_customer_stats(self, start_date, end_date):
        partners = self.env['res.partner'].search([('ref', '=like', self.DEMO_PREFIX + '-CUSTOMER-%')])
        if not partners:
            return
        partners.write({
            'mcd_order_count': 0,
            'mcd_total_spent': 0.0,
            'mcd_loyalty_points': 0,
            'mcd_first_order': False,
            'mcd_last_order': False,
        })
        orders = self.env['pos.order'].search([
            ('pos_reference', '=like', self.DEMO_PREFIX + '/%'),
            ('partner_id', 'in', partners.ids),
            ('date_order', '>=', fields.Date.to_string(start_date)),
            ('date_order', '<=', fields.Date.to_string(end_date + timedelta(days=1))),
        ], order='partner_id,date_order')
        stats = {}
        for order in orders:
            stat = stats.setdefault(order.partner_id.id, {
                'count': 0,
                'spent': 0.0,
                'first': order.date_order,
                'last': order.date_order,
            })
            stat['count'] += 1
            stat['spent'] += order.amount_total
            stat['first'] = min(stat['first'], order.date_order)
            stat['last'] = max(stat['last'], order.date_order)
        for partner in partners:
            stat = stats.get(partner.id)
            if not stat:
                continue
            partner.write({
                'mcd_order_count': stat['count'],
                'mcd_total_spent': stat['spent'],
                'mcd_loyalty_points': int(stat['spent'] // 1000),
                'mcd_first_order': stat['first'],
                'mcd_last_order': stat['last'],
            })

    def _age_display_order(self, display_order, utc_dt, source, stage):
        if not display_order:
            return
        display_order = display_order.sudo()
        is_recent = utc_dt.date() >= (fields.Datetime.now() - timedelta(days=1)).date()
        rng = random.Random(display_order.id * (3 if stage == 'kitchen' else 7))
        roll = rng.random()
        if roll < 0.08:
            duration_seconds = rng.randint(30, 40)
        elif roll < 0.9:
            duration_seconds = rng.randint(
                150,
                270,
            )
        else:
            duration_seconds = rng.randint(
                420,
                480,
            )
        values = {
            'order_time': fields.Datetime.to_string(utc_dt),
        }
        values['state'] = 'done'
        values['serve_time'] = fields.Datetime.to_string(utc_dt + timedelta(seconds=duration_seconds))
        display_order.write(values)

    def _create_kiosk_order(self, pos_order, products, partner, service_type, payment_method):
        KioskOrder = self.env['mcd.kiosk.order']
        line_commands = []
        for line in products:
            line_commands.append((0, 0, {
                'product_id': line['product'].id,
                'qty': line['qty'],
                'price_unit': line['price_unit'],
                'modifier_note': line['modifier_note'],
                'modifier_price_extra': line['modifier_price_extra'],
            }))
        return KioskOrder.create({
            'name': '%s/%s' % (self.KIOSK_PREFIX, pos_order.sequence_number),
            'service_type': service_type,
            'state': 'paid',
            'payment_method': self._payment_key(payment_method),
            'partner_id': partner.id if partner else False,
            'pos_order_id': pos_order.id,
            'line_ids': line_commands,
        })

    def _payment_key(self, payment_method):
        name = self._ascii_lower(payment_method.name or '')
        if 'qr' in name or 'transfer' in name or 'chuyen' in name or 'bank' in name or 'ngan hang' in name:
            return 'qr'
        if 'card' in name or 'the' in name:
            return 'card'
        return 'cash'

    def _ascii_lower(self, value):
        return ''.join(
            char for char in unicodedata.normalize('NFKD', value)
            if not unicodedata.combining(char)
        ).lower()

    def _holiday_dates(self, year):
        holidays = {
            fields.Date.to_date('%s-01-01' % year),
            fields.Date.to_date('%s-02-14' % year),
            fields.Date.to_date('%s-02-15' % year),
            fields.Date.to_date('%s-02-16' % year),
            fields.Date.to_date('%s-02-17' % year),
            fields.Date.to_date('%s-02-18' % year),
            fields.Date.to_date('%s-02-19' % year),
            fields.Date.to_date('%s-02-20' % year),
            fields.Date.to_date('%s-02-21' % year),
            fields.Date.to_date('%s-02-22' % year),
            fields.Date.to_date('%s-04-30' % year),
            fields.Date.to_date('%s-05-01' % year),
        }
        return holidays

    def _slug(self, value):
        return ''.join(ch if ch.isalnum() else '-' for ch in value.upper()).strip('-')
