import json
from collections import defaultdict

from odoo import fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    mcd_bom_consumed = fields.Boolean(
        string="BOM stock consumed",
        default=False,
        copy=False,
        readonly=True,
    )

    def action_pos_order_paid(self):
        res = super().action_pos_order_paid()
        self._mcd_consume_bom_stock()
        return res

    def _mcd_consume_bom_stock(self):
        Scrap = self.env["stock.scrap"].sudo()
        for order in self.sudo():
            if order.mcd_bom_consumed or order.state not in ("paid", "done", "invoiced"):
                continue

            source_location = order._mcd_stock_source_location()
            if not source_location:
                continue

            consumption = order._mcd_collect_bom_consumption()
            for material, qty in consumption.values():
                if qty <= 0:
                    continue
                scrap = Scrap.create({
                    "name": "MCD POS BOM Consume",
                    "origin": order.pos_reference or order.name,
                    "product_id": material.id,
                    "scrap_qty": qty,
                    "product_uom_id": material.uom_id.id,
                    "location_id": source_location.id,
                    "company_id": order.company_id.id,
                })
                scrap.action_validate()

            order.write({"mcd_bom_consumed": True})

    def _mcd_collect_bom_consumption(self):
        self.ensure_one()
        totals = defaultdict(lambda: {"material": False, "qty": 0.0})
        bom_cache = {}

        for line in self.lines:
            product = line.product_id
            sale_qty = line.qty or 0.0
            if not product or sale_qty <= 0:
                continue

            bom = self._mcd_find_bom(product, bom_cache)
            if not bom:
                continue

            selections = self._mcd_modifier_selections(line)
            self._mcd_add_bom_consumption(
                product,
                sale_qty,
                totals,
                bom_cache,
                selections=selections,
            )

        return {
            key: (value["material"], value["qty"])
            for key, value in totals.items()
            if value["material"] and value["qty"] > 0
        }

    def _mcd_add_bom_consumption(self, product, qty, totals, bom_cache, selections=None, depth=0):
        """Explode a sold product BOM recursively and collect real stockable materials.

        Combos are configured as kit BOMs whose components are sale products such
        as burgers, fries and drinks. Those components also have BOMs, so a
        single-level BOM read would stop too early and would not consume stock.
        """
        if not product or qty <= 0 or depth > 5:
            return

        bom = self._mcd_find_bom(product, bom_cache)
        if not bom:
            if product.type == "product":
                key = (product.id, product.uom_id.id)
                totals[key]["material"] = product
                totals[key]["qty"] += qty
            return

        selections = selections or {}
        only_bom_line_id = next(
            (line_id for line_id, action in selections.items() if action == "only"),
            None,
        )
        finished_factor = qty / (bom.product_qty or 1.0)

        for bom_line in bom.bom_line_ids:
            material = bom_line.product_id
            if not material:
                continue

            action = selections.get(str(bom_line.id))
            if only_bom_line_id:
                multiplier = 1 if str(bom_line.id) == only_bom_line_id else 0
            elif action == "remove":
                multiplier = 0
            elif action == "extra":
                multiplier = 2
            else:
                multiplier = 1

            raw_qty = finished_factor * (bom_line.product_qty or 0.0) * multiplier
            if raw_qty <= 0:
                continue

            try:
                consumed_qty = bom_line.product_uom_id._compute_quantity(raw_qty, material.uom_id)
            except Exception:
                consumed_qty = raw_qty

            child_bom = self._mcd_find_bom(material, bom_cache)
            if child_bom:
                self._mcd_add_bom_consumption(
                    material,
                    consumed_qty,
                    totals,
                    bom_cache,
                    selections={},
                    depth=depth + 1,
                )
                continue

            if material.type != "product":
                continue

            key = (material.id, material.uom_id.id)
            totals[key]["material"] = material
            totals[key]["qty"] += consumed_qty

    def _mcd_find_bom(self, product, cache=None):
        if not product or "mrp.bom" not in self.env.registry:
            return False
        if cache is not None and product.id in cache:
            return cache[product.id]

        company_domain = ["|", ("company_id", "=", False), ("company_id", "=", self.env.company.id)]
        Bom = self.env["mrp.bom"].sudo()
        bom = Bom.search([
            ("product_id", "=", product.id),
            ("type", "in", ["normal", "phantom"]),
            *company_domain,
        ], limit=1)
        if not bom:
            bom = Bom.search([
                ("product_id", "=", False),
                ("product_tmpl_id", "=", product.product_tmpl_id.id),
                ("type", "in", ["normal", "phantom"]),
                *company_domain,
            ], limit=1)

        if cache is not None:
            cache[product.id] = bom
        return bom

    def _mcd_modifier_selections(self, line):
        if "modifier_json" not in line._fields or not line.modifier_json:
            return {}
        try:
            data = json.loads(line.modifier_json) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _mcd_stock_source_location(self):
        warehouse = self.env["stock.warehouse"].sudo().search([
            ("company_id", "=", self.company_id.id),
        ], limit=1)
        if warehouse and warehouse.lot_stock_id:
            return warehouse.lot_stock_id
        return self.env["stock.location"].sudo().search([
            ("usage", "=", "internal"),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.company_id.id),
        ], limit=1)
