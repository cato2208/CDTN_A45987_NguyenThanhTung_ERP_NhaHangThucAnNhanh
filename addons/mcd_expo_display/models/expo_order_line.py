from odoo import models, fields, api


class ExpoOrderLine(models.Model):
    _name = 'expo.order.line'
    _description = 'Expo Order Line'
    _order = 'sequence, id'

    order_id = fields.Many2one(
        'expo.order',
        string="Order",
        ondelete='cascade',
        required=True,
    )
    sequence = fields.Integer(default=10)

    product_id = fields.Many2one(
        'product.product',
        string="San pham",
        required=True,
        domain=[('sale_ok', '=', True)],
    )

    product_name = fields.Char(
        string="Ten hien thi",
        compute='_compute_product_name',
        store=True,
    )

    qty = fields.Integer(string="Số lượng", default=1)

    # FIX: thêm modifier_note
    modifier_note = fields.Text(string="Ghi chú tùy chỉnh", default="")

    @api.depends('product_id')
    def _compute_product_name(self):
        for line in self:
            line.product_name = line.product_id.display_name if line.product_id else ''
