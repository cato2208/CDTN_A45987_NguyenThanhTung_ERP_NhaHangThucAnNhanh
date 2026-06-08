from odoo import fields, http
from odoo.http import request


class ExpoDisplayController(http.Controller):
    def _format_dt(self, value):
        if not value:
            return ''
        local_dt = fields.Datetime.context_timestamp(request.env.user, value)
        return local_dt.strftime('%d/%m/%Y %H:%M')

    @http.route(
        '/mcd_expo/get_orders',
        type='json',
        auth='user',
        methods=['POST'],
    )
    def get_orders(self):
        """Return all waiting expo orders (oldest first)."""
        request.env['pos.order'].sudo()._mcd_sync_recent_display_orders()
        return request.env['expo.order'].get_waiting_orders()

    @http.route(
        '/mcd_expo/serve',
        type='json',
        auth='user',
        methods=['POST'],
    )
    def serve_oldest(self, order_id=False):
        """Serve a specific waiting order."""
        served_id = request.env['expo.order'].serve_order(order_id)
        return {'served_id': served_id}

    @http.route(
        '/mcd_expo/print_pick_list/<int:order_id>',
        type='http',
        auth='user',
        methods=['GET'],
    )
    def print_pick_list(self, order_id):
        """Browser printable pick list for an expo order."""
        order = request.env['expo.order'].sudo().browse(order_id).exists()
        if not order:
            return request.not_found()
        return request.render('mcd_expo_display.print_pick_list_template', {
            'order': order,
            'printed_at': self._format_dt(fields.Datetime.now()),
            'order_time': self._format_dt(order.order_time),
            'serve_time': self._format_dt(order.serve_time),
        })
