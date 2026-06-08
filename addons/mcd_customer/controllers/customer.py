from odoo import http
from odoo.http import request


class McdCustomerController(http.Controller):

    @http.route('/mcd_customer/lookup', type='json', auth='user', methods=['POST'], csrf=False)
    def lookup(self, phone='', create=False, name=None, birthdate=None):
        """Lookup a customer by phone, optionally creating a new record."""
        partner_model = request.env['res.partner'].sudo()
        result = (
            partner_model.mcd_find_or_create(phone, name, birthdate)
            if create
            else partner_model.mcd_lookup_by_phone(phone)
        )
        if not result:
            return {'success': False, 'error': 'Số điện thoại chưa có trong danh sách khách hàng.'}
        return {'success': True, 'customer': result}

    @http.route('/mcd_customer/update', type='json', auth='user', methods=['POST'], csrf=False)
    def update_stats(self, partner_id, amount):
        """Update statistics after an order is completed by a non-POS flow."""
        partner = request.env['res.partner'].sudo().browse(partner_id)
        if not partner.exists():
            return {'success': False}
        partner.mcd_update_stats(amount)
        return {'success': True}
