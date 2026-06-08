from odoo import fields, models


class PosOrderCustomer(models.Model):
    _inherit = 'pos.order'

    mcd_customer_points_awarded = fields.Boolean(
        string='Đã cộng điểm MCD',
        default=False,
        copy=False,
        readonly=True,
    )

    def action_pos_order_paid(self):
        res = super().action_pos_order_paid()
        for order in self:
            if order.partner_id and (order.partner_id.phone or order.partner_id.mobile) and not order.mcd_customer_points_awarded:
                partner = order.partner_id.sudo()
                partner.write({'mcd_is_customer': True})
                partner.mcd_update_stats(order.amount_total)
                order.sudo().write({'mcd_customer_points_awarded': True})
        return res
