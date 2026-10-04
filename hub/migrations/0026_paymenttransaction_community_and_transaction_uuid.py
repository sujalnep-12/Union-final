from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("hub", "0025_userprofile_personalization_preferences")]

    operations = [
        migrations.AddField(
            model_name="paymenttransaction",
            name="community",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="payment_transactions",
                to="hub.community",
            ),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="transaction_uuid",
            field=models.CharField(blank=True, max_length=120, null=True, unique=True),
        ),
    ]
