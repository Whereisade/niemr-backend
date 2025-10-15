from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/accounts/", include("accounts.urls")),
    path("api/facilities/", include("facilities.urls")),
    path("api/patients/", include("patients.urls")),
    path("api/vitals/", include("vitals.urls")),
    path("api/encounters/", include("encounters.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
