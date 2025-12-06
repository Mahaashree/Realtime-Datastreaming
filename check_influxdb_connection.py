#!/usr/bin/env python3
"""
Quick script to check InfluxDB connection and show which URL works.
"""

import os
import sys
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

load_dotenv()

# Get configuration
INFLUXDB_URL_PRIMARY = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_URL_FALLBACK = os.getenv("INFLUXDB_URL_FALLBACK", None)
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "my-org")

print("=" * 70)
print("InfluxDB Connection Checker")
print("=" * 70)
print(f"Primary URL: {INFLUXDB_URL_PRIMARY}")
if INFLUXDB_URL_FALLBACK:
    print(f"Fallback URL: {INFLUXDB_URL_FALLBACK}")
else:
    print("Fallback URL: Not configured")
print(f"Organization: {INFLUXDB_ORG}")
print("=" * 70)
print()

urls_to_try = [INFLUXDB_URL_PRIMARY]
if INFLUXDB_URL_FALLBACK:
    urls_to_try.append(INFLUXDB_URL_FALLBACK)

# Always try localhost first
localhost_url = "http://localhost:8086"
if localhost_url not in urls_to_try:
    urls_to_try.insert(0, localhost_url)
elif localhost_url in urls_to_try:
    urls_to_try.remove(localhost_url)
    urls_to_try.insert(0, localhost_url)

success = False
for url in urls_to_try:
    print(f"Testing: {url}...", end=" ", flush=True)
    try:
        client = InfluxDBClient(
            url=url,
            token=INFLUXDB_TOKEN,
            org=INFLUXDB_ORG,
            timeout=5
        )
        client.ping()
        print("✓ SUCCESS")
        print(f"\n✅ Working URL: {url}")
        print(f"   Use this in your .env file:")
        print(f"   INFLUXDB_URL={url}")
        if url != INFLUXDB_URL_FALLBACK and INFLUXDB_URL_FALLBACK:
            print(f"   INFLUXDB_URL_FALLBACK={INFLUXDB_URL_FALLBACK}")
        success = True
        client.close()
        break
    except Exception as e:
        print(f"✗ FAILED: {e}")

print()
if not success:
    print("❌ Could not connect to InfluxDB at any URL")
    print("\nTroubleshooting:")
    print("1. Make sure InfluxDB is running:")
    print("   docker ps | grep influxdb")
    print("2. Test localhost connection:")
    print("   curl http://localhost:8086/health")
    print("3. Check your .env file:")
    print("   cat .env | grep INFLUXDB")
    sys.exit(1)
else:
    print("=" * 70)
    sys.exit(0)

