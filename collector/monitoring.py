"""
Monitoring & Metrics Module for POC - PRODUCTION READY
Provides structured logging, Prometheus metrics, and alerting
"""
import time
import json
import logging
import threading
from dataclasses import dataclass, asdict
from collections import deque
from typing import Optional, Dict, List
from datetime import datetime
import os

# Try to import Prometheus client (optional dependency)
try:
    from prometheus_client import Counter, Gauge, Histogram, Summary, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    print("⚠️  prometheus_client not installed. Run: pip install prometheus-client")


@dataclass
class LatencyMetrics:
    """End-to-end latency metrics."""
    p50: float
    p95: float
    p99: float
    max: float
    min: float
    avg: float
    count: int
    
    def to_dict(self):
        return asdict(self)


@dataclass
class Alert:
    """Alert data structure."""
    severity: str  # 'warning', 'error', 'critical'
    message: str
    timestamp: float
    metric_name: str
    value: float
    threshold: float
    
    def to_dict(self):
        return {
            **asdict(self),
            'datetime': datetime.fromtimestamp(self.timestamp).isoformat()
        }


class MetricsCollector:
    """
    Centralized metrics collection with Prometheus export and alerting.
    Tracks latency, throughput, errors, and system health.
    """
    
    def __init__(self, enable_prometheus: bool = True, prometheus_port: int = 9090):
        self.enable_prometheus = enable_prometheus and PROMETHEUS_AVAILABLE
        self.prometheus_port = prometheus_port
        
        # Thread-safe storage
        self.lock = threading.Lock()
        
        # Latency tracking (rolling window of last 10,000 measurements)
        self.latencies = deque(maxlen=10000)
        
        # Counters
        self.total_messages = 0
        self.processed_messages = 0
        self.error_messages = 0
        self.dropped_messages = 0
        
        # Rates (messages per second)
        self.last_rate_check = time.time()
        self.messages_since_last_check = 0
        self.current_rate = 0.0
        
        # Queue depth tracking
        self.queue_depth_history = deque(maxlen=1000)  # Last 1000 samples
        
        # Alerts
        self.alerts = deque(maxlen=100)  # Keep last 100 alerts
        self.alert_callbacks = []  # Functions to call on alert
        
        # Alert thresholds - optimized for 100-200 device POC
        self.thresholds = {
            'p95_latency_ms': float(os.getenv('ALERT_P95_LATENCY_MS', '2000')),
            'p99_latency_ms': float(os.getenv('ALERT_P99_LATENCY_MS', '5000')),
            'error_rate_percent': float(os.getenv('ALERT_ERROR_RATE_PCT', '5')),
            'drop_rate_percent': float(os.getenv('ALERT_DROP_RATE_PCT', '2')),
            'queue_depth': int(os.getenv('ALERT_QUEUE_DEPTH', '15000')),
            'throughput_min': float(os.getenv('ALERT_MIN_THROUGHPUT', '50'))  # 50 msg/s for 100 devices
        }
        
        # Prometheus metrics (if enabled)
        if self.enable_prometheus:
            self._init_prometheus_metrics()
            try:
                start_http_server(self.prometheus_port)
                print(f"📊 Prometheus metrics server started on port {self.prometheus_port}")
                print(f"   Access metrics at: http://localhost:{self.prometheus_port}/metrics")
            except Exception as e:
                print(f"⚠️  Failed to start Prometheus server: {e}")
                self.enable_prometheus = False
        
        # Background monitoring thread
        self.monitoring_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitoring_thread.start()
    
    def _init_prometheus_metrics(self):
        """Initialize Prometheus metrics."""
        self.prom_messages_total = Counter('mqtt_messages_total', 'Total MQTT messages received')
        self.prom_messages_processed = Counter('mqtt_messages_processed_total', 'Total messages successfully processed')
        self.prom_messages_errors = Counter('mqtt_messages_errors_total', 'Total messages with errors')
        self.prom_messages_dropped = Counter('mqtt_messages_dropped_total', 'Total messages dropped (queue full)')
        
        self.prom_queue_depth = Gauge('mqtt_queue_depth', 'Current message queue depth')
        self.prom_throughput = Gauge('mqtt_throughput_messages_per_second', 'Current message processing rate')
        
        self.prom_latency = Histogram(
            'mqtt_message_latency_seconds',
            'End-to-end message latency',
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        )
        
        self.prom_latency_summary = Summary('mqtt_message_latency_summary_seconds', 'Message latency percentiles')
        print("✅ Prometheus metrics initialized")
    
    def record_message_received(self):
        """Record a message received by the collector."""
        with self.lock:
            self.total_messages += 1
            self.messages_since_last_check += 1
        
        if self.enable_prometheus:
            self.prom_messages_total.inc()
    
    def record_message_processed(self, latency_seconds: Optional[float] = None):
        """Record a successfully processed message."""
        with self.lock:
            self.processed_messages += 1
            if latency_seconds is not None:
                self.latencies.append(latency_seconds)
        
        if self.enable_prometheus:
            self.prom_messages_processed.inc()
            if latency_seconds is not None:
                self.prom_latency.observe(latency_seconds)
                self.prom_latency_summary.observe(latency_seconds)
    
    def record_error(self):
        """Record a message processing error."""
        with self.lock:
            self.error_messages += 1
        
        if self.enable_prometheus:
            self.prom_messages_errors.inc()
    
    def record_drop(self):
        """Record a dropped message (queue full)."""
        with self.lock:
            self.dropped_messages += 1
        
        if self.enable_prometheus:
            self.prom_messages_dropped.inc()
    
    def record_queue_depth(self, depth: int):
        """Record current queue depth."""
        with self.lock:
            self.queue_depth_history.append(depth)
        
        if self.enable_prometheus:
            self.prom_queue_depth.set(depth)
    
    def calculate_latency_metrics(self) -> Optional[LatencyMetrics]:
        """Calculate latency percentiles from collected samples."""
        with self.lock:
            if not self.latencies:
                return None
            
            sorted_latencies = sorted(self.latencies)
            count = len(sorted_latencies)
            
            def percentile(p):
                k = (count - 1) * p
                f = int(k)
                c = f + 1 if f < count - 1 else f
                d0 = sorted_latencies[f]
                d1 = sorted_latencies[c]
                return d0 + (d1 - d0) * (k - f)
            
            return LatencyMetrics(
                p50=percentile(0.50) * 1000,
                p95=percentile(0.95) * 1000,
                p99=percentile(0.99) * 1000,
                max=max(sorted_latencies) * 1000,
                min=min(sorted_latencies) * 1000,
                avg=sum(sorted_latencies) / count * 1000,
                count=count
            )
    
    def calculate_throughput(self) -> float:
        """Calculate current throughput (msg/s)."""
        current_time = time.time()
        with self.lock:
            elapsed = current_time - self.last_rate_check
            if elapsed >= 1.0:
                self.current_rate = self.messages_since_last_check / elapsed
                self.messages_since_last_check = 0
                self.last_rate_check = current_time
                
                if self.enable_prometheus:
                    self.prom_throughput.set(self.current_rate)
            
            return self.current_rate
    
    def get_stats(self) -> Dict:
        """Get current statistics snapshot."""
        with self.lock:
            total = self.total_messages
            processed = self.processed_messages
            errors = self.error_messages
            drops = self.dropped_messages
            
            error_rate = (errors / total * 100) if total > 0 else 0
            drop_rate = (drops / total * 100) if total > 0 else 0
            success_rate = (processed / total * 100) if total > 0 else 0
            
            avg_queue_depth = sum(self.queue_depth_history) / len(self.queue_depth_history) if self.queue_depth_history else 0
            max_queue_depth = max(self.queue_depth_history) if self.queue_depth_history else 0
        
        latency = self.calculate_latency_metrics()
        throughput = self.calculate_throughput()
        
        return {
            'timestamp': time.time(),
            'datetime': datetime.now().isoformat(),
            'counters': {
                'total_messages': total,
                'processed_messages': processed,
                'error_messages': errors,
                'dropped_messages': drops
            },
            'rates': {
                'success_rate_percent': round(success_rate, 2),
                'error_rate_percent': round(error_rate, 2),
                'drop_rate_percent': round(drop_rate, 2),
                'throughput_msg_per_sec': round(throughput, 2)
            },
            'latency': latency.to_dict() if latency else None,
            'queue': {
                'current_depth': self.queue_depth_history[-1] if self.queue_depth_history else 0,
                'avg_depth': round(avg_queue_depth, 2),
                'max_depth': max_queue_depth
            }
        }
    
    def check_alerts(self, stats: Dict):
        """Check metrics against thresholds and generate alerts."""
        alerts_triggered = []
        total_msgs = stats['counters']['total_messages']
        
        # Only check if we have meaningful data
        if total_msgs < 100:
            return alerts_triggered
        
        # Check P95 latency
        if stats['latency']:
            p95 = stats['latency']['p95']
            if p95 > self.thresholds['p95_latency_ms']:
                alerts_triggered.append(Alert(
                    severity='warning',
                    message=f'P95 latency ({p95:.0f}ms) exceeds threshold ({self.thresholds["p95_latency_ms"]:.0f}ms)',
                    timestamp=time.time(),
                    metric_name='p95_latency_ms',
                    value=p95,
                    threshold=self.thresholds['p95_latency_ms']
                ))
            
            p99 = stats['latency']['p99']
            if p99 > self.thresholds['p99_latency_ms']:
                alerts_triggered.append(Alert(
                    severity='error',
                    message=f'P99 latency ({p99:.0f}ms) exceeds threshold ({self.thresholds["p99_latency_ms"]:.0f}ms)',
                    timestamp=time.time(),
                    metric_name='p99_latency_ms',
                    value=p99,
                    threshold=self.thresholds['p99_latency_ms']
                ))
        
        # Check error rate
        error_rate = stats['rates']['error_rate_percent']
        if error_rate > self.thresholds['error_rate_percent']:
            alerts_triggered.append(Alert(
                severity='error',
                message=f'Error rate ({error_rate:.2f}%) exceeds threshold ({self.thresholds["error_rate_percent"]:.2f}%)',
                timestamp=time.time(),
                metric_name='error_rate_percent',
                value=error_rate,
                threshold=self.thresholds['error_rate_percent']
            ))
        
        # Check drop rate
        drop_rate = stats['rates']['drop_rate_percent']
        if drop_rate > self.thresholds['drop_rate_percent']:
            alerts_triggered.append(Alert(
                severity='critical',
                message=f'Drop rate ({drop_rate:.2f}%) exceeds threshold ({self.thresholds["drop_rate_percent"]:.2f}%)',
                timestamp=time.time(),
                metric_name='drop_rate_percent',
                value=drop_rate,
                threshold=self.thresholds['drop_rate_percent']
            ))
        
        # Check queue depth
        queue_depth = stats['queue']['current_depth']
        if queue_depth > self.thresholds['queue_depth']:
            alerts_triggered.append(Alert(
                severity='warning',
                message=f'Queue depth ({queue_depth}) exceeds threshold ({self.thresholds["queue_depth"]})',
                timestamp=time.time(),
                metric_name='queue_depth',
                value=queue_depth,
                threshold=self.thresholds['queue_depth']
            ))
        
        # Check throughput minimum
        throughput = stats['rates']['throughput_msg_per_sec']
        if throughput > 0 and throughput < self.thresholds['throughput_min']:
            alerts_triggered.append(Alert(
                severity='warning',
                message=f'Throughput ({throughput:.2f} msg/s) below threshold ({self.thresholds["throughput_min"]:.2f} msg/s)',
                timestamp=time.time(),
                metric_name='throughput_min',
                value=throughput,
                threshold=self.thresholds['throughput_min']
            ))
        
        # Store and trigger alerts
        for alert in alerts_triggered:
            with self.lock:
                self.alerts.append(alert)
            
            for callback in self.alert_callbacks:
                try:
                    callback(alert)
                except Exception as e:
                    print(f"⚠️  Alert callback error: {e}")
        
        return alerts_triggered
    
    def register_alert_callback(self, callback):
        """Register a function to be called when alerts are triggered."""
        self.alert_callbacks.append(callback)
    
    def get_recent_alerts(self, count: int = 10) -> List[Dict]:
        """Get most recent alerts."""
        with self.lock:
            return [alert.to_dict() for alert in list(self.alerts)[-count:]]
    
    def _monitoring_loop(self):
        """Background thread that checks metrics and triggers alerts."""
        while True:
            try:
                time.sleep(10)
                stats = self.get_stats()
                alerts = self.check_alerts(stats)
                
                for alert in alerts:
                    severity_emoji = {'warning': '⚠️', 'error': '❌', 'critical': '🚨'}
                    emoji = severity_emoji.get(alert.severity, '⚠️')
                    print(f"{emoji} ALERT [{alert.severity.upper()}]: {alert.message}")
                
            except Exception as e:
                print(f"⚠️  Monitoring loop error: {e}")


