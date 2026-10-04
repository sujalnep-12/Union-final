import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'union.settings'
django.setup()
from hub.models import UserFollow
count = UserFollow.objects.filter(status='pending').update(status='accepted')
print(f'Updated {count} follows to accepted')
