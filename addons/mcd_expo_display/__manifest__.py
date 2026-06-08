{
    'name': 'McDonald Expo Display',
    'version': '2.0',
    'summary': 'Expo Display System — Order cards with timer, Eat In / Take Out, Serve button',
    'depends': ['point_of_sale', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/expo_order_views.xml',
        'views/print_templates.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mcd_expo_display/static/src/css/kitchen_display.css',
            'mcd_expo_display/static/src/xml/kitchen_display.xml',
            'mcd_expo_display/static/src/js/kitchen_display.js',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
