from odoo import models, fields, api


class ExpoOrder(models.Model):
    _name = 'expo.order'
    _description = 'Expo Order'
    _order = 'order_time asc'

    name = fields.Char(
        string="Order #",
        required=True,
        copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code('expo.order') or '/',
    )
    pos_order_id = fields.Many2one('pos.order', string="POS Order", index=True, readonly=True)
    serve_time = fields.Datetime(string="Serve Time")
    service_type = fields.Selection([
        ('eat_in', 'Eat In'),
        ('take_out', 'Take Out'),
    ], string="Service Type", default='eat_in', required=True)

    order_time = fields.Datetime(
        string="Order Time",
        default=fields.Datetime.now,
        required=True,
    )

    state = fields.Selection([
        ('waiting', 'Waiting'),
        ('done', 'Done'),
    ], string="Status", default='waiting', index=True)

    line_ids = fields.One2many(
        'expo.order.line', 'order_id',
        string="Items",
    )

    duration = fields.Float(string="Duration", compute="_compute_duration", store=True)

    @api.depends('create_date', 'serve_time')
    def _compute_duration(self):
        for order in self:
            if order.create_date and order.serve_time:
                delta = order.serve_time - order.create_date
                order.duration = delta.total_seconds() / 60
            else:
                order.duration = 0

    def action_done(self):
        for order in self:
            order.state = 'done'
            order.serve_time = fields.Datetime.now()

    @api.model
    def serve_oldest(self):
        oldest = self.search(
            [('state', '=', 'waiting')],
            order='order_time asc',
            limit=1,
        )
        if oldest:
            oldest.state = 'done'
            oldest.serve_time = fields.Datetime.now()
            return oldest.id
        return False

    @api.model
    def serve_order(self, order_id):
        if not order_id:
            return False
        order = self.browse(int(order_id)).exists()
        if order and order.state == 'waiting':
            order.state = 'done'
            order.serve_time = fields.Datetime.now()
            return order.id
        return False

    @api.model
    def get_waiting_orders(self):
        orders = self.search(
            [('state', '=', 'waiting')],
            order='order_time asc',
        )
        result = []
        for order in orders:
            result.append({
                'id': order.id,
                'name': order.name,
                'service_type': order.service_type,
                'order_time': order.order_time.isoformat() + 'Z' if order.order_time else None,
                'lines': [
                    {
                        'product_name': line.product_name,
                        'qty': line.qty,
                        'modifier_note': line.modifier_note or '',  # FIX
                    }
                    for line in order.line_ids
                ],
            })
        return result
