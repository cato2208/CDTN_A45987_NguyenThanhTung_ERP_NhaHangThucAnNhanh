{
    'name': 'McDonald Kiosk',
    'version': '1.0',
    'category': 'Point of Sale',
    'summary': 'Self-service kiosk for McDonald ordering with modifier, Eat In/Take Out, payment',
    'depends': [
        'web',
        'point_of_sale',
        'mcd_kitchen_display',
        'mcd_expo_display',
        'mcd_pos_order_type',
        'mcd_pos_modifier',
        'mcd_customer',
        'mcd_backend_theme',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/cleanup_data.xml',
        'views/kiosk_views.xml',
    ],
    'assets': {
        'web.assets_backend': [],
        'mcd_kiosk.assets_kiosk': [
            'mcd_kiosk/static/src/css/kiosk.css',
            'mcd_kiosk/static/src/js/kiosk.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
