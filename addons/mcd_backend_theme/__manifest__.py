{
    'name': 'McDonald Backend Theme',
    'version': '1.0',
    'category': 'Themes/Backend',
    'summary': 'McDonald-style login page and application launcher for Odoo backend',
    'depends': ['web', 'product'],
    'data': [
        'views/login_templates.xml',
        'views/backend_theme_views.xml',
        'views/product_template_views.xml',
    ],
    'assets': {
        'web.assets_web': [
            'mcd_backend_theme/static/src/css/app_launcher.css',
            'mcd_backend_theme/static/src/js/mcd_home.js',
            'mcd_backend_theme/static/src/xml/mcd_home.xml',
            'mcd_backend_theme/static/src/xml/navbar_app_launcher.xml',
        ],
        'web.assets_backend': [
            'mcd_backend_theme/static/src/css/app_launcher.css',
            'mcd_backend_theme/static/src/js/mcd_home.js',
            'mcd_backend_theme/static/src/xml/mcd_home.xml',
            'mcd_backend_theme/static/src/xml/navbar_app_launcher.xml',
        ],
        'web.assets_frontend': [
            'mcd_backend_theme/static/src/css/login_theme.css',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
