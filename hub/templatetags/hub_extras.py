import re
from django import template
from django.utils.html import mark_safe

register = template.Library()


@register.filter
def linkify_hashtags(value):
    if not value:
        return value
    value = str(value)
    result = re.sub(r'#(\w+)', r'<a href="/dashboard/search/?q=%23\1" class="text-blue-600 hover:underline">#\1</a>', value)
    return mark_safe(result)


@register.filter
def linkify_mentions(value):
    if not value:
        return value
    value = str(value)
    result = re.sub(r'@(\w+)', r'<a href="/dashboard/profile/\1" class="text-blue-600 hover:underline">@\1</a>', value)
    return mark_safe(result)