class StructuredLogger:
    """Structured JSON logger for better log aggregation and analysis."""
    
    def __init__(self, name: str, log_file: Optional[str] = None):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(self._json_formatter())
        self.logger.addHandler(console_handler)
        
        if log_file:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(self._json_formatter())
            self.logger.addHandler(file_handler)
            print(f"📝 Logging to file: {log_file}")
    
    def _json_formatter(self):
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                log_data = {
                    'timestamp': record.created,
                    'datetime': datetime.fromtimestamp(record.created).isoformat(),
                    'level': record.levelname,
                    'logger': record.name,
                    'message': record.getMessage(),
                }
                
                if hasattr(record, 'extra'):
                    log_data.update(record.extra)
                
                return json.dumps(log_data)
        
        return JsonFormatter()
    
    def info(self, message: str, **extra):
        self.logger.info(message, extra={'extra': extra} if extra else {})
    
    def warning(self, message: str, **extra):
        self.logger.warning(message, extra={'extra': extra} if extra else {})
    
    def error(self, message: str, **extra):
        self.logger.error(message, extra={'extra': extra} if extra else {})
    
    def critical(self, message: str, **extra):
        self.logger.critical(message, extra={'extra': extra} if extra else {})


def print_alert_notification(alert: Alert):
    """Example alert callback - prints to console with formatting."""
    severity_colors = {
        'warning': '\033[93m',
        'error': '\033[91m',
        'critical': '\033[95m'
    }
    reset = '\033[0m'
    
    color = severity_colors.get(alert.severity, '')
    print(f"\n{color}{'='*80}{reset}")
    print(f"{color}ALERT: {alert.severity.upper()}{reset}")
    print(f"{color}{alert.message}{reset}")
    print(f"{color}Time: {datetime.fromtimestamp(alert.timestamp).strftime('%Y-%m-%d %H:%M:%S')}{reset}")
    print(f"{color}{'='*80}{reset}\n")