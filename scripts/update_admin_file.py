import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'community_hub.settings')
django.setup()
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ.get('ADMIN_USERNAME', 'union@admin')
email = os.environ.get('ADMIN_EMAIL', 'union@admin@example.com')
password = os.environ.get('ADMIN_PASSWORD', 'Damauli@2026')

user = User.objects.filter(username=username).first()
if user:
    user.set_password(password)
    user.email = email
    user.is_staff = True
    user.is_superuser = True
    user.save()
    result = f'UPDATED\nusername={username}\nemail={email}\nsuperuser={user.is_superuser}'
else:
    user = User.objects.create_superuser(username=username, email=email, password=password)
    result = f'CREATED\nusername={username}\nemail={email}\nsuperuser={user.is_superuser}'

with open('scripts/admin_update_result.txt', 'w') as f:
    f.write(result)
