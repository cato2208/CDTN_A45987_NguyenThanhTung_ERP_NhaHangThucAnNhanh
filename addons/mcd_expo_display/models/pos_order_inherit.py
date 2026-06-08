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
        lines = self._mcd_prepare_display_lines()

        if not lines:
            return False

        return self.env['expo.order'].create({
            'name': display_code,
            'pos_order_id': self.id,
            'service_type': service_type,
            'state': 'waiting',
            'line_ids': lines,
        })

    def _mcd_prepare_display_lines(self):
        grouped = {}
        for line in self.lines:
            if not line.product_id or line.qty <= 0:
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
