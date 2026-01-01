#!/usr/bin/env python3
"""Quick end-to-end verification of MQTT → Collector → InfluxDB pipeline"""

import paho.mqtt.client as mqtt
import json
import time
from influxdb_client import InfluxDBClient
from influxdb_client.client.query_api import QueryApi
import os
from dotenv import load_dotenv

load_dotenv()

print('🔍 Testing MQTT → Collector → InfluxDB pipeline...\n')

# 1. Publish
print('1️⃣ Publishing test message...')
mqtt_client = mqtt.Client(client_id='e2e_test')
try:
    mqtt_client.connect('localhost', 1883, 60)
    payload = {
        'device_id': 'e2e_test_001',
        'timestamp': time.time(),
        'speed': 88.5,
        'cpu_usage': 55.0,
        'ram_usage': 65.0
    }
    mqtt_client.publish('device/data/e2e_test_001', json.dumps(payload), qos=1)
    mqtt_client.disconnect()
    print('   ✅ Published to MQTT\n')
except Exception as e:
    print(f'   ❌ MQTT publish failed: {e}\n')
    exit(1)

# 2. Wait for collector to process
print('2️⃣ Waiting for collector to process (5 seconds)...')
time.sleep(5)



# 3. Check InfluxDB
print('3️⃣ Checking InfluxDB...')
try:
    print('   🔧 Connecting to InfluxDB...')
    influx_client = InfluxDBClient(
        url=os.getenv('INFLUXDB_URL', 'http://localhost:8086'),
        token=os.getenv('INFLUXDB_TOKEN'),
        org=os.getenv('INFLUXDB_ORG', 'my-org')
    )
    query_api = influx_client.query_api()
    print(f'   ✅ Connected to InfluxDB')
    print(f'      URL: {os.getenv("INFLUXDB_URL", "http://localhost:8086")}')
    print(f'      Org: {os.getenv("INFLUXDB_ORG", "my-org")}')
    print(f'      Bucket: vehicle-data\n')

    # First check: Count ALL device_data records (no time limit)
    print('   📊 Step 1: Counting total device_data records (all time)...')
    query_all = '''
    from(bucket: "vehicle-data")
      |> range(start: 1970-01-01T00:00:00Z)
      |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> count()
    '''
    
    result_all = query_api.query(query_all)
    total = 0
    for table in result_all:
        for record in table.records:
            total = record.get_value()
    print(f'      Total device_data records (all time): {total}')
    
    # Also check recent records
    query_recent_count = '''
    from(bucket: "vehicle-data")
      |> range(start: -1h)
      |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> count()
    '''
    result_recent = query_api.query(query_recent_count)
    recent_total = 0
    for table in result_recent:
        for record in table.records:
            recent_total = record.get_value()
    print(f'      Total device_data records in last 1 hour: {recent_total}\n')
    
    # Second check: Get ALL records (no time limit) to see timestamps
    print('   🔍 Step 2: Checking for e2e_test_001 records (all time)...')
    query_recent = '''
    from(bucket: "vehicle-data")
      |> range(start: 1970-01-01T00:00:00Z)
      |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> filter(fn: (r) => r["device_id"] == "e2e_test_001")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n: 10)
    '''
    
    result_recent = query_api.query(query_recent)
    found_any = False
    record_count = 0
    for table in result_recent:
        for record in table.records:
            found_any = True
            record_count += 1
            if record_count == 1:
                print(f'      ✅ Found {record_count} record(s) for e2e_test_001:')
            print(f'         Record {record_count}:')
            print(f'            Field: {record.get_field()}')
            print(f'            Value: {record.get_value()}')
            print(f'            Time: {record.get_time()}')
            print(f'            Device: {record.values.get("device_id")}')
    
    if not found_any:
        print(f'      ❌ No records found for e2e_test_001\n')
    
    # Third check: Specific query for speed field (all time)
    print('   🎯 Step 3: Querying for speed field specifically (all time)...')
    query = '''
    from(bucket: "vehicle-data")
      |> range(start: 1970-01-01T00:00:00Z)
              |> filter(fn: (r) => r["_measurement"] == "device_data")
      |> filter(fn: (r) => r["device_id"] == "e2e_test_001")
      |> filter(fn: (r) => r["_field"] == "speed")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n: 1)
            '''
    
    result = query_api.query(query)
    found = False
            for table in result:
                for record in table.records:
            print(f'      ✅ FOUND speed field in InfluxDB!')
            print(f'         Speed: {record.get_value()}')
            print(f'         Time: {record.get_time()}')
            found = True
    
    # Summary
    print(f'\n   📋 Summary:')
    if found:
        print(f'      ✅ SUCCESS: Data found in InfluxDB!')
    elif found_any and not found:
        print(f'      ⚠️  Records found but speed field not found')
        print(f'      → Check if speed field was written correctly')
    elif total == 0:
        print(f'      ❌ No device_data records found at all')
        print(f'      → Write may have failed silently')
        print(f'      → Check collector logs for errors')
        print(f'      → Verify InfluxDB connection/auth')
            else:
        print(f'      ❌ Write succeeded but query returned no results')
        print(f'      → Possible issues:')
        print(f'         - Timestamp mismatch (data outside query range)')
        print(f'         - Bucket/org mismatch')
        print(f'         - Data not yet queryable (indexing delay)')
    
    influx_client.close()
        except Exception as e:
    print(f'   ❌ InfluxDB query failed: {e}')
            import traceback
            traceback.print_exc()
    
print('\n✅ Test complete!')