"""
Dual-Pipeline Vehicle Device Simulator
Integrates Pipeline Manager and Message Priority Processor for dual-pipeline operation
"""
import json
import time
import random
import os
import ssl
from datetime import datetime
from threading import Thread, Event
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# Import dual-pipeline components
from pipeline_manager import PipelineManager, PipelineMode
from message_priority_processor import MessagePriorityProcessor, get_message_priority
from device_simulator import (
    EnhancedOfflineQueue, MQTTQueueMonitor, DevicePriorityManager, 
    device_priority_manager, DETECTION_LABELS, PUBLISH_INTERVAL,
    MQTT_BROKER_HOST, MQTT_BROKER_PORT, MQTT_USE_TLS, MQTT_TLS_INSECURE,
    MQTT_CA_CERTS, MQTT_CERTFILE, MQTT_KEYFILE, MQTT_USERNAME, MQTT_PASSWORD
)

load_dotenv()

class DualPipelineVehicleDevice:
    """Vehicle device with dual-pipeline support (live stream + offline queue)."""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.is_connected = False
        self.shutdown_event = Event()
        
        # Dual-pipeline components
        self.pipeline_manager = PipelineManager(device_id)
        self.message_processor = MessagePriorityProcessor()
        
        # Enhanced offline queue (existing)
        self.offline_queue = EnhancedOfflineQueue(device_id)
        
        # MQTT queue monitor (existing)
        self.mqtt_monitor = MQTTQueueMonitor(device_id)
        
        # Background flush control
        self.flush_thread = None
        self.flush_active = Event()  # Signal to control flushing
        self.flush_paused = Event()  # Signal when flush is paused
        
        # Initialize MQTT client with stability-focused settings
        client_id = f"{device_id}-dual-{os.getpid()}-{int(time.time())}"  # Unique client ID
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
        
        # Set socket options for stability over performance
        mqtt_keepalive = int(os.getenv("MQTT_KEEPALIVE", "60"))
        mqtt_socket_timeout = int(os.getenv("MQTT_SOCKET_TIMEOUT", "60"))
        
        self.client.socket_timeout = mqtt_socket_timeout
        self.client.socket_keepalive = mqtt_keepalive
        
        # Configure conservative message limits for stability
        self.client.max_inflight_messages_set(10)   # Reduced from 20 for stability
        self.client.max_queued_messages_set(100)    # Reduced from 200 for stability
        
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
        
        # Configure reconnection with stability focus
        mqtt_reconnect_min = int(os.getenv("MQTT_RECONNECT_MIN_DELAY", "10"))
        mqtt_reconnect_max = int(os.getenv("MQTT_RECONNECT_MAX_DELAY", "120"))
        self.client.reconnect_delay_set(min_delay=mqtt_reconnect_min, max_delay=mqtt_reconnect_max)
        
        # Vehicle state
        self.speed = 0.0
        self.cpu_usage = random.uniform(20, 40)
        self.ram_usage = random.uniform(30, 50)
        
        # Statistics
        self.messages_published = 0
        self.messages_queued_offline = 0
        self.messages_processed_live = 0
        self.publish_errors = 0
        
        print(f"INFO [{device_id}] Dual-pipeline device initialized")
    
    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self.is_connected = True
            
            # Let pipeline manager determine mode based on full server availability
            # Don't force LIVE mode - let it check InfluxDB availability too
            current_mode = self.pipeline_manager.determine_pipeline_mode()
            
            queue_size = self.offline_queue.get_size()
            
            # DEBUG: Log connection details
            print(f"DEBUG [{self.device_id}] Connected - Mode: {current_mode.value}, Queue: {queue_size} messages")
            
            if current_mode.value == "live":
                if queue_size > 0:
                    # EVENT-DRIVEN: Notify collector about offline data
                    try:
                        notification = {"queue_size": queue_size, "device_id": self.device_id}
                        notification_topic = f"device/offline/{self.device_id}"
                        client.publish(notification_topic, json.dumps(notification), qos=1)
                        print(f"📬 [{self.device_id}] Sent offline notification to collector: {queue_size} messages pending")
                    except Exception as e:
                        print(f"WARNING [{self.device_id}] Failed to send offline notification: {e}")
                    
                    print(f"SUCCESS [{self.device_id}] Connected - starting background flush for {queue_size} offline messages")
                    # Start background flush thread instead of blocking
                    self._start_background_flush()
                else:
                    print(f"SUCCESS [{self.device_id}] Connected - live pipeline active")
            else:
                print(f"SUCCESS [{self.device_id}] Connected to MQTT but server infrastructure unavailable - staying in offline mode")
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
        
        # Let pipeline manager determine mode based on server availability
        # MQTT disconnect usually means server infrastructure is unavailable
        current_mode = self.pipeline_manager.determine_pipeline_mode()
        
        # Stop background flush if running
        self._stop_background_flush()
        
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
                print(f"WARNING [{self.device_id}] MQTT disconnected: {reason} - switching to {current_mode.value} mode")
                self._last_disconnect_log = current_time
    
    def _on_publish(self, client, userdata, mid):
        """MQTT publish callback."""
        self.mqtt_monitor.on_publish_callback(client, userdata, mid)
    
    def _start_background_flush(self):
        """Start background thread for flushing offline queue."""
        # Stop any existing flush thread
        self._stop_background_flush()
        
        # Start new flush thread
        self.flush_active.set()
        self.flush_thread = Thread(target=self._background_flush_loop, daemon=True)
        self.flush_thread.start()
        print(f"INFO [{self.device_id}] Background flush thread started")
    
    def _stop_background_flush(self):
        """Stop background flush thread."""
        if self.flush_thread and self.flush_thread.is_alive():
            self.flush_active.clear()
            self.flush_thread.join(timeout=2)
            print(f"INFO [{self.device_id}] Background flush thread stopped")
    
    def _background_flush_loop(self):
        """Background thread that continuously flushes offline queue when capacity allows."""
        batch_size = int(os.getenv("OFFLINE_QUEUE_BATCH_SIZE", "10"))
        retry_delay = float(os.getenv("QUEUE_FULL_RETRY_DELAY", "2"))
        capacity_check_interval = float(os.getenv("BROKER_CAPACITY_CHECK_INTERVAL", "5"))
        
        print(f"INFO [{self.device_id}] Starting offline queue flush (batch_size={batch_size})")
        
        while self.flush_active.is_set() and not self.shutdown_event.is_set():
            try:
                # Check if we still have messages to flush
                if not self.offline_queue.has_messages():
                    print(f"SUCCESS [{self.device_id}] Offline queue fully flushed")
                    break
                
                # Check if still connected
                if not self.is_connected:
                    print(f"INFO [{self.device_id}] Connection lost, pausing flush")
                    break
                
                # Check broker capacity before flushing
                if not self.mqtt_monitor.is_broker_available():
                    self.flush_paused.set()
                    print(f"INFO [{self.device_id}] Broker queue full, pausing flush (will retry)")
                    time.sleep(retry_delay)
                    continue
                else:
                    if self.flush_paused.is_set():
                        print(f"INFO [{self.device_id}] Broker capacity available, resuming flush")
                        self.flush_paused.clear()
                
                # SAFE FLUSH: Peek messages without deleting them first
                batch = self.offline_queue.peek_batch(batch_size)
                if not batch:
                    break
                
                successfully_published_ids = []
                failed_messages = []
                
                for msg_id, topic, payload in batch:
                    # Process through live pipeline (with priority)
                    enhanced_payload = self.message_processor.process_message_for_live_pipeline(payload)
                    
                    # Use smart publishing with monitoring
                    result = self.mqtt_monitor.publish_with_monitoring(
                        self.client, topic, enhanced_payload, priority="high"  # Offline messages get high priority
                    )
                    
                    if result['status'] == 'queued':
                        # Successfully queued for MQTT - mark for deletion from SQLite
                        successfully_published_ids.append(msg_id)
                        self.messages_published += 1
                    elif result['should_store_offline']:
                        # Failed to publish - keep in SQLite, don't delete
                        failed_messages.append((topic, payload))
                        print(f"WARNING [{self.device_id}] Failed to publish message: {result['reason']}")
                        # Pause and retry
                        time.sleep(retry_delay)
                        break
                
                # CRITICAL: Only delete messages from SQLite that were successfully published to MQTT
                if successfully_published_ids:
                    success = self.offline_queue.delete_messages_by_ids(successfully_published_ids)
                    if success:
                        remaining = self.offline_queue.get_size()
                        print(f"SUCCESS [{self.device_id}] Flushed {len(successfully_published_ids)} messages to MQTT, permanently deleted from SQLite, {remaining} remaining")
                    else:
                        print(f"ERROR [{self.device_id}] Failed to delete {len(successfully_published_ids)} messages from SQLite after successful MQTT publish")
                
                # Small delay between batches to avoid overwhelming broker
                # This allows live pipeline to continue uninterrupted
                time.sleep(capacity_check_interval)
                
            except Exception as e:
                print(f"WARNING [{self.device_id}] Background flush error: {e}")
                time.sleep(retry_delay)
        
        # Final status
        remaining = self.offline_queue.get_size()
        if remaining == 0:
            print(f"SUCCESS [{self.device_id}] All offline messages flushed and deleted from SQLite")
        else:
            print(f"INFO [{self.device_id}] Flush stopped with {remaining} messages remaining in SQLite")
    
    def _flush_offline_queue(self):
        """Legacy method - now redirects to background flush."""
        if self.offline_queue.has_messages() and self.is_connected:
            self._start_background_flush()
    
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
        """Dual-pipeline publishing loop with automatic mode switching."""
        while not self.shutdown_event.is_set():
            try:
                # Periodic cleanup of old pending messages
                if self.messages_published % 100 == 0:
                    self.mqtt_monitor.cleanup_old_pending()
                
                # Generate telemetry
                payload = self._generate_telemetry()
                
                # Process through pipeline manager to determine routing
                enhanced_payload = self.pipeline_manager.process_message(payload)
                
                # Determine which pipeline to use
                if self.pipeline_manager.should_use_live_pipeline():
                    # LIVE PIPELINE: Process through message priority processor
                    self._process_live_pipeline(enhanced_payload)
                else:
                    # OFFLINE PIPELINE: Store with device UUID priority
                    self._process_offline_pipeline(enhanced_payload)
                
                # Sleep until next publish
                time.sleep(PUBLISH_INTERVAL)
                
            except Exception as e:
                print(f"WARNING [{self.device_id}] Publish loop error: {e}")
                time.sleep(1)
    
    def _process_live_pipeline(self, payload):
        """Process message through live pipeline with message priority."""
        try:
            # Add message priority based on detection label
            live_payload = self.message_processor.process_message_for_live_pipeline(payload)
            
            topic_data = f"device/data/{self.device_id}"
            
            if self.is_connected:
                # Get message priority for MQTT publishing
                detection_label = payload.get('detection_label', 'unknown')
                message_priority = get_message_priority(detection_label)
                
                # Use smart publishing with monitoring
                result = self.mqtt_monitor.publish_with_monitoring(
                    self.client, topic_data, live_payload, priority=message_priority
                )
                
                if result['status'] == 'queued':
                    self.messages_published += 1
                    self.messages_processed_live += 1
                elif result['should_store_offline']:
                    # Fallback to offline pipeline if live fails
                    print(f"INFO [{self.device_id}] Live pipeline failed, falling back to offline")
                    self._process_offline_pipeline(payload)
                else:
                    self.publish_errors += 1
            else:
                # Not connected - fallback to offline pipeline
                self._process_offline_pipeline(payload)
                
        except Exception as e:
            print(f"WARNING [{self.device_id}] Live pipeline error: {e}")
            # Fallback to offline pipeline
            self._process_offline_pipeline(payload)
    
    def _process_offline_pipeline(self, payload):
        """Process message through offline pipeline with device UUID priority."""
        try:
            # Determine priority based on device UUID and detection label
            device_priority = device_priority_manager.get_device_priority(self.device_id)
            detection_label = payload.get('detection_label', 'unknown')
            
            # Override priority for critical detection labels
            if detection_label in ['eyes_closed', 'head_nodding']:
                final_priority = 'critical'
            elif detection_label in ['drowsy', 'yawning']:
                final_priority = 'high'
            else:
                final_priority = device_priority
            
            # Add offline pipeline metadata
            if 'pipeline_metadata' not in payload:
                payload['pipeline_metadata'] = {}
            
            payload['pipeline_metadata'].update({
                'device_priority': device_priority,
                'final_priority': final_priority,
                'queued_at': time.time()
            })
            
            # Store in offline queue
            topic_data = f"device/data/{self.device_id}"
            success = self.offline_queue.enqueue(topic_data, payload, priority=final_priority)
            
            if success:
                self.messages_queued_offline += 1
            else:
                self.publish_errors += 1
                
        except Exception as e:
            print(f"WARNING [{self.device_id}] Offline pipeline error: {e}")
            self.publish_errors += 1
    
    def start(self):
        """Start the dual-pipeline device simulator."""
        try:
            # Connect to MQTT broker
            print(f"CONNECTING [{self.device_id}] Connecting to {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}...")
            self.client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
            
            # Start MQTT loop in background
            self.client.loop_start()
            
            # Start publishing loop
            self.publish_thread = Thread(target=self._publish_loop, daemon=True)
            self.publish_thread.start()
            
            # Start periodic queue check
            self.queue_check_thread = Thread(target=self._periodic_queue_check, daemon=True)
            self.queue_check_thread.start()
            
        except Exception as e:
            print(f"ERROR [{self.device_id}] Start failed: {e}")
    
    def stop(self):
        """Stop the dual-pipeline device simulator."""
        self.shutdown_event.set()
        
        # Stop background flush thread
        self._stop_background_flush()
        
        # Stop MQTT
        self.client.loop_stop()
        self.client.disconnect()
        
        # Wait for publish thread
        if hasattr(self, 'publish_thread'):
            self.publish_thread.join(timeout=2)
        
        # Print stats
        queue_size = self.offline_queue.get_size()
        print(f"[{self.device_id}] Stopped - Published: {self.messages_published}, Queued: {queue_size}")
    
    def get_comprehensive_stats(self):
        """Get comprehensive dual-pipeline statistics."""
        pipeline_stats = self.pipeline_manager.get_pipeline_stats()
        queue_stats = self.offline_queue.get_stats()
        mqtt_stats = self.mqtt_monitor.get_stats()
        processor_stats = self.message_processor.get_processing_stats()
        
        # Flush status
        flush_status = {
            'is_active': self.flush_active.is_set() if hasattr(self, 'flush_active') else False,
            'is_paused': self.flush_paused.is_set() if hasattr(self, 'flush_paused') else False,
            'thread_alive': self.flush_thread.is_alive() if self.flush_thread else False
        }
        
        return {
            "device_id": self.device_id,
            "is_connected": self.is_connected,
            
            # Message counts
            "messages_published": self.messages_published,
            "messages_queued_offline": self.messages_queued_offline,
            "messages_processed_live": self.messages_processed_live,
            "publish_errors": self.publish_errors,
            
            # Pipeline statistics
            "pipeline": pipeline_stats,
            
            # Queue statistics
            "offline_queue": {
                "size": self.offline_queue.get_size(),
                "stats": queue_stats
            },
            
            # Flush status
            "flush_status": flush_status,
            
            # MQTT monitoring
            "mqtt_monitor": mqtt_stats,
            
            # Message processing
            "message_processor": processor_stats,
            
            # Performance metrics
            "performance": {
                "total_messages": self.messages_published + self.messages_queued_offline,
                "live_pipeline_percentage": (self.messages_processed_live / max(1, self.messages_published + self.messages_queued_offline)) * 100,
                "offline_pipeline_percentage": (self.messages_queued_offline / max(1, self.messages_published + self.messages_queued_offline)) * 100,
                "success_rate": ((self.messages_published + self.messages_queued_offline) / max(1, self.messages_published + self.messages_queued_offline + self.publish_errors)) * 100,
                "current_pipeline_mode": pipeline_stats['current_mode']
            }
        }
    
    def force_server_down(self):
        """Force server down simulation for testing."""
        print(f"[{self.device_id}] Forcing server down simulation")
        self.pipeline_manager.force_mode_switch(PipelineMode.OFFLINE, "Test: Server down simulation")
        
        # Simulate connection loss
        if self.is_connected:
            self.client.disconnect()
    
    def force_server_up(self):
        """Force server up simulation for testing."""
        print(f"[{self.device_id}] Forcing server up simulation")
        self.pipeline_manager.force_mode_switch(PipelineMode.LIVE, "Test: Server up simulation")
        
        # Attempt reconnection
        if not self.is_connected:
            try:
                self.client.reconnect()
            except Exception as e:
                print(f"WARNING [{self.device_id}] Reconnection failed: {e}")
        
        # Start background flush if we have offline messages
        if self.is_connected and self.offline_queue.has_messages():
            self._start_background_flush()

    
    def _periodic_queue_check(self):
        """Periodically check offline queue and send notifications if messages accumulate."""
        while not self.shutdown_event.is_set():
            try:
                time.sleep(60)  # Check every 60 seconds
                
                # Only check if we're connected in live mode
                if self.is_connected and self.pipeline_manager.current_mode == PipelineMode.LIVE:
                    queue_size = self.offline_queue.get_size()
                    
                    # If we have accumulated offline messages while in live mode, notify collector
                    if queue_size > 100:  # Threshold: notify if more than 100 messages
                        notification = {
                            'queue_size': queue_size,
                            'device_id': self.device_id
                        }
                        notification_topic = f'device/offline/{self.device_id}'
                        result = self.client.publish(notification_topic, json.dumps(notification), qos=1)
                        
                        if result.rc == 0:
                            print(f'🔔 [{self.device_id}] Periodic check: Notified collector about {queue_size} queued messages')
                            
            except Exception as e:
                print(f'ERROR [{self.device_id}] Periodic queue check failed: {e}')


