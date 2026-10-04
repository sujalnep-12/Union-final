import os
import secrets

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'community_hub.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

username = os.environ.get('ADMIN_USERNAME') or f"devadmin_{secrets.token_hex(3)}"
email = os.environ.get('ADMIN_EMAIL') or f"{username}@example.com"
password = os.environ.get('ADMIN_PASSWORD') or secrets.token_urlsafe(10)

created = False
try:
    user = User.objects.filter(username=username).first()
    if user:
        user.set_password(password)
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.save()
        action = 'updated'
    else:
        User.objects.create_superuser(username=username, email=email, password=password)
        action = 'created'
        created = True
except Exception as e:
    print('ERROR:', e)
    raise

print(f"Superuser {action}:")
print(f"  username: {username}")
print(f"  email:    {email}")
print(f"  password: {password}")

if created:
    print('\nNOTE: Keep these credentials safe. You can change the password later in Django admin.')
