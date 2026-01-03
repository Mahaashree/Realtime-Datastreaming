#!/usr/bin/env python3
"""Debug script to check what fields are actually in InfluxDB"""

import os
from influxdb_client import InfluxDBClient
from dotenv import load_dotenv

load_dotenv()

client = InfluxDBClient(
    url=os.getenv('INFLUXDB_URL', 'http://localhost:8086'),
    token=os.getenv('INFLUXDB_TOKEN'),
    org=os.getenv('INFLUXDB_ORG', 'my-org')
)
query_api = client.query_api()
bucket = os.getenv('INFLUXDB_BUCKET', 'vehicle-data')

# Query to see all fields in device_data measurement
query = f'''
from(bucket: "{bucket}")
  |> range(start: -5m)
  |> filter(fn: (r) => r["_measurement"] == "device_data")
  |> group(columns: ["_field"])
  |> distinct(column: "_field")
  |> sort()
'''

print("Checking what fields exist in InfluxDB (last 5 minutes)...")
print("=" * 70)

result = query_api.query(query)
fields = []
for table in result:
    for record in table.records:
        fields.append(record.get_value())

if fields:
    print(f"✅ Found {len(fields)} field(s):")
    for field in sorted(fields):
        print(f"   - {field}")
    
    if "publish_timestamp" in fields:
        print("\n✅ publish_timestamp field exists!")
    else:
        print("\n❌ publish_timestamp field NOT found!")
        print("   → Collector may not have been restarted yet")
        print("   → Or collector code may not be writing this field")
else:
    print("❌ No fields found in last 5 minutes")
    print("   → Check if collector is running and writing data")

# Also check if we have any data at all
query_count = f'''
from(bucket: "{bucket}")
  |> range(start: -5m)
  |> filter(fn: (r) => r["_measurement"] == "device_data")
  |> count()
'''

result_count = query_api.query(query_count)
count = 0
for table in result_count:
    for record in table.records:
        count = record.get_value()

print(f"\n📊 Total records in last 5 minutes: {count}")

# Check a sample record to see its structure
query_sample = f'''
from(bucket: "{bucket}")
  |> range(start: -5m)
  |> filter(fn: (r) => r["_measurement"] == "device_data")
  |> limit(n: 1)
'''

print("\n📋 Sample record structure:")
result_sample = query_api.query(query_sample)
for table in result_sample:
    for record in table.records:
        print(f"   Field: {record.get_field()}")
        print(f"   Value: {record.get_value()}")
        print(f"   Time: {record.get_time()}")
        print(f"   Tags: {record.values}")
        break

client.close()


