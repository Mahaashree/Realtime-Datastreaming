#!/bin/bash
# Generate self-signed certificates for MQTT TLS (development/testing only)
# For production, use certificates from a trusted CA

set -e

CERT_DIR="config/certs"
DAYS_VALID=365

echo "🔐 Generating self-signed certificates for MQTT TLS..."
echo "⚠️  These are for development/testing only. Use proper CA certificates for production."

# Create certs directory
mkdir -p "$CERT_DIR"

# Generate CA private key
echo "📝 Generating CA private key..."
openssl genrsa -out "$CERT_DIR/ca.key" 2048

# Generate CA certificate
echo "📝 Generating CA certificate..."
openssl req -new -x509 -days $DAYS_VALID -key "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.crt" \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=MQTT-CA"

# Generate server private key
echo "📝 Generating server private key..."
openssl genrsa -out "$CERT_DIR/server.key" 2048

# Generate server certificate signing request
echo "📝 Generating server certificate signing request..."
openssl req -new -key "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=mosquitto"

# Generate server certificate signed by CA
echo "📝 Generating server certificate..."
openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
    -CAcreateserial -out "$CERT_DIR/server.crt" -days $DAYS_VALID \
    -extensions v3_req -extfile <(
        echo "[v3_req]"
        echo "subjectAltName = @alt_names"
        echo "[alt_names]"
        echo "DNS.1 = localhost"
        echo "DNS.2 = mosquitto"
        echo "IP.1 = 127.0.0.1"
    )

# Clean up CSR
rm "$CERT_DIR/server.csr"

# Set proper permissions
chmod 600 "$CERT_DIR"/*.key
chmod 644 "$CERT_DIR"/*.crt

echo ""
echo "✅ Certificates generated successfully!"
echo ""
echo "📁 Certificate files:"
echo "   - CA Certificate: $CERT_DIR/ca.crt"
echo "   - Server Certificate: $CERT_DIR/server.crt"
echo "   - Server Private Key: $CERT_DIR/server.key"
echo ""
echo "📋 Next steps:"
echo "   1. Update docker/mosquitto/config/mosquitto.conf to enable TLS listener on port 8883"
echo "   2. Set MQTT_USE_TLS=true in your .env file"
echo "   3. Set MQTT_BROKER_PORT=8883 in your .env file"
echo "   4. Set MQTT_CA_CERTS=docker/mosquitto/config/certs/ca.crt in your .env file"
echo "   5. Set MQTT_TLS_INSECURE=true for self-signed certs (development only)"
echo ""


