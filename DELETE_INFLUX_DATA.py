# DELETE_INFLUX_DATA.py - More aggressive version
from influxdb_client import InfluxDBClient
from datetime import datetime, timezone, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "vehicle-data")

client = InfluxDBClient(
    url=INFLUXDB_URL,
    token=INFLUXDB_TOKEN,
    org=INFLUXDB_ORG
)

delete_api = client.delete_api()

# Delete from beginning of time to 1 hour in the future (to catch any timezone issues)
start = "1970-01-01T00:00:00Z"
stop = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

try:
    # Try deleting with predicate first
    print("Attempting to delete device_data measurement...")
    delete_api.delete(
        start=start,
        stop=stop,
        predicate='_measurement="device_data"',
        bucket=INFLUXDB_BUCKET
    )
    print("✅ Deleted device_data measurement")
    
    # Also try deleting everything (nuclear option)
    # Use empty predicate to match all data
    print("Deleting ALL data in bucket (to clear any type conflicts)...")
    delete_api.delete(
        start=start,
        stop=stop,
        predicate='',  # Empty predicate matches everything
        bucket=INFLUXDB_BUCKET
    )
    print("✅ Deleted all data in bucket")
    
except Exception as e:
    print(f"❌ Error deleting data: {e}")
    import traceback
    traceback.print_exc()

client.close()