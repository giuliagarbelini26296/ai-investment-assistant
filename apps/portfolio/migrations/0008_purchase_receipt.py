import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('portfolio', '0007_btg_custody'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.CreateModel(
        name='PurchaseReceipt',
        fields=[
            ('submission_key', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('lot', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='purchase_receipt', to='portfolio.lot')),
            ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
        ],
        options={'default_permissions': ()},
    )]
