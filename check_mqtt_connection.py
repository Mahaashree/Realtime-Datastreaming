#!/usr/bin/env python3
"""
Quick script to check MQTT broker connection.
Tests both localhost and domain connections.
"""

import os
import sys
import socket
import time
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

load_dotenv()

# Get configuration
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_USE_TLS = os.getenv("MQTT_USE_TLS", "false").lower() == "true"
MQTT_TLS_INSECURE = os.getenv("MQTT_TLS_INSECURE", "false").lower() == "true"
MQTT_CA_CERTS = os.getenv("MQTT_CA_CERTS", None)
MQTT_USERNAME = os.getenv("MQTT_USERNAME", None)
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", None)

print("=" * 70)
print("MQTT Broker Connection Checker")
print("=" * 70)
print(f"Configured Host: {MQTT_BROKER_HOST}")
print(f"Configured Port: {MQTT_BROKER_PORT}")
if MQTT_USE_TLS:
    print(f"TLS: Enabled (insecure={MQTT_TLS_INSECURE})")
if MQTT_USERNAME:
    print(f"Authentication: Enabled (user={MQTT_USERNAME})")
print("=" * 70)
print()

def configure_mqtt_client(client):
    """Configure MQTT client with TLS and authentication if needed."""
    # Configure TLS if enabled
    if MQTT_USE_TLS:
        import ssl
        try:
            if MQTT_CA_CERTS:
                client.tls_set(
                    ca_certs=MQTT_CA_CERTS,
                    cert_reqs=ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else ssl.CERT_NONE
                )
            else:
                client.tls_set(
                    cert_reqs=ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else ssl.CERT_NONE
                )
            if MQTT_TLS_INSECURE:
                client.tls_insecure_set(True)
        except Exception as e:
            print(f"⚠️  TLS configuration error: {e}")
            raise
    
    # Configure authentication if provided
    if MQTT_USERNAME and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

# Test hosts to try
hosts_to_test = []
if MQTT_BROKER_HOST == "localhost" or MQTT_BROKER_HOST == "127.0.0.1":
    hosts_to_test = ["localhost", "mqtt.secruin.cloud"]
elif "secruin.cloud" in MQTT_BROKER_HOST:
    hosts_to_test = ["mqtt.secruin.cloud", "localhost"]
else:
    hosts_to_test = [MQTT_BROKER_HOST, "localhost"]

success = False
for host in hosts_to_test:
    print(f"Testing: {host}:{MQTT_BROKER_PORT}...")
    
    # Test 1: Basic TCP connection
    print(f"  [1/3] Testing TCP connection...", end=" ", flush=True)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, MQTT_BROKER_PORT))
        sock.close()
        if result == 0:
            print("✓ Port is open")
        else:
            print(f"✗ Port is closed or unreachable")
            continue
    except socket.gaierror as e:
        print(f"✗ DNS resolution failed: {e}")
        continue
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        continue
    
    # Test 2: MQTT connection
    print(f"  [2/3] Testing MQTT protocol...", end=" ", flush=True)
    connection_status = [False]  # Use list to avoid nonlocal scope issue
    
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            connection_status[0] = True
    
    try:
        client = mqtt.Client(client_id="connection_test")
        client.on_connect = on_connect
        configure_mqtt_client(client)
        client.connect(host, MQTT_BROKER_PORT, keepalive=10)
        client.loop_start()
        
        # Wait for connection
        for i in range(5):
            if connection_status[0]:
                break
            time.sleep(0.5)
        
        client.loop_stop()
        client.disconnect()
        
        if connection_status[0]:
            print("✓ MQTT connection successful")
        else:
            print("✗ MQTT connection timeout")
            continue
    except Exception as e:
        print(f"✗ MQTT error: {e}")
        continue
    
    # Test 3: Publish/Subscribe test
    print(f"  [3/3] Testing publish/subscribe...", end=" ", flush=True)
    try:
        test_client = mqtt.Client(client_id="test_pubsub")
        configure_mqtt_client(test_client)
        test_client.connect(host, MQTT_BROKER_PORT, keepalive=10)
        test_client.loop_start()
        time.sleep(0.5)
        
        # Try to publish
        result = test_client.publish("test/connection", "test message", qos=1)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            print("✓ Publish successful")
            success = True
            print(f"\n✅ Working MQTT Broker: {host}:{MQTT_BROKER_PORT}")
            print(f"\nUpdate your .env file:")
            print(f"MQTT_BROKER_HOST={host}")
            print(f"MQTT_BROKER_PORT={MQTT_BROKER_PORT}")
        else:
            print(f"✗ Publish failed (rc: {result.rc})")
        
        test_client.loop_stop()
        test_client.disconnect()
        break
        
    except Exception as e:
        print(f"✗ Error: {e}")
        continue

print()
if not success:
    print("❌ Could not connect to MQTT broker at any tested host")
    print("\nTroubleshooting:")
    print("1. For local testing:")
    print("   - Make sure Mosquitto is running: docker ps | grep mosquitto")
    print("   - Test: mosquitto_pub -h localhost -p 1883 -t test -m 'hello'")
    print("   - Use: MQTT_BROKER_HOST=localhost")
    print()
    print("2. For Cloudflare tunnel:")
    print("   - Verify tunnel is running on VPS: cloudflared tunnel info docker-services")
    print("   - Check tunnel logs for errors")
    print("   - Verify DNS is configured in Cloudflare")
    print("   - Test: mosquitto_pub -h mqtt.secruin.cloud -p 1883 -t test -m 'hello'")
    sys.exit(1)
else:
    print("=" * 70)
    sys.exit(0)

