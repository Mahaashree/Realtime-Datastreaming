"""
Vehicle Device Simulator - PRODUCTION READY
Simulates vehicle telemetry with drowsiness detection
Includes offline queue with SQLite persistence
"""
import json
import time
import random
import os
import sqlite3
import ssl
from datetime import datetime
from threading import Thread, Event
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# Configuration
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

MQTT_USE_TLS = os.getenv("MQTT_USE_TLS", "false").lower() == "true"
MQTT_TLS_INSECURE = os.getenv("MQTT_TLS_INSECURE", "false").lower() == "true"
MQTT_CA_CERTS = os.getenv("MQTT_CA_CERTS", None)
MQTT_CERTFILE = os.getenv("MQTT_CERTFILE", None)
MQTT_KEYFILE = os.getenv("MQTT_KEYFILE", None)
MQTT_USERNAME = os.getenv("MQTT_USERNAME", None)
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", None)

PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "1.0"))  # seconds
OFFLINE_QUEUE_DIR = os.getenv("OFFLINE_QUEUE_DIR", "offline_queues")

# Drowsiness detection labels with typical confidence distribution
DETECTION_LABELS = [
    ("alert", 0.85),       # Most common - driver is alert
    ("drowsy", 0.10),      # Occasionally drowsy
    ("yawning", 0.03),     # Rare
    ("eyes_closed", 0.01), # Very rare - critical
    ("head_nodding", 0.005),
    ("distracted", 0.005)
]


class DevicePriorityManager:
    """Manages device priorities based on UUID configuration."""
    
    def __init__(self):
        self.device_priorities = {}
        self.default_priority = "normal"
        self._load_priorities()
    
    def _load_priorities(self):
        """Load device priorities from environment configuration."""
        try:
            # Load device priorities from env
            priorities_config = os.getenv("DEVICE_PRIORITIES", "")
            self.default_priority = os.getenv("DEFAULT_DEVICE_PRIORITY", "normal")
            
            if priorities_config:
                # Parse format: device_uuid:priority_level,device_uuid2:priority_level2
                for device_config in priorities_config.split(','):
                    if ':' in device_config:
                        device_uuid, priority = device_config.strip().split(':', 1)
                        self.device_priorities[device_uuid.strip()] = priority.strip()
            
            print(f"INFO Device priorities loaded: {len(self.device_priorities)} devices configured")
            print(f"INFO Default priority: {self.default_priority}")
            
        except Exception as e:
            print(f"WARNING Failed to load device priorities: {e}")
            self.device_priorities = {}
            self.default_priority = "normal"
    
    def get_device_priority(self, device_uuid: str) -> str:
        """Get priority level for a specific device UUID."""
        return self.device_priorities.get(device_uuid, self.default_priority)
    
    def get_priority_level(self, priority: str) -> int:
        """Convert priority string to numeric level for sorting."""
        priority_levels = {
            'critical': 1,
            'high': 2, 
            'normal': 3,
            'low': 4
        }
        return priority_levels.get(priority.lower(), 3)  # Default to normal (3)
    
    def is_high_priority_device(self, device_uuid: str) -> bool:
        """Check if device is high priority (critical or high)."""
        priority = self.get_device_priority(device_uuid)
        return priority.lower() in ['critical', 'high']
    
    def get_device_stats(self):
        """Get statistics about device priority distribution."""
        stats = {'critical': 0, 'high': 0, 'normal': 0, 'low': 0}
        
        for priority in self.device_priorities.values():
            if priority.lower() in stats:
                stats[priority.lower()] += 1
        
        return {
            'configured_devices': len(self.device_priorities),
            'priority_distribution': stats,
            'default_priority': self.default_priority
        }
    
    def add_device_priority(self, device_uuid: str, priority: str):
        """Dynamically add or update device priority."""
        if priority.lower() in ['critical', 'high', 'normal', 'low']:
            self.device_priorities[device_uuid] = priority.lower()
            print(f"INFO Updated priority for {device_uuid}: {priority}")
            return True
        else:
            print(f"WARNING Invalid priority '{priority}' for device {device_uuid}")
            return False
    
    def remove_device_priority(self, device_uuid: str):
        """Remove device from priority configuration (will use default)."""
        if device_uuid in self.device_priorities:
            removed_priority = self.device_priorities.pop(device_uuid)
            print(f"INFO Removed priority for {device_uuid} (was: {removed_priority})")
            return True
        return False


