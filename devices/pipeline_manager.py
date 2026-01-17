"""
Pipeline Manager and Server Availability Detection - Dual-Pipeline System
Manages switching between live stream and offline pipeline modes
"""
import time
import threading
import requests
from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import os

load_dotenv()

class PipelineMode(Enum):
    """Pipeline operation modes."""
    LIVE = "live"
    OFFLINE = "offline"
    TRANSITIONING = "transitioning"

class ServerAvailabilityDetector:
    """Monitors server infrastructure health and triggers pipeline switches."""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        
        # Configuration
        self.mqtt_broker_host = os.getenv("MQTT_BROKER_HOST", "localhost")
        self.mqtt_broker_port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
        self.collector_health_url = os.getenv("COLLECTOR_HEALTH_URL", f"http://{self.mqtt_broker_host}:9091/stats")
        
        # InfluxDB configuration for direct connectivity check
        self.influxdb_url = os.getenv("INFLUXDB_URL", "http://localhost:8086")
        self.influxdb_token = os.getenv("INFLUXDB_TOKEN")
        self.influxdb_org = os.getenv("INFLUXDB_ORG", "my-org")
        
        # Thresholds
        self.connection_timeout = int(os.getenv("SERVER_CONNECTION_TIMEOUT", "30"))
        self.consecutive_failure_threshold = int(os.getenv("CONSECUTIVE_FAILURE_THRESHOLD", "5"))
        self.broker_capacity_threshold = float(os.getenv("BROKER_CAPACITY_THRESHOLD", "0.9"))
        
        # State tracking
        self.consecutive_failures = 0
        self.consecutive_influx_failures = 0
        self.last_successful_connection = None
        self.last_successful_influx_connection = None
        self.last_health_check = 0
        self.health_check_interval = 10  # seconds
        
        # MQTT test client for connectivity checks
        self._test_client = None
        self._connection_test_result = None
        self._connection_test_lock = threading.Lock()
    
    def is_influxdb_available(self) -> bool:
        """Check if InfluxDB is reachable and accepting writes."""
        if not self.influxdb_url or not self.influxdb_token:
            return True  # If not configured, assume available
            
        try:
            # Simple ping to InfluxDB health endpoint
            health_url = f"{self.influxdb_url}/health"
            response = requests.get(health_url, timeout=5)
            
            if response.status_code == 200:
                self.consecutive_influx_failures = 0
                self.last_successful_influx_connection = time.time()
                return True
            else:
                self.consecutive_influx_failures += 1
                return False
                
        except Exception as e:
            self.consecutive_influx_failures += 1
            # Only log InfluxDB errors occasionally to reduce spam
            if not hasattr(self, '_last_influx_error_time') or time.time() - self._last_influx_error_time > 60:
                print(f"WARNING [{self.device_id}] InfluxDB connectivity check failed: {e}")
                self._last_influx_error_time = time.time()
            return False

    def is_mqtt_broker_available(self) -> bool:
        """Check if MQTT broker is reachable."""
        try:
            # Create a test client for connectivity check
            test_client = mqtt.Client(client_id=f"test-{self.device_id}-{int(time.time())}")
            
            # Set up connection result tracking
            connection_result = {'connected': False, 'error': None}
            
            def on_connect(client, userdata, flags, rc):
                if rc == 0:
                    connection_result['connected'] = True
                else:
                    connection_result['error'] = f"Connection failed with code {rc}"
                client.disconnect()
            
            def on_disconnect(client, userdata, rc):
                pass
            
            test_client.on_connect = on_connect
            test_client.on_disconnect = on_disconnect
            
            # Attempt connection with timeout
            test_client.connect(self.mqtt_broker_host, self.mqtt_broker_port, keepalive=10)
            
            # Wait for connection result with timeout
            start_time = time.time()
            test_client.loop_start()
            
            while time.time() - start_time < 5:  # 5 second timeout
                if connection_result['connected'] or connection_result['error']:
                    break
                time.sleep(0.1)
            
            test_client.loop_stop()
            
            if connection_result['connected']:
                self.consecutive_failures = 0
                self.last_successful_connection = time.time()
                return True
            else:
                self.consecutive_failures += 1
                return False
                
        except Exception as e:
            self.consecutive_failures += 1
            print(f"WARNING [{self.device_id}] MQTT broker connectivity check failed: {e}")
            return False
    
    def is_collector_available(self) -> bool:
        """Check if collector service is healthy via HTTP health check."""
        # Skip health check if disabled
        if self.collector_health_url == "disabled" or not self.collector_health_url:
            return True
            
        try:
            response = requests.get(self.collector_health_url, timeout=3)
            if response.status_code == 200:
                # Check if collector is processing messages
                stats = response.json()
                # Consider collector healthy if it's receiving messages or has low error rate
                error_rate = stats.get('rates', {}).get('error_rate_percent', 0)
                throughput = stats.get('rates', {}).get('throughput_msg_per_sec', 0)
                
                # Collector is healthy if error rate < 80% OR throughput > 0
                is_healthy = error_rate < 80 or throughput > 0
                
                if not is_healthy:
                    print(f"WARNING [{self.device_id}] Collector unhealthy: error_rate={error_rate}%, throughput={throughput}")
                
                return is_healthy
            else:
                return False
        except Exception as e:
            # Only log collector errors occasionally to reduce spam
            if not hasattr(self, '_last_collector_error_time') or time.time() - self._last_collector_error_time > 60:
                print(f"WARNING [{self.device_id}] Collector health check failed: {e}")
                self._last_collector_error_time = time.time()
            return False
    
    def check_broker_capacity(self) -> bool:
        """Check if broker has capacity for new messages."""
        try:
            # This would typically check broker queue depth
            # For now, we'll use a simple heuristic based on consecutive failures
            return self.consecutive_failures < self.consecutive_failure_threshold
        except Exception as e:
            print(f"WARNING [{self.device_id}] Broker capacity check failed: {e}")
            return False
    
    def is_server_infrastructure_available(self) -> bool:
        """Comprehensive check of server infrastructure availability."""
        current_time = time.time()
        
        # Rate limit health checks to reduce spam (check every 30 seconds)
        if hasattr(self, '_last_availability_check') and current_time - self._last_availability_check < 30:
            # Use cached result
            return getattr(self, '_last_availability_result', False)
        
        self._last_availability_check = current_time
        
        # Check MQTT broker
        mqtt_available = self.is_mqtt_broker_available()
        
        # Check InfluxDB
        influxdb_available = self.is_influxdb_available()
        
        # Check collector (MQTT collector availability)
        collector_available = self.is_collector_available()
        
        # Check broker capacity
        capacity_available = self.check_broker_capacity()
        
        # Server is available ONLY if ALL components are reachable
        # This ensures devices go offline when any critical component is down
        is_available = mqtt_available and influxdb_available and collector_available and capacity_available
        
        if is_available:
            self.consecutive_failures = 0
            self.consecutive_influx_failures = 0
        else:
            # Log why server is considered unavailable (only once per check cycle)
            reasons = []
            if not mqtt_available:
                reasons.append("MQTT broker unreachable")
            if not influxdb_available:
                reasons.append("InfluxDB unreachable")
            if not collector_available:
                reasons.append("MQTT collector unreachable")
            if not capacity_available:
                reasons.append("Broker capacity exceeded")
            
            # Only log once per check cycle to reduce spam
            if not hasattr(self, '_last_logged_reasons') or self._last_logged_reasons != reasons:
                print(f"INFO [{self.device_id}] Server infrastructure unavailable: {', '.join(reasons)}")
                self._last_logged_reasons = reasons
        
        # Cache result
        self._last_availability_result = is_available
        return is_available
    
    def get_availability_stats(self) -> Dict[str, Any]:
        """Get detailed availability statistics."""
        return {
            'consecutive_failures': self.consecutive_failures,
            'consecutive_influx_failures': self.consecutive_influx_failures,
            'last_successful_connection': self.last_successful_connection,
            'last_successful_influx_connection': self.last_successful_influx_connection,
            'failure_threshold': self.consecutive_failure_threshold,
            'connection_timeout': self.connection_timeout,
            'broker_capacity_threshold': self.broker_capacity_threshold,
            'influxdb_url': self.influxdb_url,
            'influxdb_configured': bool(self.influxdb_url and self.influxdb_token)
        }


