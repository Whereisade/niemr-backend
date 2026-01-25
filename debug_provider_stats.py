from django.contrib.auth import get_user_model
from providers.models import ProviderProfile
from providers.enums import VerificationStatus
from django.db.models import Count, Q

User = get_user_model()

print("\n" + "="*60)
print("PROVIDER STATS DEBUG REPORT")
print("="*60 + "\n")

# Get all facilities
from facilities.models import Facility
facilities = Facility.objects.all()

print(f"📊 Total Facilities: {facilities.count()}\n")

for facility in facilities:
    print(f"\n🏥 Facility: {facility.name} (ID: {facility.id})")
    print("-" * 60)
    
    # Method 1: Direct count (what the frontend was doing)
    direct_providers = ProviderProfile.objects.filter(user__facility=facility)
    direct_count = direct_providers.count()
    print(f"   Total Providers (direct): {direct_count}")
    
    # Method 2: With filters (what the endpoint should return)
    stats = ProviderProfile.objects.filter(
        user__facility=facility
    ).aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(
            verification_status=VerificationStatus.APPROVED,
            user__is_active=True,
            user__is_sacked=False
        )),
        pending=Count('id', filter=Q(
            verification_status=VerificationStatus.PENDING
        )),
        rejected=Count('id', filter=Q(
            verification_status=VerificationStatus.REJECTED
        )),
        sacked=Count('id', filter=Q(
            user__is_sacked=True
        ))
    )
    
    print(f"   Total Providers: {stats['total']}")
    print(f"   ✅ Active Providers: {stats['active']}")
    print(f"   ⏳ Pending Providers: {stats['pending']}")
    print(f"   ❌ Rejected Providers: {stats['rejected']}")
    print(f"   🚫 Sacked Providers: {stats['sacked']}")
    
    # Show individual providers
    if direct_count > 0:
        print("\n   Provider Details:")
        for p in direct_providers.select_related('user'):
            user = p.user
            print(f"   - {user.email}")
            print(f"     Status: {p.verification_status}")
            print(f"     Active: {user.is_active}")
            print(f"     Sacked: {user.is_sacked}")
            print(f"     Type: {p.provider_type}")

print("\n" + "="*60)
print("END OF REPORT")
print("="*60 + "\n")

# Test the actual endpoint
print("\n🔧 TESTING ENDPOINT...")
from django.test import RequestFactory
from providers.views import ProviderViewSet

factory = RequestFactory()

# Get a user with a facility
test_user = User.objects.filter(facility__isnull=False).first()

if test_user:
    print(f"Using test user: {test_user.email}")
    print(f"User facility: {test_user.facility.name if test_user.facility else 'None'}")
    
    # Create a fake request
    request = factory.get('/api/providers/facility-stats/?current=true')
    request.user = test_user
    
    # Call the view
    view = ProviderViewSet.as_view({'get': 'facility_stats'})
    response = view(request)
    
    print(f"\nResponse Status: {response.status_code}")
    print(f"Response Data: {response.data}")
else:
    print("❌ No users with facility found for testing")