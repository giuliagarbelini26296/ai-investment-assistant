from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('react_portal', '0007_mcp')]

    operations = [
        migrations.AddField(
            model_name='patrimoniodiario', name='source',
            field=models.CharField(choices=[('portal', 'Registro diário do portal'), ('btg', 'Reconstruído (histórico do BTG)')],
                                   default='portal', db_default='portal', max_length=10),
        ),
        migrations.AddField(model_name='patrimoniodiario', name='flow',
                            field=models.DecimalField(blank=True, decimal_places=8, max_digits=28, null=True)),
        migrations.AddField(model_name='patrimoniodiario', name='income',
                            field=models.DecimalField(decimal_places=8, default=0, db_default=0, max_digits=28)),
    ]
