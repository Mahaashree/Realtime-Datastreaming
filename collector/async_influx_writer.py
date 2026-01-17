"""
Async InfluxDB Writer - Non-blocking write operations
Decouples message processing from InfluxDB I/O using thread pool
"""
import time
import threading
from queue import Queue, Empty, Full
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Callable
from influxdb_client import Point
import logging

logger = logging.getLogger(__name__)


class AsyncInfluxWriter:
    """
    Asynchronous InfluxDB writer that uses a thread pool to handle blocking I/O.
    Worker threads submit points to a queue, and dedicated write threads batch and flush.
    """
    
    def __init__(
        self,
        write_api,
        bucket: str,
        max_workers: int = 4,
        batch_size: int = 100,
        flush_interval: float = 0.05,
        max_queue_size: int = 10000,
        success_callback: Optional[Callable] = None,
        error_callback: Optional[Callable] = None
    ):
        """
        Initialize async writer optimized for low latency.
        
        Args:
            write_api: InfluxDB write API instance
            bucket: InfluxDB bucket name
            max_workers: Number of write threads (default 4 for parallelism)
            batch_size: Max points per batch (default 100 for low latency)
            flush_interval: Max seconds between flushes (default 0.05s = 50ms for low latency)
            max_queue_size: Max queue depth before backpressure
            success_callback: Called on successful write (batch_size, duration, metadata)
            error_callback: Called on write error (exception, batch_size, metadata)
            
        Note: Smaller batch_size and flush_interval reduce latency but increase write frequency.
              For high-throughput scenarios, increase batch_size (500+) and flush_interval (0.5s+).
        """
        self.write_api = write_api
        self.bucket = bucket
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.success_callback = success_callback
        self.error_callback = error_callback
        
        # Write queue
        self.write_queue = Queue(maxsize=max_queue_size)
        
        # Thread pool for write operations
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="influx-writer")
        
        # Shutdown flag
        self.shutdown_event = threading.Event()
        
        # Statistics
        self.total_writes = 0
        self.total_points = 0
        self.total_errors = 0
        self.queue_full_count = 0
        
        # Start batch processor threads
        for i in range(max_workers):
            self.executor.submit(self._batch_processor, worker_id=i)
        
        logger.info(f"AsyncInfluxWriter initialized: {max_workers} workers, batch_size={batch_size}, flush_interval={flush_interval}s")
    
    def write_async(self, points: List[Point], metadata: Optional[dict] = None) -> bool:
        """
        Submit points for async writing. Returns immediately.
        
        Args:
            points: List of InfluxDB points
            metadata: Optional metadata to track with this batch (e.g., timestamps)
        
        Returns:
            True if queued successfully, False if queue full
        """
        if not points:
            return True
        
        try:
            # Package points with metadata
            item = {
                'points': points,
                'metadata': metadata or {},
                'queued_at': time.time()
            }
            self.write_queue.put_nowait(item)
            return True
            
        except Full:
            self.queue_full_count += 1
            if self.queue_full_count % 100 == 1:
                logger.warning(f"Write queue full ({self.write_queue.qsize()}/{self.write_queue.maxsize}), applying backpressure")
            return False
    
    def _batch_processor(self, worker_id: int):
        """
        Background thread that batches and writes points to InfluxDB.
        Runs continuously until shutdown.
        """
        logger.info(f"Write worker {worker_id} started")
        
        batch = []
        batch_metadata = []
        last_flush = time.time()
        
        while not self.shutdown_event.is_set():
            try:
                # Try to get item with timeout
                item = self.write_queue.get(timeout=0.1)
                
                batch.extend(item['points'])
                batch_metadata.append(item['metadata'])
                
                # Check if we should flush
                should_flush = (
                    len(batch) >= self.batch_size or
                    (time.time() - last_flush) >= self.flush_interval
                )
                
                if should_flush:
                    self._flush_batch(batch, batch_metadata, worker_id)
                    batch = []
                    batch_metadata = []
                    last_flush = time.time()
                
            except Empty:
                # Timeout - flush partial batch if time elapsed
                if batch and (time.time() - last_flush) >= self.flush_interval:
                    self._flush_batch(batch, batch_metadata, worker_id)
                    batch = []
                    batch_metadata = []
                    last_flush = time.time()
            
            except Exception as e:
                logger.error(f"Write worker {worker_id} error: {e}")
        
        # Flush remaining batch on shutdown
        if batch:
            self._flush_batch(batch, batch_metadata, worker_id)
        
        logger.info(f"Write worker {worker_id} stopped")
    
    def _flush_batch(self, batch: List[Point], batch_metadata: List[dict], worker_id: int):
        """
        Flush a batch of points to InfluxDB (synchronous, runs in write thread).
        """
        if not batch:
            return
        
        batch_size = len(batch)
        start_time = time.time()
        
        try:
            # Synchronous write (blocks this write thread only)
            self.write_api.write(bucket=self.bucket, record=batch)
            
            duration = time.time() - start_time
            self.total_writes += 1
            self.total_points += batch_size
            
            logger.debug(f"Worker {worker_id} flushed {batch_size} points in {duration:.3f}s")
            
            # Call success callback
            if self.success_callback:
                try:
                    self.success_callback(batch_size, duration, batch_metadata)
                except Exception as e:
                    logger.error(f"Success callback error: {e}")
        
        except Exception as e:
            self.total_errors += 1
            logger.error(f"Worker {worker_id} failed to write {batch_size} points: {e}")
            
            # Call error callback
            if self.error_callback:
                try:
                    self.error_callback(e, batch_size, batch_metadata)
                except Exception as e2:
                    logger.error(f"Error callback error: {e2}")
    
    def get_stats(self) -> dict:
        """Get writer statistics."""
        return {
            'total_writes': self.total_writes,
            'total_points': self.total_points,
            'total_errors': self.total_errors,
            'queue_depth': self.write_queue.qsize(),
            'queue_full_count': self.queue_full_count,
            'queue_capacity': self.write_queue.maxsize
        }
    
    def shutdown(self, timeout: float = 30.0):
        """
        Gracefully shutdown writer, flushing remaining batches.
        
        Args:
            timeout: Max seconds to wait for shutdown
        """
        logger.info("Shutting down AsyncInfluxWriter...")
        self.shutdown_event.set()
        
        # Wait for queue to drain
        start = time.time()
        while not self.write_queue.empty() and (time.time() - start) < timeout:
            time.sleep(0.1)
        
        # Shutdown executor
        self.executor.shutdown(wait=True, timeout=timeout)
        
        stats = self.get_stats()
        logger.info(f"AsyncInfluxWriter shutdown complete: {stats['total_writes']} writes, {stats['total_points']} points, {stats['total_errors']} errors")
