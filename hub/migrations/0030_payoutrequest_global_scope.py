from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("hub", "0029_payoutrequest_minimum")]

    operations = [
        migrations.AlterField(
            model_name="payoutrequest",
            name="community",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="payout_requests",
                to="hub.community",
            ),
        ),
    ]
