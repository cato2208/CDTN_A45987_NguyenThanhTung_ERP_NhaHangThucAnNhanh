from html import escape

from odoo import http
from odoo.http import request


def _render_grill_slips(tickets):
    ticket_html = []
    for ticket in tickets:
        ticket_html.append(f"""
            <div class="ticket">
                <div class="row">
                    <span class="label">Mã đơn</span>
                    <span class="value">{escape(ticket['order_code'])}</span>
                </div>
                <div class="row">
                    <span class="label">Mon an</span>
                    <span class="value">{escape(ticket['product_name'])}</span>
                </div>
                <div class="row">
                    <span class="label">+ Modifier</span>
                    <span class="modifier">{escape(ticket['modifier_note'])}</span>
                </div>
            </div>
        """)

    body = ''.join(ticket_html) or """
        <div class="ticket">
            <div class="row">
                <span class="value">Don nay khong co modifier.</span>
            </div>
        </div>
    """

    html = f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8"/>
    <title>Grill Slip</title>
    <style>
        @page {{ size: 80mm auto; margin: 4mm; }}
        body {{
            color: #111;
            font-family: Arial, sans-serif;
            font-size: 18px;
            font-weight: 800;
            margin: 0;
        }}
        .ticket {{
            page-break-after: always;
            width: 72mm;
        }}
        .ticket:last-child {{
            page-break-after: auto;
        }}
        .row {{
            border-bottom: 1px dashed #999;
            padding: 10px 0;
        }}
        .label {{
            display: block;
            font-size: 12px;
            letter-spacing: 0.8px;
            margin-bottom: 4px;
            text-transform: uppercase;
        }}
        .value {{
            display: block;
            font-size: 24px;
            line-height: 1.25;
        }}
        .modifier {{
            border: 2px solid #111;
            display: block;
            font-size: 24px;
            line-height: 1.25;
            margin-top: 6px;
            padding: 12px;
            white-space: pre-wrap;
        }}
    </style>
    <script>
        window.addEventListener('load', function () {{
            setTimeout(function () {{ window.print(); }}, 250);
        }});
    </script>
</head>
<body>{body}</body>
</html>"""
    return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])


class KitchenDisplayController(http.Controller):
    @http.route(
        '/mcd_kds/get_orders',
        type='json',
        auth='user',
        methods=['POST'],
    )
    def get_orders(self):
        """Return all waiting kitchen orders (oldest first)."""
        request.env['pos.order'].sudo()._mcd_sync_recent_display_orders()
        return request.env['kitchen.order'].get_waiting_orders()

    @http.route(
        '/mcd_kds/serve',
        type='json',
        auth='user',
        methods=['POST'],
    )
    def serve_oldest(self, order_id=False):
        """Serve a specific waiting order."""
        served_id = request.env['kitchen.order'].serve_order(order_id)
        return {'served_id': served_id}

    @http.route(
        '/mcd_kds/print_modifiers/<int:order_id>',
        type='http',
        auth='user',
        methods=['GET'],
    )
    def print_modifier_tickets(self, order_id):
        """Browser printable modifier tickets, one paper per modified item."""
        order = request.env['kitchen.order'].sudo().browse(order_id).exists()
        if not order:
            return request.not_found()

        tickets = []
        for line in order.line_ids:
            note = (line.modifier_note or '').strip()
            if not note:
                continue
            qty = max(1, int(round(line.qty or 1)))
            for index in range(qty):
                tickets.append({
                    'order_code': order.name or str(order.id),
                    'product_name': line.product_name,
                    'modifier_note': note,
                    'copy_no': index + 1,
                    'copy_total': qty,
                })

        return _render_grill_slips(tickets)
