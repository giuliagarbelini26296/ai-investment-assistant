from django.db import migrations,models

class Migration(migrations.Migration):
    dependencies=[('react_portal','0001_workbook')]
    operations=[migrations.CreateModel(name='BrokerSnapshot',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
        ('account',models.CharField(max_length=10,choices=[('ana','Ana'),('bruno','Bruno')])),
        ('source_key',models.CharField(max_length=120)),('captured_at',models.DateTimeField()),
        ('data',models.JSONField()),('recorded_at',models.DateTimeField(auto_now_add=True))],
        options={'default_permissions':(),'ordering':['-captured_at','-pk']}),
        migrations.AddConstraint(model_name='brokersnapshot',constraint=models.UniqueConstraint(fields=('account','source_key'),name='broker_snapshot_unique'))]
