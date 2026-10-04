from django.contrib.auth import get_user_model


def account_switcher(request):
    """Expose the accounts remembered in this authenticated browser session."""
    if not getattr(request.user, "is_authenticated", False):
        return {"account_switch_users": []}

    raw_ids = request.session.get("account_switch_user_ids", [])
    try:
        account_ids = [int(value) for value in raw_ids]
    except (TypeError, ValueError):
        account_ids = []

    account_ids = list(dict.fromkeys(account_ids))
    if request.user.pk not in account_ids:
        account_ids.insert(0, request.user.pk)

    users = get_user_model().objects.filter(pk__in=account_ids, is_active=True)
    users_by_id = {user.pk: user for user in users}
    ordered_users = [users_by_id[user_id] for user_id in account_ids if user_id in users_by_id]
    return {"account_switch_users": ordered_users}
