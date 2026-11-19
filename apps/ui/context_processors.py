from django.conf import settings


def portal(request):
    ctx = {"portal": settings.PORTAL}
    if (
        hasattr(request, "session")
        and getattr(request, "user", None) is not None
        and request.user.is_authenticated
    ):
        from apps.alerts import services as alert_services
        from apps.marketdata import fxservice

        ctx["moeda_visao"] = request.session.get("moeda_visao", "BRL")
        ctx["cambio_visao"] = fxservice.cambio_atual()
        ctx["alertas_nao_vistos"] = alert_services.nao_vistos()
    return ctx