# Global priority manager instance
device_priority_manager = DevicePriorityManager()


class EnhancedOfflineQueue:
    """Enhanced SQLite-based persistent queue with capacity management and priority levels."""
    
    def __init__(self, device_id: str, queue_dir: str = OFFLINE_QUEUE_DIR):
        os.makedirs(queue_dir, exist_ok=True)
        self.db_path = os.path.join(queue_dir, f"{device_id}.db")
        self.max_size = int(os.getenv("OFFLINE_QUEUE_MAX_SIZE", "50000"))
        self.critical_retention = int(os.getenv("CRITICAL_MESSAGE_RETENTION", "1000"))
        self.device_id = device_id
        self._init_db()
        self._current_size = None  # Cache for performance
    
    def _init_db(self):
        """Initialize SQLite database with enhanced schema."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    priority TEXT DEFAULT 'normal',
                    retry_count INTEGER DEFAULT 0,
                    created_at REAL DEFAULT (julianday('now'))
                )
            ''')
            
            # Create index for performance
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_priority_timestamp 
                ON messages(priority DESC, timestamp ASC)
            ''')
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"WARNING [{self.device_id}] Database initialization error: {e}")
            # Try to create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            # Retry once
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        topic TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        priority TEXT DEFAULT 'normal',
                        retry_count INTEGER DEFAULT 0,
                        created_at REAL DEFAULT (julianday('now'))
                    )
                ''')
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_priority_timestamp 
                    ON messages(priority DESC, timestamp ASC)
                ''')
                conn.commit()
                conn.close()
            except Exception as e2:
                print(f"ERROR [{self.device_id}] Failed to initialize database: {e2}")
    
    def get_size(self):
        """Get current queue size with caching."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM messages')
            size = cursor.fetchone()[0]
            conn.close()
            self._current_size = size
            return size
        except Exception as e:
            print(f"WARNING [{self.device_id}] Queue size check error: {e}")
            return self._current_size or 0
    
    def check_capacity(self, priority="normal"):
        """Check if queue has space for new message."""
        current_size = self.get_size()
        
        if priority == "critical":
            # Critical messages always have space (up to max_size)
            return current_size < self.max_size
        else:
            # Normal messages need to leave space for critical messages
            return current_size < (self.max_size - self.critical_retention)
    
    def _cleanup_old_messages(self, priority="normal"):
        """Remove oldest normal priority messages to make space."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if priority == "critical":
                # For critical messages, remove oldest normal messages
                cursor.execute('''
                    DELETE FROM messages 
                    WHERE id IN (
                        SELECT id FROM messages 
                        WHERE priority = 'normal' 
                        ORDER BY timestamp ASC 
                        LIMIT 100
                    )
                ''')
            else:
                # For normal messages, remove oldest messages
                cursor.execute('''
                    DELETE FROM messages 
                    WHERE id IN (
                        SELECT id FROM messages 
                        ORDER BY timestamp ASC 
                        LIMIT 100
                    )
                ''')
            
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            
            if deleted > 0:
                print(f"INFO [{self.device_id}] Cleaned up {deleted} old messages")
            
            return deleted > 0
        except Exception as e:
            print(f"WARNING [{self.device_id}] Cleanup error: {e}")
            return False
    
    def enqueue(self, topic: str, payload: dict, priority="normal"):
        """Add message to offline queue with priority and capacity management."""
        try:
            # Check capacity
            if not self.check_capacity(priority):
                # Try to make space by cleaning up
                if not self._cleanup_old_messages(priority):
                    if priority != "critical":
                        print(f"WARNING [{self.device_id}] Queue full, dropping normal message")
                        return False
                    # For critical messages, force cleanup
                    self._cleanup_old_messages("critical")
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO messages (topic, payload, timestamp, priority) 
                   VALUES (?, ?, ?, ?)''',
                (topic, json.dumps(payload), time.time(), priority)
            )
            conn.commit()
            conn.close()
            
            # Update cached size
            if self._current_size is not None:
                self._current_size += 1
            
            return True
        except Exception as e:
            print(f"WARNING [{self.device_id}] Offline queue error: {e}")
            return False
    
    def peek_batch(self, batch_size: int = 50):
        """Retrieve a batch of messages WITHOUT removing them (for safe processing)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get messages in priority order (critical first, then by timestamp)
            cursor.execute('''
                SELECT id, topic, payload FROM messages 
                ORDER BY 
                    CASE priority 
                        WHEN 'critical' THEN 1 
                        WHEN 'high' THEN 2 
                        ELSE 3 
                    END,
                    timestamp ASC 
                LIMIT ?
            ''', (batch_size,))
            
            messages = cursor.fetchall()
            conn.close()
            
            # Return as (id, topic, payload) tuples with IDs for later deletion
            return [(msg[0], msg[1], json.loads(msg[2])) for msg in messages]
            
        except Exception as e:
            print(f"WARNING [{self.device_id}] Offline queue peek error: {e}")
            return []
    
    def delete_messages_by_ids(self, message_ids: list):
        """Permanently delete messages by their IDs after successful processing."""
        if not message_ids:
            return True
            
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            placeholders = ','.join('?' * len(message_ids))
            cursor.execute(f'DELETE FROM messages WHERE id IN ({placeholders})', message_ids)
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            # Update cached size
            if self._current_size is not None:
                self._current_size -= deleted_count
            
            return True
            
        except Exception as e:
            print(f"WARNING [{self.device_id}] Failed to delete messages from SQLite: {e}")
            return False

    def dequeue_batch(self, batch_size: int = 50):
        """Retrieve and remove a batch of messages (priority order) - LEGACY METHOD."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get messages in priority order (critical first, then by timestamp)
            cursor.execute('''
                SELECT id, topic, payload FROM messages 
                ORDER BY 
                    CASE priority 
                        WHEN 'critical' THEN 1 
                        WHEN 'high' THEN 2 
                        ELSE 3 
                    END,
                    timestamp ASC 
                LIMIT ?
            ''', (batch_size,))
            
            messages = cursor.fetchall()
            
            if messages:
                # Delete retrieved messages
                ids = [msg[0] for msg in messages]
                placeholders = ','.join('?' * len(ids))
                cursor.execute(f'DELETE FROM messages WHERE id IN ({placeholders})', ids)
                
                # Update cached size
                if self._current_size is not None:
                    self._current_size -= len(messages)
            
            conn.commit()
            conn.close()
            
            # Return as (topic, payload) tuples
            return [(msg[1], json.loads(msg[2])) for msg in messages]
            
        except Exception as e:
            print(f"WARNING [{self.device_id}] Offline queue dequeue error: {e}")
            return []
    
    def get_stats(self):
        """Get queue statistics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Total count
            cursor.execute('SELECT COUNT(*) FROM messages')
            total = cursor.fetchone()[0]
            
            # Count by priority
            cursor.execute('''
                SELECT priority, COUNT(*) FROM messages 
                GROUP BY priority
            ''')
            by_priority = dict(cursor.fetchall())
            
            # Oldest message age
            cursor.execute('SELECT MIN(timestamp) FROM messages')
            oldest_ts = cursor.fetchone()[0]
            oldest_age = time.time() - oldest_ts if oldest_ts else 0
            
            conn.close()
            
            return {
                'total_messages': total,
                'by_priority': by_priority,
                'oldest_message_age_seconds': oldest_age,
                'capacity_used_percent': (total / self.max_size) * 100
            }
        except Exception as e:
            return {'error': str(e)}
    
    def has_messages(self):
        """Check if queue has any messages."""
        return self.get_size() > 0


