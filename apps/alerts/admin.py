from django.contrib import admin

from .models import Alert, Rule, RuleState, RuleVersion


class RuleVersionInline(admin.TabularInline):
    model = RuleVersion
    extra = 0
    can_delete = False
    readonly_fields = (
        "number",
        "family",
        "condition",
        "threshold",
        "currency",
        "classification",
        "created_at",
        "created_by",
        "reason",
    )


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ("name", "asset", "is_active", "current_version")
    inlines = [RuleVersionInline]


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("asset", "version", "triggered_at", "resolved_at", "seen_at", "classification")
    readonly_fields = [f.name for f in Alert._meta.fields]

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(RuleState)
