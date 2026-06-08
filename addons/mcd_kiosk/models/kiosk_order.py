import json
import uuid
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class KioskOrder(models.Model):
    _name = 'mcd.kiosk.order'
    _description = 'Kiosk Order'
    _order = 'create_date desc'

    name = fields.Char(
        string='Order #',
        required=True,
        copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code('mcd.kiosk.order') or '/',
    )
    service_type = fields.Selection([
        ('eat_in', 'Eat In'),
        ('take_out', 'Take Out'),
    ], string='Service Type', default='eat_in', required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ], string='State', default='draft')
    payment_method = fields.Selection([
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('qr', 'QR Code'),
    ], string='Payment Method')
    amount_total = fields.Float(string='Total', compute='_compute_total', store=True)
    partner_id = fields.Many2one('res.partner', string='Customer')
    pos_order_id = fields.Many2one('pos.order', string='POS Order', readonly=True, copy=False)
    line_ids = fields.One2many('mcd.kiosk.order.line', 'order_id', string='Lines')
    note = fields.Text(string='Note')

    @api.depends('line_ids.subtotal')
    def _compute_total(self):
        for order in self:
            order.amount_total = sum(order.line_ids.mapped('subtotal'))

    @api.model
    def _mcd_fix_broken_pos_references(self):
        broken_orders = self.env['pos.order'].sudo().search([
            ('state', '=', 'draft'),
            ('pos_reference', '=like', 'KIOSK/%'),
        ])
        for index, order in enumerate(broken_orders, start=1):
            session = order.session_id
            sequence_number = order.sequence_number or index
            order.pos_reference = '%05d-%03d-%04d' % (
                session.config_id.id if session else 0,
                (session.id if session else 0) % 1000,
                sequence_number,
            )
        return True

    def action_paid(self, payment_method='cash'):
        self.ensure_one()
        self.write({'state': 'paid', 'payment_method': payment_method})
        self._create_pos_order(payment_method=payment_method)
        return True

    def _get_kiosk_pos_session(self):
        session = self.env['pos.session'].search([
            ('state', '=', 'opened'),
        ], order='id desc', limit=1)
        if not session:
            raise UserError('Cần mở một phiên POS trước khi kiosk tạo đơn.')
        return session

    def _get_kiosk_payment_method(self, session, payment_method):
        methods = session.payment_method_ids
        if not methods:
            raise UserError('Phiên POS chưa cấu hình phương thức thanh toán.')

        method_key = payment_method or 'cash'
        method = self.env['pos.payment.method']
        
        try:
            if method_key == 'cash':
                method = methods.filtered('is_cash_count')[:1]
            elif method_key == 'qr':
                # Try to find QR-specific payment method
                method = methods.filtered(lambda m: 'qr' in (m.name or '').lower())[:1]
                # Fallback to bank payment methods if no QR method found
                if not method:
                    method = methods.filtered(
                        lambda m: 'bank' in (m.name or '').lower()
                        or 'ngan' in (m.name or '').lower()
                        or 'ngân' in (m.name or '').lower()
                    )[:1]
            elif method_key == 'card':
                # Try to find card payment method
                method = methods.filtered(
                    lambda m: 'card' in (m.name or '').lower()
                    or 'the' in (m.name or '').lower()
                    or 'thẻ' in (m.name or '').lower()
                )[:1]
        except Exception as e:
            _logger.error(f'Error filtering payment method {method_key}: {e}')
        
        # Use first available method as fallback
        result_method = method or methods[:1]
        if not result_method:
            raise UserError(f'Không tìm thấy phương thức thanh toán {method_key} trong session POS.')
        return result_method

    def _prepare_pos_order_line(self, line, session):
        product = line.product_id.with_company(session.company_id)
        price_unit = line.price_unit + line.modifier_price_extra
        taxes = product.taxes_id.filtered(lambda tax: tax.company_id == session.company_id)
        tax_result = taxes.compute_all(
            price_unit,
            session.currency_id,
            line.qty,
            product=product,
            partner=self.partner_id,
        )
        modifier_payload = {
            'source': 'kiosk',
            'note': line.modifier_note or '',
            'price_extra': line.modifier_price_extra,
        }
        modifier_json = line.modifier_json or json.dumps(modifier_payload, ensure_ascii=False)
        return [0, 0, {
            'id': str(uuid.uuid4()),
            'uuid': str(uuid.uuid4()),
            'product_id': product.id,
            'full_product_name': product.display_name,
            'qty': line.qty,
            'price_unit': price_unit,
            'price_subtotal': tax_result['total_excluded'],
            'price_subtotal_incl': tax_result['total_included'],
            'discount': 0,
            'tax_ids': [(6, 0, taxes.ids)],
            'modifier_note': line.modifier_note or '',
            'modifier_json': modifier_json,
            'modifier_price_extra': line.modifier_price_extra,
        }]

    def _create_pos_order(self, payment_method='cash'):
        self.ensure_one()
        if self.pos_order_id:
            return self.pos_order_id

        try:
            session = self._get_kiosk_pos_session()
            payment = self._get_kiosk_payment_method(session, payment_method)
            sequence_number = session.sequence_number or 1
            pos_reference = '%05d-%03d-%04d' % (
                session.config_id.id or 0,
                session.id % 1000,
                sequence_number,
            )
            now = fields.Datetime.now()
            now_str = fields.Datetime.to_string(now)
            pos_lines = [
                self._prepare_pos_order_line(line, session)
                for line in self.line_ids
                if line.product_id and line.qty > 0
            ]
            if not pos_lines:
                raise UserError('Đơn kiosk không có sản phẩm.')

            amount_total = sum(line[2]['price_subtotal_incl'] for line in pos_lines)
            amount_tax = amount_total - sum(line[2]['price_subtotal'] for line in pos_lines)
            payload = {
                'data': {
                    'name': pos_reference,
                    'uid': str(uuid.uuid4()),
                    'amount_paid': amount_total,
                    'amount_total': amount_total,
                    'amount_tax': amount_tax,
                    'amount_return': 0,
                    'pos_session_id': session.id,
                    'pricelist_id': session.config_id.pricelist_id.id,
                    'partner_id': self.partner_id.id if self.partner_id else False,
                    'user_id': self.env.user.id,
                    'sequence_number': sequence_number,
                    'date_order': now_str,
                    'fiscal_position_id': session.config_id.default_fiscal_position_id.id or False,
                    'lines': pos_lines,
                    'statement_ids': [[0, 0, {
                        'name': now_str,
                        'amount': amount_total,
                        'payment_method_id': payment.id,
                    }]],
                    'to_invoice': False,
                    'shipping_date': False,
                    'is_tipped': False,
                    'tip_amount': 0,
                    'access_token': '',
                    'ticket_code': '',
                    'last_order_preparation_change': '{}',
                    'mcd_service_type': self.service_type,
                },
            }
            
            _logger.info(f'[Kiosk] Creating POS order with {len(pos_lines)} lines, payment_method={payment_method}')
            _logger.info(f'[Kiosk] Payment method: {payment.name} (id={payment.id})')
            
            result = self.env['pos.order'].create_from_ui([payload], draft=False)
            _logger.info(f'[Kiosk] create_from_ui returned: {result}')
            
            if not result:
                _logger.error('[Kiosk] create_from_ui returned empty list!')
                raise UserError('POS order creation failed - server returned empty response.')
            
            pos_order_id = result[0].get('id') if result else None
            if not pos_order_id:
                _logger.error(f'[Kiosk] No order ID in response: {result}')
                pos_order = self.env['pos.order'].search([('pos_reference', '=', pos_reference)], limit=1)
                if pos_order:
                    pos_order_id = pos_order.id
                    _logger.info(f'[Kiosk] Found order by pos_reference: {pos_order_id}')
                else:
                    raise UserError('Không tạo được đơn POS từ kiosk.')
            
            pos_order = self.env['pos.order'].browse(pos_order_id)
            if not pos_order:
                raise UserError('Không thể tải đơn POS vừa tạo.')
            
            _logger.info(f'[Kiosk] POS order created successfully: {pos_order.name} (id={pos_order.id})')
            
            # Send to kitchen display
            try:
                if hasattr(pos_order, '_send_to_kitchen'):
                    kitchen_order = pos_order._send_to_kitchen()
                    if not kitchen_order:
                        _logger.warning('[Kiosk] Kitchen order creation returned False')
                else:
                    _logger.info('[Kiosk] _send_to_kitchen method not available')
            except Exception as e:
                _logger.error(f'[Kiosk] Error sending to kitchen: {e}', exc_info=True)
                
            # Send to expo display
            try:
                if hasattr(pos_order, '_send_to_expo'):
                    expo_order = pos_order._send_to_expo()
                    if not expo_order:
                        _logger.warning('[Kiosk] Expo order creation returned False')
                else:
                    _logger.info('[Kiosk] _send_to_expo method not available')
            except Exception as e:
                _logger.error(f'[Kiosk] Error sending to expo: {e}', exc_info=True)
                
            self.write({'pos_order_id': pos_order.id})
            session.write({'sequence_number': sequence_number + 1})
            return pos_order
        except Exception as e:
            _logger.error(f'[Kiosk] CRITICAL: Failed to create POS order: {e}', exc_info=True)
            raise


class KioskOrderLine(models.Model):
    _name = 'mcd.kiosk.order.line'
    _description = 'Kiosk Order Line'

    order_id = fields.Many2one('mcd.kiosk.order', ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Product', required=True)
    product_name = fields.Char(string='Name', related='product_id.display_name')
    qty = fields.Integer(string='Qty', default=1)
    price_unit = fields.Float(string='Unit Price')
    modifier_note = fields.Text(string='Modifier')
    modifier_json = fields.Text(string='Modifier JSON')
    modifier_price_extra = fields.Float(string='Extra Price', default=0.0)
    subtotal = fields.Float(string='Subtotal', compute='_compute_subtotal', store=True)

    @api.depends('qty', 'price_unit', 'modifier_price_extra')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.qty * (line.price_unit + line.modifier_price_extra)