class PipelineManager:
    """Central coordinator that determines which pipeline to use based on server availability."""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.current_mode = PipelineMode.OFFLINE  # Start in offline mode for safety
        self.last_mode_switch = time.time()
        self.mode_switch_log = []
        
        # Server availability detector
        self.availability_detector = ServerAvailabilityDetector(device_id)
        
        # Mode switching configuration
        self.mode_switch_cooldown = int(os.getenv("PIPELINE_MODE_SWITCH_COOLDOWN", "10"))  # Reduced from 30 to 10 seconds
        
        # Statistics
        self.stats = {
            'mode_switches': 0,
            'time_in_live_mode': 0.0,
            'time_in_offline_mode': 0.0,
            'last_mode_start': time.time()
        }
        
        print(f"INFO [{device_id}] Pipeline Manager initialized - Starting in {self.current_mode.value} mode")
    
    def determine_pipeline_mode(self) -> PipelineMode:
        """Checks server availability and returns appropriate pipeline mode."""
        current_time = time.time()
        
        # Check server availability first
        server_available = self.availability_detector.is_server_infrastructure_available()
        target_mode = PipelineMode.LIVE if server_available else PipelineMode.OFFLINE
        
        # Allow immediate switching in both directions to prevent message loss
        # Only apply cooldown for repeated switches to the same mode (to avoid flapping)
        should_switch = False
        
        if target_mode != self.current_mode:
            # Different mode requested
            if current_time - self.last_mode_switch >= self.mode_switch_cooldown:
                # Cooldown period passed - allow switch
                should_switch = True
            elif target_mode == PipelineMode.OFFLINE:
                # Always allow immediate switch to offline to prevent message loss
                should_switch = True
                print(f"INFO [{self.availability_detector.device_id}] Immediate switch to offline mode to prevent message loss")
            elif target_mode == PipelineMode.LIVE and self.current_mode == PipelineMode.OFFLINE:
                # Allow immediate switch to live when server becomes available
                should_switch = True
                print(f"INFO [{self.availability_detector.device_id}] Immediate switch to live mode - server available")
        
        if should_switch:
            self._switch_pipeline(target_mode)
        
        return self.current_mode
    
    def _switch_pipeline(self, new_mode: PipelineMode):
        """Handles transition between live and offline modes."""
        if new_mode == self.current_mode:
            return
        
        old_mode = self.current_mode
        current_time = time.time()
        
        # Update statistics
        time_in_mode = current_time - self.stats['last_mode_start']
        if old_mode == PipelineMode.LIVE:
            self.stats['time_in_live_mode'] += time_in_mode
        elif old_mode == PipelineMode.OFFLINE:
            self.stats['time_in_offline_mode'] += time_in_mode
        
        # Set transitioning state
        self.current_mode = PipelineMode.TRANSITIONING
        
        # Log the transition
        switch_event = {
            'timestamp': current_time,
            'from_mode': old_mode.value,
            'to_mode': new_mode.value,
            'reason': self._get_switch_reason(new_mode),
            'availability_stats': self.availability_detector.get_availability_stats()
        }
        
        self.mode_switch_log.append(switch_event)
        self.stats['mode_switches'] += 1
        self.last_mode_switch = current_time
        self.stats['last_mode_start'] = current_time
        
        # Log the switch
        print(f"INFO [{self.device_id}] Pipeline mode switch: {old_mode.value} -> {new_mode.value}")
        print(f"INFO [{self.device_id}] Switch reason: {switch_event['reason']}")
        
        # Complete the transition
        self.current_mode = new_mode
        
        # Keep only recent switch events (last 100)
        if len(self.mode_switch_log) > 100:
            self.mode_switch_log = self.mode_switch_log[-100:]
    
    def _get_switch_reason(self, new_mode: PipelineMode) -> str:
        """Get human-readable reason for mode switch."""
        if new_mode == PipelineMode.LIVE:
            return "Server infrastructure became available"
        elif new_mode == PipelineMode.OFFLINE:
            failures = self.availability_detector.consecutive_failures
            threshold = self.availability_detector.consecutive_failure_threshold
            return f"Server infrastructure unavailable ({failures}/{threshold} failures)"
        else:
            return "Unknown reason"
    
    def process_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Routes message to appropriate pipeline and adds metadata."""
        # Determine current pipeline mode
        current_mode = self.determine_pipeline_mode()
        
        # Add pipeline metadata
        pipeline_metadata = {
            'mode': current_mode.value,
            'processed_at': time.time(),
            'pipeline_manager_id': self.device_id
        }
        
        # Add mode-specific metadata
        if current_mode == PipelineMode.LIVE:
            pipeline_metadata['routing'] = 'direct_mqtt'
        elif current_mode == PipelineMode.OFFLINE:
            pipeline_metadata['routing'] = 'sqlite_queue'
        
        # Create enhanced payload
        enhanced_payload = payload.copy()
        enhanced_payload['pipeline_metadata'] = pipeline_metadata
        
        return enhanced_payload
    
    def should_use_live_pipeline(self) -> bool:
        """Check if live pipeline should be used."""
        return self.determine_pipeline_mode() == PipelineMode.LIVE
    
    def should_use_offline_pipeline(self) -> bool:
        """Check if offline pipeline should be used."""
        return self.determine_pipeline_mode() == PipelineMode.OFFLINE
    
    def is_transitioning(self) -> bool:
        """Check if pipeline is currently transitioning between modes."""
        return self.current_mode == PipelineMode.TRANSITIONING
    
    def get_current_mode(self) -> PipelineMode:
        """Get current pipeline mode."""
        return self.current_mode
    
    def get_pipeline_stats(self) -> Dict[str, Any]:
        """Get comprehensive pipeline statistics."""
        current_time = time.time()
        
        # Update current mode time
        time_in_current_mode = current_time - self.stats['last_mode_start']
        
        return {
            'current_mode': self.current_mode.value,
            'mode_switches': self.stats['mode_switches'],
            'time_in_live_mode': self.stats['time_in_live_mode'] + (time_in_current_mode if self.current_mode == PipelineMode.LIVE else 0),
            'time_in_offline_mode': self.stats['time_in_offline_mode'] + (time_in_current_mode if self.current_mode == PipelineMode.OFFLINE else 0),
            'time_in_current_mode': time_in_current_mode,
            'last_mode_switch': self.last_mode_switch,
            'recent_switches': self.mode_switch_log[-10:] if self.mode_switch_log else [],
            'availability_stats': self.availability_detector.get_availability_stats()
        }
    
    def force_mode_switch(self, target_mode: PipelineMode, reason: str = "Manual override"):
        """Force a pipeline mode switch (for testing/admin purposes)."""
        if target_mode != self.current_mode:
            # Temporarily override switch reason
            old_get_switch_reason = self._get_switch_reason
            self._get_switch_reason = lambda mode: reason
            
            self._switch_pipeline(target_mode)
            
            # Restore original method
            self._get_switch_reason = old_get_switch_reason
            
            print(f"INFO [{self.device_id}] Forced pipeline mode switch to {target_mode.value}: {reason}")