from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("hub", "0027_community_theme_preset"),
    ]

    operations = [
        migrations.CreateModel(
            name="PayoutRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10, validators=[MinValueValidator(Decimal("10000.00"))])),
                ("payout_method", models.CharField(choices=[("bank", "Bank account"), ("esewa", "eSewa"), ("khalti", "Khalti")], max_length=20)),
                ("status", models.CharField(choices=[("pending", "Pending review"), ("processing", "Processing"), ("paid", "Paid"), ("rejected", "Rejected"), ("cancelled", "Cancelled")], default="pending", max_length=20)),
                ("creator_note", models.TextField(blank=True)),
                ("admin_note", models.TextField(blank=True)),
                ("reference_code", models.CharField(blank=True, max_length=120)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("community", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payout_requests", to="hub.community")),
                ("processed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="processed_community_payouts", to=settings.AUTH_USER_MODEL)),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="community_payout_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-requested_at"]},
        ),
    ]
