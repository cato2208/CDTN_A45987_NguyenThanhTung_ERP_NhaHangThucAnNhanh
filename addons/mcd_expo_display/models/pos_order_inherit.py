from odoo import models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _send_to_expo(self):
        """Create an Expo Display order once the POS order is paid."""
        existing = self.env['expo.order'].search([
            ('pos_order_id', '=', self.id),
        ], limit=1)
        if existing:
            return existing

        legacy_refs = [ref for ref in [self.pos_reference, self.name] if ref]
        if legacy_refs:
            existing = self.env['expo.order'].search([
                ('pos_order_id', '=', False),
                ('name', 'in', legacy_refs),
            ], limit=1)
            if existing:
                existing.pos_order_id = self.id
                return existing

        service_type = getattr(self, 'mcd_service_type', False) or 'eat_in'
        if hasattr(self, '_mcd_ensure_display_code'):
            self._mcd_ensure_display_code()
        display_code = getattr(self, 'mcd_display_code', False) or self.pos_reference or self.name
        lines = self._mcd_prepare_expo_display_lines()

        if not lines:
            return False

        return self.env['expo.order'].create({
            'name': display_code,
            'pos_order_id': self.id,
            'service_type': service_type,
            'state': 'waiting',
            'line_ids': lines,
        })

    def _mcd_prepare_expo_display_lines(self):
        grouped = {}
        for line in self.lines:
            if not line.product_id or line.qty <= 0:
                continue
            if self._mcd_skip_expo_product(line.product_id):
                continue
            modifier_note = (getattr(line, 'modifier_note', '') or '').strip()
            key = (line.product_id.id, modifier_note)
            if key not in grouped:
                grouped[key] = {
                    'product_id': line.product_id.id,
                    'qty': 0,
                    'modifier_note': modifier_note,
                }
            grouped[key]['qty'] += int(line.qty)
        return [
            (0, 0, vals)
            for vals in grouped.values()
            if vals['qty'] > 0
        ]

    def _mcd_skip_expo_product(self, product):
        name = (product.display_name or product.name or '').lower()
        default_code = (product.default_code or '').upper()
        category = (product.categ_id.complete_name or product.categ_id.name or '').lower()
        return (
            'gift card' in name
            or 'thẻ quà' in name
            or 'the qua' in name
            or 'gift card' in category
            or default_code.startswith('MCD-DEMO-')
        )
