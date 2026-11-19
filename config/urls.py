from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from apps.react_portal import evolution as react_evolution
from apps.react_portal import intraday as react_intraday
from apps.react_portal import logos as react_logos
from apps.react_portal import push as react_push
from apps.react_portal import inbound as react_inbound
from apps.react_portal import mcp as react_mcp
from apps.react_portal import api as react_api, views as react_views
from apps.insights import decision_views, assistant_views
from apps.react_portal import reports
from apps.react_portal import workbook
from apps.react_portal.income import income
from apps.react_portal import install as app_install

urlpatterns = [
    path("api/v1/income/", income, name="api_income"),
    path("api/v1/evolution/", react_evolution.evolution, name="api_evolution"),
    path("api/v1/intraday/", react_intraday.intraday, name="api_intraday"),
    path("ativos/logo/<str:symbol>.svg", react_logos.logo, name="asset_logo"),
    path("sw.js", react_push.service_worker, name="service_worker"),
    path("api/v1/push/", react_push.status, name="api_push_status"),
    path("api/v1/push/subscribe/", react_push.subscribe, name="api_push_subscribe"),
    path("api/v1/push/unsubscribe/", react_push.unsubscribe, name="api_push_unsubscribe"),
    path("api/v1/push/test/", react_push.test, name="api_push_test"),
    path("api/v1/notificacoes/", react_inbound.receber, name="api_notificacoes_externas"),
    path("mcp/", react_mcp.servidor, name="mcp"),
    path("mcp", react_mcp.servidor, name="mcp_sem_barra"),
    path("manifest.webmanifest", app_install.manifest, name="app_manifest"),
    path("instalar/", app_install.instructions, name="app_install"),
    path("favicon.ico", RedirectView.as_view(url="/static/react_portal/favicon.svg", permanent=True)),
    path("", react_views.shell, name="home"),
    path("nova/", react_views.shell, name="react_alias"),
    path("api/v1/portfolio/", react_api.portfolio, name="api_portfolio_v1"),
    path("api/v1/reports/excel/", reports.excel, name="api_excel_report"),
    path("api/v1/workbook/", workbook.workbook, name="api_workbook"),
    path("api/v1/assistant/status/", workbook.assistant_status, name="api_assistant_status"),
    path("planejamento/", include("apps.insights.urls")),
    path("avaliacao/consultar/", assistant_views.query, name="decision_assistant"),
    path("avaliacao/", decision_views.dashboard, name="decision_dashboard"),
    path("carteiras/", include("apps.managed_portfolios.urls")),
    path("admin/", admin.site.urls),
    path("conta/", include("apps.accounts.urls")),
    path("", include("apps.portfolio.urls")),
    path("", include("apps.alerts.urls")),
    path("", include("apps.mercado.urls")),
    path("noticias/", include("apps.noticias.urls")),
    path("", include("apps.ui.urls")),
]