def start_dual_pipeline_devices(num_devices: int = 5):
    """Start multiple dual-pipeline device simulators."""
    devices = []
    
    print("=" * 70)
    print(f"Starting {num_devices} Dual-Pipeline Vehicle Device Simulators")
    print("=" * 70)
    print(f"   MQTT Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
    print(f"   Protocol: {'TLS' if MQTT_USE_TLS else 'TCP'}")
    print(f"   Publish Interval: {PUBLISH_INTERVAL}s")
    print("=" * 70)
    
    # Start devices with staggered initialization
    for i in range(1, num_devices + 1):
        device_id = f"vehicle_{i:03d}"
        
        try:
            device = DualPipelineVehicleDevice(device_id)
            device.start()
            devices.append(device)
            
            print(f"  [{i:3d}/{num_devices}] Starting {device_id}... ✓ Started")
            
            # Stagger connections to avoid overwhelming broker
            time.sleep(0.2)
                
        except Exception as e:
            print(f"  [{i:3d}/{num_devices}] Starting {device_id}... ✗ Failed: {e}")
    
    return devices


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # Check if first argument is a number (old behavior) or device ID (new behavior)
        first_arg = sys.argv[1]
        
        try:
            # If it's a number, use old behavior (start multiple devices)
            num_devices = int(first_arg)
            devices = start_dual_pipeline_devices(num_devices)
            
            # Keep running
            try:
                while True:
                    time.sleep(10)
                    
                    # Print status
                    connected = sum(1 for d in devices if d.is_connected)
                    total_published = sum(d.messages_published for d in devices)
                    total_queued = sum(d.offline_queue.get_size() for d in devices)
                    
                    print(f"STATUS: {connected}/{len(devices)} connected | Published: {total_published:,} | Queued: {total_queued:,}")
                    
            except KeyboardInterrupt:
                print("\nSTOPPING all devices...")
                for device in devices:
                    device.stop()
                print("SUCCESS All devices stopped")
                
        except ValueError:
            # If it's not a number, treat it as a device ID (new behavior for run_devices.py)
            device_id = first_arg
            print(f"Starting dual-pipeline device: {device_id}")
            
            device = DualPipelineVehicleDevice(device_id)
            device.start()
            
            try:
                last_stats = None
                stats_interval = 30  # Show stats every 30 seconds instead of 5
                last_stats_time = time.time()
                
                while True:
                    time.sleep(5)
                    current_time = time.time()
                    
                    # Only show stats every 30 seconds or when mode changes
                    if current_time - last_stats_time >= stats_interval:
                        stats = device.get_comprehensive_stats()
                        current_mode = stats['performance']['current_pipeline_mode']
                        queued = stats['messages_queued_offline']
                        published = stats['messages_published']
                        
                        # Show stats if mode changed or significant queue change
                        if (last_stats is None or 
                            last_stats.get('mode') != current_mode or 
                            abs(last_stats.get('queued', 0) - queued) >= 5):
                            
                            print(f"   Device stats: Mode={current_mode}, Published={published}, Queued={queued}")
                            last_stats = {'mode': current_mode, 'queued': queued, 'published': published}
                        
                        last_stats_time = current_time
                        
            except KeyboardInterrupt:
                print(f"\nStopping {device_id}...")
                device.stop()
                print(f"{device_id} stopped")
    else:
        # Start single device for testing
        device = DualPipelineVehicleDevice("test_vehicle_001")
        device.start()
        
        try:
            last_stats = None
            stats_interval = 30  # Show stats every 30 seconds
            last_stats_time = time.time()
            
            while True:
                time.sleep(5)
                current_time = time.time()
                
                if current_time - last_stats_time >= stats_interval:
                    stats = device.get_comprehensive_stats()
                    current_mode = stats['performance']['current_pipeline_mode']
                    queued = stats['messages_queued_offline']
                    published = stats['messages_published']
                    
                    print(f"Device stats: Mode={current_mode}, Published={published}, Queued={queued}")
                    last_stats_time = current_time
                    
        except KeyboardInterrupt:
            device.stop()