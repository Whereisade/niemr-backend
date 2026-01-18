"""
Django Shell Diagnostic Script for HMO Contact Information
Run with: python manage.py shell < check_hmo_contacts.py
"""

from patients.models import FacilityHMO, SystemHMO
from django.utils import timezone
from datetime import timedelta

print("\n" + "="*60)
print("HMO CONTACT INFORMATION DIAGNOSTIC")
print("="*60 + "\n")

# Check if FacilityHMO has the contact fields
print("1. Checking if FacilityHMO model has contact fields...")
print("-" * 60)
try:
    test_fields = [
        'email', 'addresses', 'contact_numbers', 
        'contact_person_name', 'contact_person_phone', 
        'contact_person_email', 'nhis_number'
    ]
    
    model_fields = [f.name for f in FacilityHMO._meta.get_fields()]
    
    for field in test_fields:
        if field in model_fields:
            print(f"  ✓ {field} exists")
        else:
            print(f"  ✗ {field} MISSING!")
    
    print("\n✅ Model structure check complete\n")
except Exception as e:
    print(f"\n❌ Error checking model: {e}\n")

# Check recent FacilityHMOs
print("2. Checking recent FacilityHMO records...")
print("-" * 60)

# Get the 5 most recent FacilityHMOs
recent_hmos = FacilityHMO.objects.select_related('system_hmo', 'facility').order_by('-created_at')[:5]

if not recent_hmos:
    print("  ⚠ No FacilityHMO records found in database\n")
else:
    for idx, fhmo in enumerate(recent_hmos, 1):
        print(f"\n  Record #{idx}:")
        print(f"  ID: {fhmo.id}")
        print(f"  System HMO: {fhmo.system_hmo.name}")
        print(f"  Facility: {fhmo.facility.name if fhmo.facility else 'Independent Provider'}")
        print(f"  Created: {fhmo.created_at.strftime('%Y-%m-%d %H:%M')}")
        print(f"  Is Active: {fhmo.is_active}")
        
        # Check contact fields
        print(f"\n  Contact Information:")
        print(f"    Email: '{fhmo.email}' {'✓' if fhmo.email else '✗ EMPTY'}")
        print(f"    Addresses: {fhmo.addresses} {'✓' if fhmo.addresses else '✗ EMPTY'}")
        print(f"    Contact Numbers: {fhmo.contact_numbers} {'✓' if fhmo.contact_numbers else '✗ EMPTY'}")
        print(f"    Contact Person Name: '{fhmo.contact_person_name}' {'✓' if fhmo.contact_person_name else '✗ EMPTY'}")
        print(f"    Contact Person Phone: '{fhmo.contact_person_phone}' {'✓' if fhmo.contact_person_phone else '✗ EMPTY'}")
        print(f"    Contact Person Email: '{fhmo.contact_person_email}' {'✓' if fhmo.contact_person_email else '✗ EMPTY'}")
        print(f"    NHIS Number: '{fhmo.nhis_number}' {'✓' if fhmo.nhis_number else '✗ EMPTY'}")
        
        # Summary
        has_any_contact = (
            fhmo.email or 
            fhmo.addresses or 
            fhmo.contact_numbers or 
            fhmo.contact_person_name or 
            fhmo.contact_person_phone or 
            fhmo.contact_person_email
        )
        
        if has_any_contact:
            print(f"\n  ✅ HAS CONTACT DATA")
        else:
            print(f"\n  ❌ NO CONTACT DATA - This is the issue!")
        
        print("-" * 60)

# Summary
print("\n3. Summary & Recommendations")
print("-" * 60)

total_hmos = FacilityHMO.objects.count()
hmos_with_email = FacilityHMO.objects.exclude(email='').count()
hmos_with_addresses = FacilityHMO.objects.exclude(addresses=[]).count()
hmos_with_numbers = FacilityHMO.objects.exclude(contact_numbers=[]).count()
hmos_with_person = FacilityHMO.objects.exclude(contact_person_name='').count()

print(f"Total FacilityHMO records: {total_hmos}")
print(f"Records with email: {hmos_with_email}")
print(f"Records with addresses: {hmos_with_addresses}")
print(f"Records with contact numbers: {hmos_with_numbers}")
print(f"Records with contact person: {hmos_with_person}")

if total_hmos > 0:
    percentage = ((hmos_with_email + hmos_with_addresses + hmos_with_numbers) / (total_hmos * 3)) * 100
    print(f"\nContact data completeness: {percentage:.1f}%")
    
    if percentage < 50:
        print("\n❌ ISSUE CONFIRMED: Most HMO records are missing contact information")
        print("\nPossible causes:")
        print("  1. Modal is not sending contact data properly")
        print("  2. Backend enable endpoint is not saving contact data")
        print("  3. Contact fields were added after existing records were created")
        print("\nNext steps:")
        print("  1. Try adding a new HMO via the modal")
        print("  2. Check the browser console for the payload being sent")
        print("  3. Check the backend logs to see if data is received")
        print("  4. Run: python manage.py shell < update_existing_hmos.py")
    else:
        print("\n✅ Contact data looks good in database")
        print("\nIf contact info still not showing on frontend:")
        print("  1. Check the API response (browser Network tab)")
        print("  2. Check the useHMOFinancials hook")
        print("  3. Add debug console.logs to page.js")

print("\n" + "="*60)
print("DIAGNOSTIC COMPLETE")
print("="*60 + "\n")