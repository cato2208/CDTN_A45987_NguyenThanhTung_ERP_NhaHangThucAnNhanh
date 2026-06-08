import logging
from datetime import datetime, time, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosOrderModifierTrigger(models.Model):
    _inherit = 'pos.order'

    mcd_display_code = fields.Char(
        string='Display Order Code',
        copy=False,
        index=True,
        readonly=True,
    )

    def action_pos_order_paid(self):
        """Send paid orders to Kitchen and Expo once, using a compact display code."""
        res = super().action_pos_order_paid()
        self._mcd_send_display_orders()
        return res

    @api.model
    def create_from_ui(self, orders, draft=False):
        if not orders:
            _logger.error("[MCD POS] Empty create_from_ui payload received: %r, draft=%s", orders, draft)
            raise UserError("POS không gửi dữ liệu đơn hàng lên máy chủ. Vui lòng tải lại POS và thanh toán lại.")
        
        try:
            _logger.info("[Kiosk] Creating %d POS orders from UI, draft=%s", len(orders), draft)
            res = super().create_from_ui(orders, draft=draft)
            _logger.info("[Kiosk] Successfully created %d POS orders", len(res) if res else 0)
            
            if not draft and res:
                try:
                    order_ids = [order.get('id') for order in res if order.get('id')]
                    if order_ids:
                        _logger.info("[Kiosk] Sending %d orders to display systems", len(order_ids))
                        self.browse(order_ids)._mcd_send_display_orders()
                except Exception as e:
                    _logger.error("[Kiosk] Error sending orders to display: %s", str(e), exc_info=True)
            
            return res
        except Exception as e:
            _logger.error("[Kiosk] CRITICAL: POS order creation failed: %s", str(e), exc_info=True)
            raise

    def write(self, vals):
        res = super().write(vals)
        if vals.get('state') in ('paid', 'done', 'invoiced'):
            self._mcd_send_display_orders()
        return res

    def _mcd_send_display_orders(self):
        for order in self.filtered(lambda pos_order: pos_order.state in ('paid', 'done', 'invoiced')):
            order._mcd_ensure_display_code()
            if hasattr(order, '_send_to_kitchen'):
                order._send_to_kitchen()
            if hasattr(order, '_send_to_expo'):
                order._send_to_expo()

    def _mcd_sync_recent_display_orders(self, limit=80):
        orders = self.search(
            [('state', 'in', ('paid', 'done', 'invoiced'))],
            order='id desc',
            limit=limit,
        )
        orders._mcd_send_display_orders()
        return True

    def _mcd_ensure_display_code(self):
        for order in self:
            if order.mcd_display_code:
                continue

            service_type = getattr(order, 'mcd_service_type', False) or 'eat_in'
            prefix = 'TO' if service_type == 'take_out' else 'EI'
            order_datetime = order.date_order or fields.Datetime.now()
            order_day = order_datetime.date()
            day_start = datetime.combine(order_day, time.min)
            day_end = day_start + timedelta(days=1)

            last_order = self.search([
                ('id', '!=', order.id),
                ('mcd_display_code', '=like', prefix + '%'),
                ('date_order', '>=', fields.Datetime.to_string(day_start)),
                ('date_order', '<', fields.Datetime.to_string(day_end)),
            ], order='id desc', limit=1)

            next_number = 1
            if last_order and last_order.mcd_display_code:
                digits = last_order.mcd_display_code.replace(prefix, '', 1)
                if digits.isdigit():
                    next_number = int(digits) + 1

            order.mcd_display_code = '%s%03d' % (prefix, next_number)
