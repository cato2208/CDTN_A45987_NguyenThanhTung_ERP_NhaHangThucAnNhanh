import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class KioskController(http.Controller):
    _VIRTUAL_CATEGORIES = {
        'burger': {'id': -10, 'name': 'Burger'},
        'combo': {'id': -20, 'name': 'Combo'},
        'drink': {'id': -30, 'name': 'Drink'},
        'fries': {'id': -40, 'name': 'Fries'},
    }

    def _get_product_pos_categories(self, product):
        categories = request.env['pos.category'].sudo().browse()
        if 'pos_categ_ids' in product._fields:
            categories |= product.pos_categ_ids
        if 'pos_category_ids' in product._fields:
            categories |= product.pos_category_ids
        tmpl = product.product_tmpl_id
        if 'pos_categ_ids' in tmpl._fields:
            categories |= tmpl.pos_categ_ids
        if 'pos_category_ids' in tmpl._fields:
            categories |= tmpl.pos_category_ids
        return categories

    def _infer_kiosk_category(self, product_name):
        name = (product_name or '').lower()
        if 'combo' in name:
            return self._VIRTUAL_CATEGORIES['combo']
        if any(word in name for word in ['coke', 'cocacola', 'coca', 'fanta', 'sprite', 'zero', 'drink']):
            return self._VIRTUAL_CATEGORIES['drink']
        if 'fries' in name or 'khoai' in name:
            return self._VIRTUAL_CATEGORIES['fries']
        if any(word in name for word in ['burger', 'bigmac', 'bicmac', 'cheese']):
            return self._VIRTUAL_CATEGORIES['burger']
        return None

    def _kiosk_category_sort_key(self, category):
        name = (category.get('name') or '').lower()
        priority = [
            ('burger', 10),
            ('combo', 20),
            ('drink', 30),
            ('drinks', 30),
            ('fries', 40),
        ]
        for key, order in priority:
            if key in name:
                return (order, name)
        return (100, name)

    def _get_unavailable_by_product(self, products):
        if not products:
            return {}

        company_domain = ['|', ('company_id', '=', False), ('company_id', '=', request.env.company.id)]
        Bom = request.env['mrp.bom'].sudo()
        product_boms = Bom.search([
            ('product_id', 'in', products.ids),
            ('type', 'in', ['normal', 'phantom']),
            *company_domain,
        ])
        template_boms = Bom.search([
            ('product_id', '=', False),
            ('product_tmpl_id', 'in', products.mapped('product_tmpl_id').ids),
            ('type', 'in', ['normal', 'phantom']),
            *company_domain,
        ])
        bom_by_product = {bom.product_id.id: bom for bom in product_boms if bom.product_id}
        bom_by_template = {
            bom.product_tmpl_id.id: bom
            for bom in template_boms
        }

        unavailable = {}
        for product in products:
            bom = bom_by_product.get(product.id) or bom_by_template.get(product.product_tmpl_id.id)
            missing = self._missing_bom_materials(bom)
            if missing:
                unavailable[product.id] = {
                    'reason': 'Táº¡m dá»«ng phá»¥c vá»¥: thiáº¿u ' + ', '.join(missing[:3]),
                }
        return unavailable

    def _missing_bom_materials(self, bom):
        if not bom:
            return []

        missing = []
        finished_qty = bom.product_qty or 1.0
        for line in bom.bom_line_ids:
            material = line.product_id
            if not material:
                continue

            detailed_type = getattr(material, 'detailed_type', False)
            product_type = getattr(material, 'type', False)
            if detailed_type and detailed_type != 'product' and product_type != 'product':
                continue
            if not detailed_type and product_type != 'product':
                continue

            needed_qty = (line.product_qty or 0.0) / finished_qty
            try:
                needed_qty = line.product_uom_id._compute_quantity(needed_qty, material.uom_id)
            except Exception:
                pass

            available_qty = material.qty_available or 0.0
            if available_qty + 0.00001 < needed_qty:
                short_qty = round(needed_qty - available_qty, 2)
                missing.append('%s (%s %s)' % (
                    material.display_name,
                    short_qty,
                    material.uom_id.name,
                ))

        return missing

    # â”€â”€â”€ TRANG KIOSK â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @http.route('/kiosk', type='http', auth='user', website=False)
    def kiosk_index(self, **kw):
        return request.render('mcd_kiosk.kiosk_page', {})

    # â”€â”€â”€ LOAD MENU â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @http.route('/mcd_kiosk/menu', type='json', auth='user', methods=['POST'], csrf=False)
    def get_menu(self):
        try:
            env = request.env

            # Products
            products = env['product.product'].sudo().search([
                ('available_in_pos', '=', True),
                ('active', '=', True),
                ('sale_ok', '=', True),
            ], order='name asc')

            product_list = []
            tmpl_ids = set()
            categ_map = {}
            unavailable_by_product = self._get_unavailable_by_product(products)

            for p in products:
                tmpl_ids.add(p.product_tmpl_id.id)
                categ_ids_p = []
                unavailable = unavailable_by_product.get(p.id)
                pos_categories = self._get_product_pos_categories(p)
                if pos_categories:
                    for c in pos_categories:
                        categ_ids_p.append(c.id)
                        if c.id not in categ_map:
                            categ_map[c.id] = {
                                'id': c.id,
                                'name': c.name,
                                'image': f'/web/image/pos.category/{c.id}/image_128' if c.image_128 else False,
                                'sequence': c.sequence,
                            }
                else:
                    fallback_category = self._infer_kiosk_category(p.display_name)
                    if fallback_category:
                        categ_ids_p.append(fallback_category['id'])
                        categ_map[fallback_category['id']] = {
                            **fallback_category,
                            'image': False,
                            'sequence': 999,
                        }
                product_list.append({
                    'id':       p.id,
                    'tmpl_id':  p.product_tmpl_id.id,
                    'name':     p.display_name,
                    'price':    p.lst_price,
                    'image':    f'/web/image/product.product/{p.id}/image_128',
                    'categ_ids': categ_ids_p,
                    'unavailable': bool(unavailable),
                    'unavailable_reason': unavailable['reason'] if unavailable else '',
                    'mcd_pos_unavailable': bool(unavailable),
                    'mcd_pos_unavailable_reason': unavailable['reason'] if unavailable else '',
                })

            categories = sorted(categ_map.values(), key=self._kiosk_category_sort_key)

            # Modifiers (BOM)
            boms = env['mrp.bom'].sudo().search([
                ('product_tmpl_id', 'in', list(tmpl_ids)),
                ('type', 'in', ['normal', 'phantom']),
            ]) if tmpl_ids else []

            category_action_map = {
                'Protein':   ['only', 'extra'],
                'Vegetable': ['remove', 'extra'],
                'Sauce':     ['remove', 'extra'],
                'Cheese':    ['remove', 'extra'],
                'Fixed':     [],
                'Buns':      [],
            }

            modifiers = {}
            for bom in boms:
                ingredients = []
                for line in bom.bom_line_ids:
                    categ_name = line.product_id.categ_id.complete_name or ''
                    leaf = categ_name.split('/')[-1].strip()
                    allowed = category_action_map.get(leaf, ['remove', 'extra'])
                    ingredients.append({
                        'bom_line_id':     line.id,
                        'product_id':      line.product_id.id,
                        'name':            line.product_id.display_name,
                        'qty':             line.product_qty,
                        'uom':             line.product_uom_id.name,
                        'category':        leaf,
                        'allowed_actions': allowed,
                        'price_unit':      line.mcd_modifier_price or 0.0,
                    })
                modifiers[bom.product_tmpl_id.id] = ingredients

            return {
                'categories': categories,
                'products':   product_list,
                'modifiers':  modifiers,
            }

        except Exception as e:
            _logger.error('Kiosk menu error: %s', e, exc_info=True)
            return {'categories': [], 'products': [], 'modifiers': {}, 'error': str(e)}

    # â”€â”€â”€ Äáº¶T HĂ€NG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @http.route('/mcd_kiosk/order', type='json', auth='user', methods=['POST'], csrf=False)
    def place_order(self, service_type='eat_in', lines=None, payment_method='cash', partner_id=None, phone=None):
        if not lines:
            return {'success': False, 'error': 'No items'}
        try:
            env = request.env
            product_ids = [line.get('product_id') for line in lines if line.get('product_id')]
            products = env['product.product'].sudo().browse(product_ids).exists()
            unavailable_by_product = self._get_unavailable_by_product(products)
            if unavailable_by_product:
                first_reason = next(iter(unavailable_by_product.values())).get('reason')
                return {
                    'success': False,
                    'error': first_reason or 'Món đang tạm dừng phục vụ do không đủ nguyên liệu.',
                }

            # Xu ly khach hang
            resolved_partner = None
            if partner_id:
                resolved_partner = env['res.partner'].sudo().browse(partner_id).exists()
            elif phone:
                result = env['res.partner'].sudo().mcd_find_or_create(phone)
                if result:
                    resolved_partner = env['res.partner'].sudo().browse(result['id']).exists()

            order = env['mcd.kiosk.order'].sudo().create({
                'service_type': service_type,
                'partner_id': resolved_partner.id if resolved_partner else False,
                'line_ids': [
                    (0, 0, {
                        'product_id':           line['product_id'],
                        'qty':                  line.get('qty', 1),
                        'price_unit':           line.get('price_unit', 0),
                        'modifier_note':        line.get('modifier_note', ''),
                        'modifier_json':        line.get('modifier_json', ''),
                        'modifier_price_extra': line.get('modifier_price_extra', 0),
                    })
                    for line in lines
                ],
            })
            order.action_paid(payment_method=payment_method)
            display_code = (
                order.pos_order_id.mcd_display_code
                if order.pos_order_id and hasattr(order.pos_order_id, 'mcd_display_code')
                else ''
            )

            return {
                'success':      True,
                'order_id':     order.id,
                'order_name':   display_code or order.name,
                'amount_total': order.amount_total,
                'pos_order_id':  order.pos_order_id.id if order.pos_order_id else None,
                'pos_reference': display_code or (order.pos_order_id.pos_reference if order.pos_order_id else ''),
                'partner_id':   resolved_partner.id if resolved_partner else None,
            }
        except Exception as e:
            _logger.error('Kiosk order error: %s', e, exc_info=True)
            return {'success': False, 'error': str(e)}

    # â”€â”€â”€ QR PAYMENT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @http.route('/mcd_kiosk/payment/qr', type='json', auth='user', methods=['POST'], csrf=False)
    def generate_qr(self, amount=0, order_ref=''):
        bank_account = '1234567890'
        bank_bin     = '970436'   # Vietcombank
        qr_url = (
            f'https://img.vietqr.io/image/{bank_bin}-{bank_account}-compact2.png'
            f'?amount={int(amount)}&addInfo={order_ref}&accountName=MCDONALD'
        )
        return {'qr_url': qr_url, 'amount': amount, 'order_ref': order_ref}