class MQTTQueueMonitor:
    """Monitor MQTT broker queue capacity and manage pending messages."""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.pending_messages = {}  # mid -> {timestamp, message}
        self.max_pending = int(os.getenv("MQTT_MAX_PENDING_MESSAGES", "1000"))
        self.consecutive_failures = 0
        self.last_capacity_check = 0
        self.capacity_check_interval = int(os.getenv("BROKER_CAPACITY_CHECK_INTERVAL", "5"))
        self.broker_available = True
        
        # Statistics
        self.stats = {
            'messages_sent': 0,
            'messages_failed': 0,
            'queue_full_events': 0,
            'broker_unavailable_events': 0,
            'avg_pending_messages': 0
        }
    
    def is_broker_available(self):
        """Check if broker has capacity for new messages."""
        current_time = time.time()
        
        # Check capacity periodically
        if current_time - self.last_capacity_check > self.capacity_check_interval:
            self.last_capacity_check = current_time
            
            # Method 1: Check pending message count
            pending_count = len(self.pending_messages)
            if pending_count >= self.max_pending:
                self.broker_available = False
                self.stats['queue_full_events'] += 1
                return False
            
            # Method 2: Check for old pending messages (timeout detection)
            timeout_threshold = 30  # 30 seconds
            timed_out_messages = [
                mid for mid, info in self.pending_messages.items()
                if current_time - info['timestamp'] > timeout_threshold
            ]
            
            if len(timed_out_messages) > 10:  # Too many timeouts
                self.broker_available = False
                self.stats['broker_unavailable_events'] += 1
                # Clean up timed out messages
                for mid in timed_out_messages:
                    del self.pending_messages[mid]
                return False
            
            # Method 3: Check consecutive failures
            if self.consecutive_failures > 5:
                self.broker_available = False
                return False
            
            self.broker_available = True
        
        return self.broker_available
    
    def publish_with_monitoring(self, client, topic: str, payload: dict, priority="normal"):
        """Publish message with queue monitoring and capacity checking."""
        
        # Check broker capacity first
        if not self.is_broker_available():
            return {
                'status': 'queue_full',
                'reason': 'Broker queue at capacity',
                'should_store_offline': True
            }
        
        try:
            # Publish with QoS 1 for acknowledgment tracking
            msg_info = client.publish(topic, json.dumps(payload), qos=1)
            
            if msg_info.rc == mqtt.MQTT_ERR_SUCCESS:
                # Track pending message
                self.pending_messages[msg_info.mid] = {
                    'timestamp': time.time(),
                    'message': payload,
                    'priority': priority,
                    'topic': topic
                }
                
                self.consecutive_failures = 0
                return {
                    'status': 'queued',
                    'mid': msg_info.mid,
                    'should_store_offline': False
                }
            else:
                # Publish failed immediately
                self.consecutive_failures += 1
                self.stats['messages_failed'] += 1
                
                return {
                    'status': 'failed',
                    'reason': f'MQTT error: {msg_info.rc}',
                    'should_store_offline': True
                }
                
        except Exception as e:
            self.consecutive_failures += 1
            self.stats['messages_failed'] += 1
            
            return {
                'status': 'error',
                'reason': str(e),
                'should_store_offline': True
            }
    
    def on_publish_callback(self, client, userdata, mid):
        """Handle successful publish acknowledgment."""
        if mid in self.pending_messages:
            # Message successfully delivered
            del self.pending_messages[mid]
            self.stats['messages_sent'] += 1
            self.consecutive_failures = max(0, self.consecutive_failures - 1)
    
    def on_disconnect_callback(self, client, userdata, rc):
        """Handle MQTT disconnection."""
        # Mark all pending messages as failed
        failed_count = len(self.pending_messages)
        self.pending_messages.clear()
        self.stats['messages_failed'] += failed_count
        self.broker_available = False
        
        print(f"INFO [{self.device_id}] MQTT disconnected, {failed_count} pending messages lost")
    
    def get_stats(self):
        """Get monitoring statistics."""
        current_pending = len(self.pending_messages)
        
        # Update average pending messages
        if self.stats['messages_sent'] > 0:
            self.stats['avg_pending_messages'] = current_pending
        
        return {
            **self.stats,
            'current_pending_messages': current_pending,
            'broker_available': self.broker_available,
            'consecutive_failures': self.consecutive_failures,
            'max_pending_threshold': self.max_pending
        }
    
    def cleanup_old_pending(self):
        """Clean up old pending messages (called periodically)."""
        current_time = time.time()
        timeout_threshold = 60  # 1 minute timeout
        
        timed_out = [
            mid for mid, info in self.pending_messages.items()
            if current_time - info['timestamp'] > timeout_threshold
        ]
        
        for mid in timed_out:
            del self.pending_messages[mid]
            self.stats['messages_failed'] += 1
        
        if timed_out:
            print(f"WARNING [{self.device_id}] Cleaned up {len(timed_out)} timed-out pending messages")


