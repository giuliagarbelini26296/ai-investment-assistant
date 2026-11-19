from django.db import migrations


def names(apps,schema_editor):
    model=apps.get_model('managed_portfolios','Portfolio')
    model.objects.get_or_create(slug='ana',defaults={'name':'Ana','legacy':True})
    model.objects.get_or_create(slug='bruno',defaults={'name':'Bruno','legacy':False})


class Migration(migrations.Migration):
    dependencies=[('managed_portfolios','0001_initial')]
    operations=[migrations.RunPython(names,migrations.RunPython.noop)]
