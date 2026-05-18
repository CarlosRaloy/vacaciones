from django.contrib import admin
from django.conf.urls.static import static
from django.conf import settings
from django.urls import path, include

# ──────── Personalización del panel /admin/ ────────
admin.site.site_header = 'Vacaciones Raloy · Panel de Soporte'
admin.site.site_title = 'Soporte Vacaciones'
admin.site.index_title = 'Mesa de ayuda — Administración'
admin.site.empty_value_display = '—'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include(('aplications.users.urls', 'users'), namespace='users')),
    path('vacaciones/', include(('aplications.vacations.urls', 'vacations'), namespace='vacations')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
