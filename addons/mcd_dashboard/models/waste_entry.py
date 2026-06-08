from odoo import models, fields, api


class McdWasteEntry(models.Model):
    _name = 'mcd.waste.entry'
    _description = 'Waste Entry'
    _order = 'date desc'

    product_id = fields.Many2one(
        'product.product',
        string='Nguyên liệu (từ kho)',
        domain=[('type', 'in', ['product', 'consu'])],
    )
    scrap_id = fields.Many2one(
        'stock.scrap',
        string='Phiếu trừ kho',
        readonly=True,
        ondelete='set null',
    )
    product_name = fields.Char(
        string='Tên sản phẩm',
        compute='_compute_product_name',
        store=True,
    )
    qty       = fields.Float(string='Số lượng hủy', required=True, default=1)
    unit_cost = fields.Float(string='Đơn giá ước tính (VND)', default=0)
    reason = fields.Selection([
        ('expired',       'Hết hạn'),
        ('wrong_order',   'Làm sai đơn'),
        ('dropped',       'Rơi vỡ'),
        ('cancelled',     'Khách hủy'),
        ('cooking_error', 'Hỏng chế biến'),
        ('other',         'Khác'),
    ], string='Lý do hủy', required=True, default='other')
    date          = fields.Datetime(string='Ngày', required=True, default=fields.Datetime.now)
    employee_name = fields.Char(string='Nhân viên')
    note          = fields.Text(string='Ghi chú')
    total_loss    = fields.Float(
        string='Tổng thất thoát (VND)',
        compute='_compute_total_loss',
        store=True,
    )

    @api.depends('product_id')
    def _compute_product_name(self):
        for rec in self:
            if rec.product_id:
                rec.product_name = rec.product_id.display_name
            # keep existing product_name if no product_id

    @api.depends('qty', 'unit_cost')
    def _compute_total_loss(self):
        for rec in self:
            rec.total_loss = rec.qty * rec.unit_cost
