from odoo import models, fields


class PosOrder(models.Model):
    _inherit = 'pos.order'

    service_type = fields.Selection([
        ('eat_in', 'Eat In'),
        ('take_out', 'Take Out'),
    ], string='Service Type', default='eat_in')

    def _get_service_type_from_table(self):
        return getattr(self, 'mcd_service_type', False) or self.service_type or 'eat_in'

    def _send_to_kitchen(self):
        send_to_kitchen = getattr(super(), '_send_to_kitchen', None)
        return send_to_kitchen() if send_to_kitchen else False
