from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hub", "0028_payoutrequest")]

    operations = [
        migrations.AlterField(
            model_name="payoutrequest",
            name="amount",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=10,
                validators=[MinValueValidator(Decimal("1000.00"))],
            ),
        ),
    ]
