from . import controllers
from . import models


def post_init_hook(env):
    env['mcd.kiosk.order']._mcd_fix_broken_pos_references()
