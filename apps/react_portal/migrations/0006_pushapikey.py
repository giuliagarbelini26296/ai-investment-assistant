import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('react_portal', '0005_push')]

    operations = [
        migrations.CreateModel(
            name='PushApiKey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=40, unique=True)),
                ('prefix', models.CharField(max_length=12)),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={'default_permissions': ()},
        ),
        migrations.CreateModel(
            name='ExternalNotification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=80)),
                ('body', models.CharField(max_length=300)),
                ('devices', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(max_length=20)),
                ('created_at', models.DateTimeField()),
                ('key', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to='react_portal.pushapikey')),
            ],
            options={'default_permissions': ()},
        ),
        migrations.AddIndex(
            model_name='externalnotification',
            index=models.Index(fields=['key', 'created_at'], name='react_porta_extnotif_idx'),
        ),
    ]
