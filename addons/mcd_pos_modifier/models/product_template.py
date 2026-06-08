from odoo import api, models


class PosSession(models.Model):
    _inherit = "pos.session"

    @api.model
    def _loader_params_product_product(self):
        params = super()._loader_params_product_product()
        params["search_params"]["fields"] += ["product_tmpl_id", "display_name", "lst_price"]
        return params

    def _pos_data_process(self, loaded_data):
        super()._pos_data_process(loaded_data)
        self._remove_mcd_hidden_pos_products(loaded_data)
        loaded_data["mcd_modifiers_by_tmpl"] = self._build_mcd_modifiers(loaded_data)
        self._apply_mcd_product_availability(loaded_data)
        self._apply_mcd_customization_flags(loaded_data)

    def load_pos_data(self):
        data = super().load_pos_data()
        self._remove_mcd_hidden_pos_products(data)
        data["mcd_modifiers_by_tmpl"] = self._build_mcd_modifiers(data)
        self._apply_mcd_product_availability(data)
        self._apply_mcd_customization_flags(data)
        return data

    def _remove_mcd_hidden_pos_products(self, loaded_data):
        products = loaded_data.get("product.product", [])
        if not products:
            return

        filtered = []
        for row in products:
            name = (row.get("display_name") or row.get("name") or "").lower()
            default_code = (row.get("default_code") or "").upper()
            if "gift card" in name:
                continue
            if default_code.startswith("MCD-DEMO-"):
                continue
            filtered.append(row)
        loaded_data["product.product"] = filtered

    def _build_mcd_modifiers(self, loaded_data):
        products = loaded_data.get("product.product", [])
        tmpl_ids = set()
        for p in products:
            val = p.get("product_tmpl_id")
            if isinstance(val, (list, tuple)):
                tmpl_ids.add(val[0])
            elif isinstance(val, int):
                tmpl_ids.add(val)

        if not tmpl_ids:
            return {}

        boms = self.env["mrp.bom"].search([
            ("product_tmpl_id", "in", list(tmpl_ids)),
            ("type", "in", ["normal", "phantom"]),
        ])

        category_action_map = {
            "Protein": ["only", "extra"],
            "Vegetable": ["remove", "extra"],
            "Sauce": ["remove", "extra"],
            "Cheese": ["remove", "extra"],
            "Fixed": [],
            "Buns": [],
        }

        modifiers_by_tmpl = {}
        for bom in boms:
            ingredients = []
            for line in bom.bom_line_ids:
                categ_name = line.product_id.categ_id.complete_name or ""
                leaf = categ_name.split("/")[-1].strip()
                allowed = category_action_map.get(leaf, ["remove", "extra"])
                modifier_price = line.mcd_modifier_price or line.product_id.lst_price
                if not modifier_price:
                    modifier_price = (line.product_id.standard_price or 0.0) * (line.product_qty or 1.0)
                ingredients.append({
                    "bom_line_id": line.id,
                    "product_id": line.product_id.id,
                    "name": line.product_id.display_name,
                    "qty": line.product_qty,
                    "uom": line.product_uom_id.name,
                    "category": leaf,
                    "allowed_actions": allowed,
                    "price_unit": modifier_price or 0.0,
                })
            modifiers_by_tmpl[bom.product_tmpl_id.id] = ingredients

        return modifiers_by_tmpl

    def _apply_mcd_product_availability(self, loaded_data):
        """Mark POS menu products unavailable when their BoM lacks stock."""
        product_rows = loaded_data.get("product.product", [])
        product_ids = [row.get("id") for row in product_rows if row.get("id")]
        if not product_ids:
            return

        products = self.env["product.product"].browse(product_ids).exists()
        if not products:
            return

        company_domain = ["|", ("company_id", "=", False), ("company_id", "=", self.env.company.id)]
        Bom = self.env["mrp.bom"]
        product_boms = Bom.search([
            ("product_id", "in", products.ids),
            ("type", "in", ["normal", "phantom"]),
            *company_domain,
        ])
        template_boms = Bom.search([
            ("product_id", "=", False),
            ("product_tmpl_id", "in", products.mapped("product_tmpl_id").ids),
            ("type", "in", ["normal", "phantom"]),
            *company_domain,
        ])
        bom_by_product = {bom.product_id.id: bom for bom in product_boms if bom.product_id}
        bom_by_template = {
            bom.product_tmpl_id.id: bom
            for bom in template_boms
            if bom.product_tmpl_id.id not in bom_by_product
        }

        status_by_product = {}
        for product in products:
            bom = bom_by_product.get(product.id) or bom_by_template.get(product.product_tmpl_id.id)
            missing = self._mcd_missing_bom_materials(bom)
            if missing:
                status_by_product[product.id] = {
                    "mcd_pos_unavailable": True,
                    "mcd_pos_unavailable_reason": "Tạm dừng phục vụ: thiếu " + ", ".join(missing[:3]),
                }

        for row in product_rows:
            status = status_by_product.get(row.get("id"))
            row["mcd_pos_unavailable"] = bool(status)
            row["mcd_pos_unavailable_reason"] = (
                status["mcd_pos_unavailable_reason"]
                if status
                else ""
            )

    def _mcd_missing_bom_materials(self, bom):
        if not bom:
            return []

        missing = []
        finished_qty = bom.product_qty or 1.0
        for line in bom.bom_line_ids:
            material = line.product_id
            if not material:
                continue

            detailed_type = getattr(material, "detailed_type", False)
            product_type = getattr(material, "type", False)
            if detailed_type and detailed_type != "product" and product_type != "product":
                continue
            if not detailed_type and product_type != "product":
                continue

            needed_qty = (line.product_qty or 0.0) / finished_qty
            try:
                needed_qty = line.product_uom_id._compute_quantity(needed_qty, material.uom_id)
            except Exception:
                pass

            available_qty = material.qty_available or 0.0
            if available_qty + 0.00001 < needed_qty:
                short_qty = round(needed_qty - available_qty, 2)
                missing.append("%s (%s %s)" % (
                    material.display_name,
                    short_qty,
                    material.uom_id.name,
                ))

        return missing

    def _apply_mcd_customization_flags(self, loaded_data):
        """Only burger menu items can open the ingredient modifier UI."""
        product_rows = loaded_data.get("product.product", [])
        product_ids = [row.get("id") for row in product_rows if row.get("id")]
        if not product_ids:
            return

        products = {
            product.id: product
            for product in self.env["product.product"].browse(product_ids).exists()
        }

        for row in product_rows:
            product = products.get(row.get("id"))
            row["mcd_can_customize"] = bool(
                product and self._mcd_product_can_customize(product)
            )

    def _mcd_product_can_customize(self, product):
        category_names = []
        records = [product, product.product_tmpl_id]

        for record in records:
            for field_name in ("pos_categ_ids", "pos_category_ids"):
                if field_name in record._fields:
                    category_names.extend(record[field_name].mapped("name"))

        if product.categ_id:
            category_names.append(product.categ_id.complete_name or product.categ_id.name or "")

        if not category_names:
            category_names.append(product.display_name or "")

        return any("burger" in (name or "").lower() for name in category_names)
