from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    my_event,
    colleagues,
    OutreachEventViewSet,
    OutreachPatientViewSet,
    OutreachVitalsViewSet,
    OutreachEncounterViewSet,
    OutreachLabTestViewSet,
    OutreachLabOrderViewSet,
    OutreachLabResultViewSet,
    OutreachDrugViewSet,
    OutreachDispenseViewSet,
    OutreachImmunizationViewSet,
    OutreachVaccineCatalogViewSet,
    OutreachBloodDonationViewSet,
    OutreachReferralViewSet,
    OutreachSurgicalViewSet,
    OutreachEyeCheckViewSet,
    OutreachDentalCheckViewSet,
    OutreachCounselingViewSet,
    OutreachMaternalViewSet,
    OutreachExportViewSet,
)

router = DefaultRouter()
router.register(r"events", OutreachEventViewSet, basename="outreach-events")
router.register(r"patients", OutreachPatientViewSet, basename="outreach-patients")
router.register(r"vitals", OutreachVitalsViewSet, basename="outreach-vitals")
router.register(r"encounters", OutreachEncounterViewSet, basename="outreach-encounters")

# Lab under /labs/...
labs_router = DefaultRouter()
labs_router.register(r"tests", OutreachLabTestViewSet, basename="outreach-lab-tests")
labs_router.register(r"orders", OutreachLabOrderViewSet, basename="outreach-lab-orders")
labs_router.register(r"results", OutreachLabResultViewSet, basename="outreach-lab-results")

# Pharmacy under /pharmacy/...
pharm_router = DefaultRouter()
pharm_router.register(r"drugs", OutreachDrugViewSet, basename="outreach-drugs")
pharm_router.register(r"dispenses", OutreachDispenseViewSet, basename="outreach-dispenses")

# Other modules
router.register(r"immunizations", OutreachImmunizationViewSet, basename="outreach-immunizations")
router.register(r"immunization-vaccines", OutreachVaccineCatalogViewSet, basename="outreach-immunization-vaccines")
router.register(r"blood-donations", OutreachBloodDonationViewSet, basename="outreach-blood-donations")
router.register(r"referrals", OutreachReferralViewSet, basename="outreach-referrals")
router.register(r"surgicals", OutreachSurgicalViewSet, basename="outreach-surgicals")
router.register(r"eye-checks", OutreachEyeCheckViewSet, basename="outreach-eye-checks")
router.register(r"dental-checks", OutreachDentalCheckViewSet, basename="outreach-dental-checks")
router.register(r"counseling", OutreachCounselingViewSet, basename="outreach-counseling")
router.register(r"maternal", OutreachMaternalViewSet, basename="outreach-maternal")

# Exports read-only (OSA)
router.register(r"exports", OutreachExportViewSet, basename="outreach-exports")

urlpatterns = [
    path("", include(router.urls)),
    path("my-event/", my_event, name="outreach-my-event"),
    path("colleagues/", colleagues, name="outreach-colleagues"),
    path("labs/", include(labs_router.urls)),
    path("pharmacy/", include(pharm_router.urls)),
]
