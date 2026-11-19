import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('react_portal', '0006_pushapikey')]

    operations = [
        migrations.AddField(
            model_name='pushapikey', name='scope',
            field=models.CharField(choices=[('notificacoes', 'Enviar notificações'), ('leitura', 'Ler pelo MCP')], default='notificacoes', max_length=15),
        ),
        migrations.AddField(model_name='pushapikey', name='accounts', field=models.JSONField(default=list)),
        migrations.CreateModel(
            name='McpCall',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('method', models.CharField(max_length=40)),
                ('tool', models.CharField(blank=True, max_length=40)),
                ('ok', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField()),
                ('key', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mcp_calls', to='react_portal.pushapikey')),
            ],
            options={'default_permissions': ()},
        ),
        migrations.AddIndex(model_name='mcpcall', index=models.Index(fields=['key', 'created_at'], name='react_porta_mcpcall_idx')),
    ]
