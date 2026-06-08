def _set_mcd_home_action(env):
    action = env.ref('mcd_backend_theme.action_mcd_home', raise_if_not_found=False)
    if not action:
        return
    internal_group = env.ref('base.group_user')
    users = env['res.users'].sudo().search([
        ('groups_id', 'in', internal_group.id),
        ('share', '=', False),
    ])
    users.write({'action_id': action.id})


def post_init_hook(env):
    _set_mcd_home_action(env)
