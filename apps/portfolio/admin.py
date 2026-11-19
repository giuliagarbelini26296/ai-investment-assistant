from django.contrib import admin

from .models import Asset, Lot, LotRevision


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "market", "asset_class", "native_currency", "custody", "is_active")
    list_filter = ("market", "custody", "is_active")
    search_fields = ("symbol", "name")


class LotRevisionInline(admin.TabularInline):
    model = LotRevision
    extra = 0
    can_delete = False
    readonly_fields = ("number", "previous", "reason", "changed_at", "changed_by")


@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):
    list_display = (
        "asset",
        "trade_date",
        "quantity",
        "unit_price",
        "fx_rate",
        "fx_origin",
        "revision",
        "cancelado_em",
    )
    list_filter = ("asset__market", "fx_origin")
    readonly_fields = ("revision", "cancelado_em", "fx_origin", "created_by")
    inlines = [LotRevisionInline]

    def has_delete_permission(self, request, obj=None):
        return False  # cancelamento lógico apenas
