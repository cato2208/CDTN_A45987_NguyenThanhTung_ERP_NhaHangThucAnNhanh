{
    "name": "MCD POS Modifier",
    "version": "17.0.1.0.0",
    "category": "Point of Sale",
    "summary": "POS modifier popup based on BoM ingredients",
    "depends": ["point_of_sale", "mrp"],
    "data": [
        "views/pos_order_line_views.xml",
        "views/print_templates.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "mcd_pos_modifier/static/src/css/product_availability.css",
            "mcd_pos_modifier/static/src/js/pos_modifier_patch.js",
            "mcd_pos_modifier/static/src/xml/pos_modifier_templates.xml",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
