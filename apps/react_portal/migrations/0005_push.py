import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('react_portal', '0004_assetlogo'),
        ('noticias', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PushSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('endpoint', models.URLField(max_length=800, unique=True)),
                ('p256dh', models.CharField(max_length=200)),
                ('auth', models.CharField(max_length=100)),
                ('accounts', models.JSONField(default=list)),
                ('device', models.CharField(blank=True, max_length=120)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_success_at', models.DateTimeField(blank=True, null=True)),
                ('failures', models.PositiveIntegerField(default=0)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'default_permissions': ()},
        ),
        migrations.CreateModel(
            name='PushDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('symbol', models.CharField(max_length=12)),
                ('notified', models.BooleanField(default=True)),
                ('sent_at', models.DateTimeField()),
                ('article', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='noticias.article')),
                ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='react_portal.pushsubscription')),
            ],
            options={'default_permissions': ()},
        ),
        migrations.AddConstraint(
            model_name='pushdelivery',
            constraint=models.UniqueConstraint(fields=('subscription', 'article'), name='push_delivery_unica'),
        ),
        migrations.AddIndex(
            model_name='pushdelivery',
            index=models.Index(fields=['subscription', 'sent_at'], name='react_porta_subscri_push_idx'),
        ),
    ]
