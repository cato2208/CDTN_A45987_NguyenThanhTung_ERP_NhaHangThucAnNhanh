from odoo import api, fields, models


class McdCustomer(models.Model):
    """
    Extend res.partner with the customer metrics used by the POS loyalty flow.
    """
    _inherit = 'res.partner'

    mcd_is_customer = fields.Boolean(string='Khách hàng MCD', default=False)
    mcd_birthdate = fields.Date(string='Ngày sinh')
    mcd_first_order = fields.Datetime(string='Lần đầu mua', readonly=True)
    mcd_last_order = fields.Datetime(string='Lần cuối mua', readonly=True)
    mcd_order_count = fields.Integer(string='Tổng số đơn', default=0, readonly=True)
    mcd_total_spent = fields.Float(string='Tổng chi tiêu', default=0.0, readonly=True)
    mcd_loyalty_points = fields.Integer(string='Điểm tích lũy', default=0, readonly=True)
    mcd_is_new = fields.Boolean(string='Khách mới', compute='_compute_is_new', store=False)

    @api.depends('mcd_order_count')
    def _compute_is_new(self):
        for rec in self:
            rec.mcd_is_new = rec.mcd_order_count == 0

    @api.model
    def mcd_normalize_phone(self, phone):
        return ''.join(filter(str.isdigit, phone or ''))

    @api.model
    def _mcd_phone_domain(self, phone_clean):
        alt = phone_clean
        if alt.startswith('84'):
            alt = '0' + alt[2:]
        elif alt.startswith('0'):
            alt = '84' + alt[1:]
        return [
            '|', '|', '|',
            ('phone', 'ilike', phone_clean),
            ('mobile', 'ilike', phone_clean),
            ('phone', 'ilike', alt),
            ('mobile', 'ilike', alt),
        ]

    @api.model
    def _mcd_find_partner_by_phone(self, phone_clean):
        if not phone_clean:
            return self.browse()
        domain = [
            ('active', '=', True),
        ] + self._mcd_phone_domain(phone_clean)
        return self.sudo().search(domain, limit=1)

    def _mcd_customer_payload(self, phone_clean=None, is_new=False):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone or self.mobile or phone_clean or '',
            'birthdate': self.mcd_birthdate.isoformat() if self.mcd_birthdate else '',
            'is_new': is_new,
            'order_count': self.mcd_order_count,
            'total_spent': round(self.mcd_total_spent),
            'loyalty_points': self.mcd_loyalty_points,
            'first_order': self.mcd_first_order.strftime('%d/%m/%Y') if self.mcd_first_order else None,
            'last_order': self.mcd_last_order.strftime('%d/%m/%Y') if self.mcd_last_order else None,
        }

    def mcd_update_stats(self, amount):
        """Update customer metrics after a paid POS order."""
        self.ensure_one()
        now = fields.Datetime.now()
        points = int(max(amount or 0, 0) // 1000)
        vals = {
            'mcd_is_customer': True,
            'mcd_last_order': now,
            'mcd_order_count': self.mcd_order_count + 1,
            'mcd_total_spent': self.mcd_total_spent + amount,
            'mcd_loyalty_points': self.mcd_loyalty_points + points,
        }
        if not self.mcd_first_order:
            vals['mcd_first_order'] = now
        self.write(vals)

    @api.model
    def mcd_lookup_by_phone(self, phone):
        """Lookup by phone only. This does not create a new customer."""
        phone_clean = self.mcd_normalize_phone(phone)
        if not phone_clean:
            return None
        partner = self._mcd_find_partner_by_phone(phone_clean)
        if not partner:
            return None
        return partner._mcd_customer_payload(
            phone_clean=phone_clean,
            is_new=partner.mcd_order_count == 0,
        )

    @api.model
    def mcd_find_or_create(self, phone, name=None, birthdate=None):
        """
        Find a customer by phone, or create one when the cashier enters the
        extra customer fields on the payment screen.
        """
        phone_clean = self.mcd_normalize_phone(phone)
        if not phone_clean:
            return None

        partner = self._mcd_find_partner_by_phone(phone_clean)
        if not partner:
            partner = self.sudo().create({
                'name': name or f'Khách {phone_clean}',
                'phone': phone_clean,
                'mobile': phone_clean,
                'customer_rank': 1,
                'mcd_is_customer': True,
                'mcd_birthdate': birthdate or False,
            })
            is_new = True
        else:
            is_new = partner.mcd_order_count == 0
            vals = {
                'mcd_is_customer': True,
                'customer_rank': max(partner.customer_rank, 1),
            }
            if name and (not partner.name or partner.name.startswith('Khách ')):
                vals['name'] = name
            if birthdate and not partner.mcd_birthdate:
                vals['mcd_birthdate'] = birthdate
            partner.sudo().write(vals)

        return partner._mcd_customer_payload(phone_clean=phone_clean, is_new=is_new)
