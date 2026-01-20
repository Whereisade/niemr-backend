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
    path("api/labs/", include("labs.urls")),
    path("api/imaging/", include("imaging.urls")),
    path("api/pharmacy/", include("pharmacy.urls")),
    path("api/appointments/", include("appointments.urls")),
    path("api/billing/", include("billing.urls")),
    path("api/attachments/", include("attachments.urls")),
    path("api/notifications/", include("notifications.urls")),
    path("api/providers/", include("providers.urls")),
    path("api/audit/", include("audit.urls")),
    path("api/reports/", include("reports.urls")),
    path("api/emails/", include("emails.urls")),
]

# In production, media is served by Supabase Storage (S3 protocol), not Django.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)