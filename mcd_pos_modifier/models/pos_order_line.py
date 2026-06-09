import json

from odoo import fields, models


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    modifier_json = fields.Text(string="Modifier JSON")
    modifier_note = fields.Text(string="Modifier Note")
    modifier_price_extra = fields.Float(string="Modifier Price Extra", default=0.0)

    def _export_for_kitchen_display(self):
        """Hook to enrich kitchen payload when kitchen module calls this method."""
        data = super()._export_for_kitchen_display() if hasattr(super(), "_export_for_kitchen_display") else {}
        data["modifier_note"] = self.modifier_note or ""
        return data


class PosOrder(models.Model):
    _inherit = "pos.order"

    mcd_service_type = fields.Selection(
        [
            ("eat_in", "Eat In"),
            ("take_out", "Take Out"),
        ],
        string="Service Type",
        default="eat_in",
    )

    def _order_fields(self, ui_order):
        vals = super()._order_fields(ui_order)
        vals["mcd_service_type"] = ui_order.get("mcd_service_type") or "eat_in"
        return vals

    def _order_line_fields(self, line, session_id=None):
        vals = super()._order_line_fields(line, session_id=session_id)
        
        # vals is [cmd, id, {values_dict}]
        if not vals or not isinstance(vals, (list, tuple)) or len(vals) <= 2:
            return vals
        
        line_data = vals[2]
        if not isinstance(line_data, dict):
            return vals
        
        try:
            modifier_price_extra = float(line_data.get("modifier_price_extra", 0.0) or 0.0)
        except (ValueError, TypeError):
            modifier_price_extra = 0.0
        
        modifier_json = line_data.get("modifier_json") or ""
        modifier_note = line_data.get("modifier_note") or ""

        vals[2].update({
            "modifier_json": modifier_json,
            "modifier_note": modifier_note,
            "modifier_price_extra": modifier_price_extra,
        })

        # Validate JSON if present
        if modifier_json:
            try:
                json.loads(modifier_json)
            except Exception as e:
                # Log the invalid JSON but don't fail the order creation
                # Invalid JSON will be stored as-is for debugging
                pass

        return vals


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    product_tmpl_id = fields.Many2one(
        domain=[
            "|",
            ("type", "in", ["product", "consu"]),
            ("available_in_pos", "=", True),
        ],
    )
    product_id = fields.Many2one(
        domain="[('product_tmpl_id', '=', product_tmpl_id), '|', ('type', 'in', ['product', 'consu']), ('available_in_pos', '=', True)]",
    )


class MrpBomLine(models.Model):
    _inherit = "mrp.bom.line"

    mcd_modifier_price = fields.Float(
        string="Giá tùy chỉnh (POS)",
        default=0.0,
        help="Giá tính thêm khi khách chọn Thêm/Chỉ nguyên liệu này trong POS"
    )
