{
    'name': 'MCD Customer',
    'version': '1.0',
    'category': 'Point of Sale',
    'summary': 'Nhan dien khach hang qua SDT tai Kiosk va POS',
    'depends': [
        'web',
        'point_of_sale',
        'contacts',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/customer_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'mcd_customer/static/src/js/pos_customer.js',
            'mcd_customer/static/src/xml/pos_customer_templates.xml',
            'mcd_customer/static/src/css/pos_customer.css',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
