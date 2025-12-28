"""
Performance Comparison Script
Compares Python collector vs Telegraf performance
"""
import time
from influxdb_client import InfluxDBClient
from influxdb_client.client.query_api import QueryApi
import os
from dotenv import load_dotenv

load_dotenv()

INFLUXDB_URL = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "vehicle-data")

def get_collector_stats(collector_name, debug=False):
    """Get statistics for a specific collector."""
    client = InfluxDBClient(
        url=INFLUXDB_URL,
        token=INFLUXDB_TOKEN,
        org=INFLUXDB_ORG
    )
    query_api = client.query_api()
    
    try:
        # First, debug: see what fields this collector is actually writing
        if debug:
            debug_query = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: -1m)
              |> filter(fn: (r) => r["_measurement"] == "device_data" and r["collector"] == "{collector_name}")
              |> group(columns: ["_field"])
              |> count()
            '''
            
            debug_result = query_api.query(query=debug_query)
            fields = {}
            for table in debug_result:
                for record in table.records:
                    field = record.values.get("_field", "unknown")
                    count = record.get_value()
                    fields[field] = count
            
            if fields:
                print(f"  Debug - {collector_name.capitalize()} fields found:")
                for field, count in sorted(fields.items()):
                    print(f"    Field '{field}': {count} points")
            else:
                print(f"  Debug - {collector_name.capitalize()}: No fields found in last 1 minute")
        
        # Query last 1 minute of data - use speed field to count messages (avoids type conflicts)
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
          |> range(start: -1m)
          |> filter(fn: (r) => r["_measurement"] == "device_data" and r["collector"] == "{collector_name}")
          |> filter(fn: (r) => r["_field"] == "speed")
          |> group(columns: ["device_id"])
          |> count()
        '''
        
        result = query_api.query(query=query)
        
        count = 0
        for table in result:
            for record in table.records:
                count += record.get_value()
        
        # If no speed field found, try counting any unique device_id + timestamp combinations
        if count == 0 and debug:
            # Try to count unique messages by device_id and time
            alt_query = f'''
            from(bucket: "{INFLUXDB_BUCKET}")
              |> range(start: -1m)
              |> filter(fn: (r) => r["_measurement"] == "device_data" and r["collector"] == "{collector_name}")
              |> group(columns: ["device_id", "_time"])
              |> count()
            '''
            alt_result = query_api.query(query=alt_query)
            alt_count = 0
            for table in alt_result:
                for record in table.records:
                    alt_count += record.get_value()
            if alt_count > 0:
                print(f"  Debug - Found {alt_count} unique time points (but no 'speed' field)")
        
        # Calculate rate (messages per second over 1 minute = 60 seconds)
        rate = count / 60.0 if count > 0 else 0
        
    except Exception as e:
        print(f"Error querying {collector_name} collector: {e}")
        import traceback
        traceback.print_exc()
        count = 0
        rate = 0
    
    client.close()
    return {"count": count, "rate_per_second": rate}

def compare_collectors(debug=False):
    """Compare both collectors."""
    print("=" * 60)
    print("Collector Performance Comparison")
    print("=" * 60)
    print()
    
    try:
        python_stats = get_collector_stats("python", debug=debug)
        print()
        telegraf_stats = get_collector_stats("telegraf", debug=debug)
        
        print(f"Python Collector:")
        print(f"  Messages (last 1 min): {python_stats['count']}")
        print(f"  Rate: {python_stats['rate_per_second']:.2f} msg/sec")
        print()
        
        print(f"Telegraf:")
        print(f"  Messages (last 1 min): {telegraf_stats['count']}")
        print(f"  Rate: {telegraf_stats['rate_per_second']:.2f} msg/sec")
        print()
        
        if python_stats['rate_per_second'] > 0 and telegraf_stats['rate_per_second'] > 0:
            improvement = ((telegraf_stats['rate_per_second'] - python_stats['rate_per_second']) / python_stats['rate_per_second']) * 100
            print(f"Telegraf is {improvement:.1f}% {'faster' if improvement > 0 else 'slower'} than Python collector")
        elif python_stats['rate_per_second'] == 0:
            print("⚠️  Python collector has no data (may not be running)")
        elif telegraf_stats['rate_per_second'] == 0:
            print("⚠️  Telegraf has no data (may not be running)")
        print("=" * 60)
    except Exception as e:
        print(f"Error comparing collectors: {e}")
        print("=" * 60)

if __name__ == "__main__":
    import sys
    debug = "--debug" in sys.argv or "-d" in sys.argv
    compare_collectors(debug=debug)