class VehicleDevice:
    """Simulates a vehicle device with telemetry and drowsiness detection."""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.is_connected = False
        self.shutdown_event = Event()
        
        # Enhanced offline queue
        self.offline_queue = EnhancedOfflineQueue(device_id)
        
        # MQTT queue monitor
        self.mqtt_monitor = MQTTQueueMonitor(device_id)
        
        # Initialize MQTT client with stability-focused settings
        client_id = f"{device_id}-{os.getpid()}-{int(time.time())}"  # Unique client ID
        self.client = mqtt.Client(
            client_id=client_id,
            clean_session=True,   # Use clean session to reduce broker memory pressure
            protocol=mqtt.MQTTv311  # Use MQTT 3.1.1 for better compatibility
        )
        
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        
        # Configure conservative reconnection settings for stability
        self.client.reconnect_delay_set(min_delay=10, max_delay=120)  # Less aggressive reconnection
        
        # Set socket options for stability
        mqtt_keepalive = int(os.getenv("MQTT_KEEPALIVE", "60"))
        mqtt_socket_timeout = int(os.getenv("MQTT_SOCKET_TIMEOUT", "60"))
        
        self.client.socket_timeout = mqtt_socket_timeout
        self.client.socket_keepalive = mqtt_keepalive
        
        # Configure conservative message limits for stability
        self.client.max_inflight_messages_set(10)   # Reduced from 20
        self.client.max_queued_messages_set(100)    # Reduced from 200
        
        # Configure TLS
        if MQTT_USE_TLS:
            try:
                if MQTT_CA_CERTS:
                    self.client.tls_set(
                        ca_certs=MQTT_CA_CERTS,
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else ssl.CERT_NONE
                    )
                else:
                    self.client.tls_set(
                        certfile=MQTT_CERTFILE,
                        keyfile=MQTT_KEYFILE,
                        cert_reqs=ssl.CERT_REQUIRED if not MQTT_TLS_INSECURE else ssl.CERT_NONE
                    )
                
                if MQTT_TLS_INSECURE:
                    self.client.tls_insecure_set(True)
            except Exception as e:
                print(f"WARNING [{device_id}] TLS configuration failed: {e}")
        
        # Configure authentication
        if MQTT_USERNAME and MQTT_PASSWORD:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        
        # Configure reconnection with stability settings
        mqtt_reconnect_min = int(os.getenv("MQTT_RECONNECT_MIN_DELAY", "10"))
        mqtt_reconnect_max = int(os.getenv("MQTT_RECONNECT_MAX_DELAY", "120"))
        self.client.reconnect_delay_set(min_delay=mqtt_reconnect_min, max_delay=mqtt_reconnect_max)
        
        # Vehicle state
        self.speed = 0.0
        self.cpu_usage = random.uniform(20, 40)
        self.ram_usage = random.uniform(30, 50)
        
        # Statistics
        self.messages_published = 0
        self.messages_queued = 0
        self.publish_errors = 0
    
    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self.is_connected = True
            queue_size = self.offline_queue.get_size()
            
            if queue_size > 0:
                print(f"SUCCESS [{self.device_id}] Connected - flushing {queue_size} offline messages")
                self._flush_offline_queue()
            else:
                print(f"SUCCESS [{self.device_id}] Connected")
        else:
            self.is_connected = False
            error_messages = {
                1: "incorrect protocol version",
                2: "invalid client identifier",
                3: "server unavailable",
                4: "bad username or password",
                5: "not authorized"
            }
            error_msg = error_messages.get(rc, f"unknown error ({rc})")
            print(f"ERROR [{self.device_id}] Connection failed: {error_msg}")
    
    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnect callback."""
        self.is_connected = False
        # Notify monitor of disconnection
        self.mqtt_monitor.on_disconnect_callback(client, userdata, rc)
        
        # Only log connection issues occasionally to reduce spam
        if rc != 0:
            # Rate limit disconnect messages (once per minute)
            current_time = time.time()
            if not hasattr(self, '_last_disconnect_log') or current_time - self._last_disconnect_log > 60:
                disconnect_reasons = {
                    1: "incorrect protocol version",
                    2: "invalid client identifier", 
                    3: "server unavailable",
                    4: "bad username or password",
                    5: "not authorized",
                    7: "connection lost"
                }
                reason = disconnect_reasons.get(rc, f"unknown error ({rc})")
                print(f"WARNING [{self.device_id}] MQTT disconnected: {reason} - will auto-reconnect")
                self._last_disconnect_log = current_time
    
    def _on_publish(self, client, userdata, mid):
        """MQTT publish callback."""
        # Notify monitor of successful publish
        self.mqtt_monitor.on_publish_callback(client, userdata, mid)
    
    def _flush_offline_queue(self):
        """Smart offline queue flushing with broker capacity monitoring and safe SQLite cleanup."""
        try:
            batch_size = int(os.getenv("OFFLINE_QUEUE_BATCH_SIZE", "10"))
            
            while self.offline_queue.has_messages() and self.is_connected:
                # Check if broker can accept more messages
                if not self.mqtt_monitor.is_broker_available():
                    print(f"INFO [{self.device_id}] Broker queue full, pausing flush")
                    break
                
                # SAFE FLUSH: Peek messages without deleting them first
                batch = self.offline_queue.peek_batch(batch_size)
                if not batch:
                    break
                
                successfully_published_ids = []
                
                for msg_id, topic, payload in batch:
                    # Use smart publishing with monitoring
                    result = self.mqtt_monitor.publish_with_monitoring(
                        self.client, topic, payload, priority="high"  # Offline messages get high priority
                    )
                    
                    if result['status'] == 'queued':
                        # Successfully queued for MQTT - mark for deletion from SQLite
                        successfully_published_ids.append(msg_id)
                        self.messages_published += 1
                    elif result['should_store_offline']:
                        # Failed to publish - keep in SQLite
                        print(f"WARNING [{self.device_id}] Failed to publish message: {result['reason']}")
                        break
                
                # CRITICAL: Only delete messages from SQLite that were successfully published to MQTT
                if successfully_published_ids:
                    success = self.offline_queue.delete_messages_by_ids(successfully_published_ids)
                    if success:
                        print(f"SUCCESS [{self.device_id}] Flushed {len(successfully_published_ids)} messages to MQTT, permanently deleted from SQLite")
                    else:
                        print(f"ERROR [{self.device_id}] Failed to delete {len(successfully_published_ids)} messages from SQLite after successful MQTT publish")
                
                # Small delay to avoid overwhelming broker
                time.sleep(float(os.getenv("QUEUE_FULL_RETRY_DELAY", "0.1")))
            
            remaining = self.offline_queue.get_size()
            if remaining == 0:
                print(f"SUCCESS [{self.device_id}] Offline queue flushed and SQLite cleaned")
            else:
                print(f"INFO [{self.device_id}] {remaining} messages remain in offline queue")
                
        except Exception as e:
            print(f"WARNING [{self.device_id}] Flush error: {e}")
    
    def _generate_detection_label(self):
        """Generate drowsiness detection label with realistic distribution."""
        rand = random.random()
        cumulative = 0.0
        
        for label, prob in DETECTION_LABELS:
            cumulative += prob
            if rand < cumulative:
                # Confidence varies based on label
                if label == "alert":
                    confidence = random.uniform(0.85, 0.99)
                elif label == "drowsy":
                    confidence = random.uniform(0.60, 0.85)
                else:
                    confidence = random.uniform(0.70, 0.95)
                
                return label, confidence
        
        # Fallback (should rarely happen)
        return "alert", 0.90
    
    def _generate_telemetry(self):
        """Generate realistic vehicle telemetry data."""
        # Simulate speed variations (0-120 km/h)
        if random.random() < 0.05:  # 5% chance of speed change
            self.speed = max(0, min(120, self.speed + random.uniform(-20, 20)))
        
        # Simulate CPU/RAM variations
        self.cpu_usage += random.uniform(-5, 5)
        self.cpu_usage = max(10, min(95, self.cpu_usage))
        
        self.ram_usage += random.uniform(-3, 3)
        self.ram_usage = max(20, min(90, self.ram_usage))
        
        # Generate detection
        detection_label, detection_confidence = self._generate_detection_label()
        
        # Build payload
        payload = {
            "device_id": self.device_id,
            "timestamp": time.time(),
            "speed": round(self.speed, 2),
            "cpu_usage": round(self.cpu_usage, 2),
            "ram_usage": round(self.ram_usage, 2),
            "memory_total": 8192,  # MB
            "memory_used": round(8192 * self.ram_usage / 100, 2),
            "memory_available": round(8192 * (100 - self.ram_usage) / 100, 2),
            "memory_percent": round(self.ram_usage, 2),
            "disk_total": 256000,  # MB
            "disk_used": round(256000 * random.uniform(0.4, 0.7), 2),
            "disk_free": round(256000 * random.uniform(0.3, 0.6), 2),
            "disk_percent": round(random.uniform(40, 70), 2),
            "network_bytes_sent": random.randint(1000000, 5000000),
            "network_bytes_recv": random.randint(2000000, 10000000),
            "detection_label": detection_label,
            "detection_confidence": round(detection_confidence, 3)
        }
        
        return payload
    
    def _publish_loop(self):
        """Enhanced publishing loop with smart queue management."""
        while not self.shutdown_event.is_set():
            try:
                # Periodic cleanup of old pending messages
                if self.messages_published % 100 == 0:
                    self.mqtt_monitor.cleanup_old_pending()
                
                # Priority 1: Flush offline queue first (if connected)
                if self.is_connected and self.offline_queue.has_messages():
                    self._flush_offline_queue()
                
                # Priority 2: Generate and send new telemetry
                payload = self._generate_telemetry()
                topic_data = f"device/data/{self.device_id}"
                
                if self.is_connected:
                    # Use smart publishing with monitoring
                    result = self.mqtt_monitor.publish_with_monitoring(
                        self.client, topic_data, payload, priority="normal"
                    )
                    
                    if result['status'] == 'queued':
                        self.messages_published += 1
                    elif result['should_store_offline']:
                        # Store offline with appropriate priority
                        priority = "critical" if "critical" in payload.get('detection_label', '') else "normal"
                        self.offline_queue.enqueue(topic_data, payload, priority=priority)
                        self.messages_queued += 1
                        
                        # Log issues periodically
                        if result['status'] == 'queue_full':
                            if self.messages_queued % 50 == 0:
                                print(f"INFO [{self.device_id}] Broker queue full, storing offline (queued: {self.messages_queued})")
                        else:
                            self.publish_errors += 1
                            if self.publish_errors % 10 == 0:
                                print(f"WARNING [{self.device_id}] Publish errors: {self.publish_errors}, reason: {result.get('reason', 'unknown')}")
                else:
                    # Not connected - store offline
                    priority = "critical" if "critical" in payload.get('detection_label', '') else "normal"
                    self.offline_queue.enqueue(topic_data, payload, priority=priority)
                    self.messages_queued += 1
                
                # Sleep until next publish
                time.sleep(PUBLISH_INTERVAL)
                
            except Exception as e:
                print(f"WARNING [{self.device_id}] Publish loop error: {e}")
                time.sleep(1)
    
    def start(self):
        """Start the device simulator."""
        try:
            # Connect to MQTT broker
            print(f"CONNECTING [{self.device_id}] Connecting to {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}...")
            self.client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
            
            # Start MQTT loop in background
            self.client.loop_start()
            
            # Start publishing loop
            self.publish_thread = Thread(target=self._publish_loop, daemon=True)
            self.publish_thread.start()
            
        except Exception as e:
            print(f"ERROR [{self.device_id}] Start failed: {e}")
    
    def stop(self):
        """Stop the device simulator."""
        self.shutdown_event.set()
        
        # Stop MQTT
        self.client.loop_stop()
        self.client.disconnect()
        
        # Wait for publish thread
        if hasattr(self, 'publish_thread'):
            self.publish_thread.join(timeout=2)
        
        # Print stats
        queue_size = self.offline_queue.get_size()
        print(f"🛑 [{self.device_id}] Stopped - Published: {self.messages_published}, Queued: {queue_size}")
    
    def get_stats(self):
        """Get comprehensive device statistics."""
        queue_stats = self.offline_queue.get_stats()
        mqtt_stats = self.mqtt_monitor.get_stats()
        
        return {
            "device_id": self.device_id,
            "is_connected": self.is_connected,
            "messages_published": self.messages_published,
            "messages_queued": self.messages_queued,
            "publish_errors": self.publish_errors,
            
            # Enhanced queue statistics
            "offline_queue": {
                "size": self.offline_queue.get_size(),
                "stats": queue_stats
            },
            
            # MQTT monitoring statistics
            "mqtt_monitor": mqtt_stats,
            
            # Performance metrics
            "performance": {
                "success_rate": (self.messages_published / max(1, self.messages_published + self.publish_errors)) * 100,
                "queue_usage_percent": queue_stats.get('capacity_used_percent', 0),
                "broker_available": mqtt_stats.get('broker_available', True)
            }
        }


def start_devices(num_devices: int = 100):
    """Start multiple device simulators."""
    devices = []
    
    print("=" * 70)
    print(f"🚀 Starting {num_devices} Vehicle Device Simulators")
    print("=" * 70)
    print(f"   MQTT Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
    print(f"   Protocol: {'TLS' if MQTT_USE_TLS else 'TCP'}")
    print(f"   Publish Interval: {PUBLISH_INTERVAL}s")
    print(f"   Offline Queue: {OFFLINE_QUEUE_DIR}")
    print("=" * 70)
    
    # Start devices with staggered initialization
    for i in range(1, num_devices + 1):
        device_id = f"vehicle_{i:03d}"
        
        try:
            device = VehicleDevice(device_id)
            device.start()
            devices.append(device)
            
            print(f"  [{i:3d}/{num_devices}] Starting {device_id}... ✓ Started")
            
            # Stagger connections to avoid overwhelming broker
            if i % 10 == 0:
                time.sleep(0.5)
            else:
                time.sleep(0.05)
                
        except Exception as e:
            print(f"  [{i:3d}/{num_devices}] Starting {device_id}... ✗ Failed: {e}")
    
    print("=" * 70)
    print("All devices started. Monitoring status...")
    print("=" * 70)
    
    # Monitor devices
    try:
        while True:
            time.sleep(10)
            
            # Count connected devices
            connected = sum(1 for d in devices if d.is_connected)
            total_published = sum(d.messages_published for d in devices)
            total_queued = sum(d.offline_queue.get_size() for d in devices)
            
            print(f"STATUS: {connected}/{len(devices)} devices connected | "
                  f"Published: {total_published:,} | Queued: {total_queued:,}")
            
    except KeyboardInterrupt:
        print("\nSTOPPING all devices...")
        for device in devices:
            device.stop()
        print("SUCCESS All devices stopped")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # Check if first argument is a device ID (starts with "vehicle_") or a number
        first_arg = sys.argv[1]
        if first_arg.startswith("vehicle_"):
            # Called from run_devices.py with device ID
            device_id = first_arg
            broker_host = sys.argv[2] if len(sys.argv) > 2 else "localhost"
            broker_port = int(sys.argv[3]) if len(sys.argv) > 3 else 1883
            
            print(f"Starting device: {device_id}")
            print(f"   MQTT Broker: {broker_host}:{broker_port}")
            
            # Create and start single device
            device = VehicleDevice(device_id)
            try:
                device.start()
                # Keep the device running
                while not device.shutdown_event.is_set():
                    time.sleep(1)
            except KeyboardInterrupt:
                print(f"\nStopping {device_id}...")
                device.stop()
        else:
            # Called directly with number of devices
            try:
                num_devices = int(first_arg)
                
                # Ensure num_devices is reasonable
                if num_devices < 1 or num_devices > 1000:
                    print("ERROR Number of devices must be between 1 and 1000")
                    sys.exit(1)
                
                start_devices(num_devices)
            except ValueError:
                print(f"ERROR Invalid argument: {first_arg}")
                print("Usage: python device_simulator.py [device_id] or [num_devices]")
                sys.exit(1)
    else:
        # No arguments - use default
        start_devices(100)