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


class PosModifierPrintController(http.Controller):
    @http.route(
        '/mcd_pos_modifier/print_grill_slips/<int:order_id>',
        type='http',
        auth='user',
        methods=['GET'],
    )
    def print_grill_slips(self, order_id):
        order = request.env['pos.order'].sudo().browse(order_id).exists()
        if not order:
            return request.not_found()

        tickets = []
        for line in order.lines:
            note = (line.modifier_note or '').strip()
            if not note:
                continue
            qty = max(1, int(round(line.qty or 1)))
            for _index in range(qty):
                tickets.append({
                    'order_code': order.mcd_display_code or order.name or order.pos_reference or str(order.id),
                    'product_name': line.product_id.display_name or line.full_product_name or line.name,
                    'modifier_note': note,
                })

        return _render_grill_slips(tickets)
