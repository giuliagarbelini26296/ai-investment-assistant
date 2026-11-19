from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('react_portal', '0003_patrimoniodiario')]

    operations = [
        migrations.CreateModel(
            name='AssetLogo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('symbol', models.CharField(max_length=12, unique=True)),
                ('svg', models.TextField(blank=True)),
                ('found', models.BooleanField(default=False)),
                ('source_symbol', models.CharField(blank=True, max_length=12)),
                ('fetched_at', models.DateTimeField()),
            ],
            options={'default_permissions': ()},
        ),
    ]
