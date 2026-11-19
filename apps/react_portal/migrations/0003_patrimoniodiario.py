from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('react_portal', '0002_brokersnapshot')]

    operations = [
        migrations.CreateModel(
            name='PatrimonioDiario',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('account', models.CharField(choices=[('ana', 'Ana'), ('bruno', 'Bruno')], max_length=10)),
                ('currency', models.CharField(max_length=3)),
                ('date', models.DateField()),
                ('value', models.DecimalField(decimal_places=8, max_digits=28, null=True)),
                ('invested', models.DecimalField(decimal_places=8, max_digits=28)),
                ('gain', models.DecimalField(decimal_places=8, max_digits=28, null=True)),
                ('positions', models.PositiveIntegerField()),
                ('quoted', models.PositiveIntegerField()),
                ('captured_at', models.DateTimeField()),
            ],
            options={'ordering': ['account', 'currency', 'date'], 'default_permissions': ()},
        ),
        migrations.AddConstraint(
            model_name='patrimoniodiario',
            constraint=models.UniqueConstraint(fields=('account', 'currency', 'date'), name='patrimonio_diario_unico'),
        ),
    ]
