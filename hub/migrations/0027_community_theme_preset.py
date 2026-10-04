from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hub", "0026_paymenttransaction_community_and_transaction_uuid")]

    operations = [
        migrations.AddField(
            model_name="community",
            name="theme_preset",
            field=models.CharField(
                choices=[
                    ("violet", "Violet night"),
                    ("ocean", "Ocean blue"),
                    ("sunset", "Sunset coral"),
                    ("forest", "Forest green"),
                ],
                default="violet",
                max_length=20,
            ),
        ),
    ]
