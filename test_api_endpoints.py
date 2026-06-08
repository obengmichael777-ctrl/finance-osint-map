# test_api_endpoints.py
"""Test all API endpoints"""
import requests
import json

BASE_URL = "http://localhost:8000"

# Health check
print("1. Health Check")
response = requests.get(f"{BASE_URL}/")
print(f"   Status: {response.status_code}")
print(f"   {json.dumps(response.json(), indent=2)[:200]}")

# Get markers
print("\n2. Get Markers")
response = requests.get(f"{BASE_URL}/api/v1/markers")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"   Total markers: {data['total_count']}")
    if data['markers']:
        print(f"   Sample marker: {json.dumps(data['markers'][0], indent=2)[:300]}")

# Filter by region
print("\n3. Filter by Region (Japan)")
response = requests.get(f"{BASE_URL}/api/v1/markers?region=East%20Asia%20Developed&country=JP")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    print(f"   Japan markers: {len(response.json()['markers'])}")

# Get regions
print("\n4. Region Summary")
response = requests.get(f"{BASE_URL}/api/v1/regions")
print(f"   Status: {response.status_code}")

# Search stores
print("\n5. Search Tokyo stores")
response = requests.get(f"{BASE_URL}/api/v1/search?query=Tokyo&field=city")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"   Found: {data['total_found']} stores")
    for result in data['results'][:3]:
        print(f"   - {result['store_id']} ({result['city']})")

# Get alerts
print("\n6. Active Alerts")
response = requests.get(f"{BASE_URL}/api/v1/alerts")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    print(f"   Alert count: {response.json()['alert_count']}")

# Test store detail
print("\n7. Store Detail (first store)")
response = requests.get(f"{BASE_URL}/api/v1/markers/store_JP_001")
print(f"   Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"   Store: {data['store_id']}")
    print(f"   Location: {data['location']['city']}")
    print(f"   Revenue MTD: ${data['financials']['revenue_mtd_usd']:,.2f}")

print(f"\n{'='*50}")
print("API Testing Complete!")
print(f"Visit http://localhost:8000/docs for interactive documentation")
