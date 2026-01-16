from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_nested import routers as nested_routers
from .views import (
    FacilityViewSet, 
    FacilityAdminRegisterView, 
    BedAssignmentViewSet, 
    FacilityHMOViewSet,
    FacilitySystemHMOViewSet,
    FacilityHMOManagementViewSet,
    FacilityHMOApprovalViewSet,
)

# Main router
router = DefaultRouter()
router.register("bed-assignments", BedAssignmentViewSet, basename="bed-assignment")
router.register("legacy-hmos", FacilityHMOViewSet, basename="facility-legacy-hmo")  # Legacy HMO
router.register("", FacilityViewSet, basename="facility")

# Nested routers for facility-specific HMO management
# This creates URLs like: /api/facilities/{facility_pk}/system-hmos/
# and /api/facilities/{facility_pk}/hmos/
facilities_router = nested_routers.NestedSimpleRouter(router, '', lookup='facility')
facilities_router.register(r'system-hmos', FacilitySystemHMOViewSet, basename='facility-system-hmo')
facilities_router.register(r'hmos', FacilityHMOManagementViewSet, basename='facility-hmo-management')
facilities_router.register(r'hmo-approvals', FacilityHMOApprovalViewSet, basename='facility-hmo-approval')

urlpatterns = [
    path("register-admin/", FacilityAdminRegisterView.as_view(), name="facility-register-admin"),
    path("", include(router.urls)),
    path("", include(facilities_router.urls)),
]