{
    'name': 'McDonald Kitchen Display',
    'version': '2.0',
    'summary': 'Kitchen Display System — Order cards with timer, Eat In / Take Out, Serve button',
    'depends': ['point_of_sale', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/kitchen_order_views.xml',
        'views/print_templates.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mcd_kitchen_display/static/src/css/kitchen_display.css',
            'mcd_kitchen_display/static/src/xml/kitchen_display.xml',
            'mcd_kitchen_display/static/src/js/kitchen_display.js',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
