from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='WorkbookState', fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('account',models.CharField(max_length=10,unique=True,choices=[('ana','Ana'),('bruno','Bruno')])),
            ('version',models.PositiveIntegerField(default=0)),('data',models.JSONField(default=dict)),
            ('updated_at',models.DateTimeField(auto_now=True)),
            ('updated_by',models.ForeignKey(null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL))], options={'default_permissions':()}),
        migrations.CreateModel(name='WorkbookRevision',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('version',models.PositiveIntegerField()),('data',models.JSONField()),('created_at',models.DateTimeField(auto_now_add=True)),
            ('user',models.ForeignKey(null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),
            ('workbook',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='revisions',to='react_portal.workbookstate'))],options={'default_permissions':()}),
        migrations.AddConstraint(model_name='workbookrevision',constraint=models.UniqueConstraint(fields=('workbook','version'),name='workbook_revision_unique')),
    ]
