#!/usr/bin/env python3
"""
Network Failure Testing Suite

Simulates network partitions and tests offline queue functionality:
- Disconnect 20-50% of devices for 5-30 minutes
- Verify offline queue stores messages correctly
- Test queue limits (what happens if offline for hours/days)
- Measure queue flush time after reconnection (target <30s for reasonable backlog)
"""

import os
import sys
import time
import json
import signal
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Configuration
TESTING_DIR = Path(__file__).parent
RESULTS_DIR = TESTING_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
QUEUE_DIR = Path("devices/queues")
MAX_QUEUE_SIZE = 10000  # From device_simulator.py

# Test scenarios
TEST_SCENARIOS = [
    {"name": "20% Devices - 5 minutes", "disconnect_percent": 20, "duration_minutes": 5},
    {"name": "30% Devices - 10 minutes", "disconnect_percent": 30, "duration_minutes": 10},
    {"name": "50% Devices - 15 minutes", "disconnect_percent": 50, "duration_minutes": 15},
    {"name": "20% Devices - 30 minutes", "disconnect_percent": 20, "duration_minutes": 30},
    {"name": "50% Devices - 30 minutes", "disconnect_percent": 50, "duration_minutes": 30},
]


class NetworkFailureTester:
    """Test network failure scenarios and measure queue performance."""
    
    def __init__(self, num_devices: int = 50):
        self.num_devices = num_devices
        self.device_processes: Dict[str, subprocess.Popen] = {}
        self.device_ids = [f"vehicle_{i:03d}" for i in range(1, num_devices + 1)]
        self.test_results = []
        self.running = True
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, sig, frame):
        """Handle Ctrl+C gracefully."""
        print("\n\n🛑 Stopping test...")
        self.running = False
        self._stop_all_devices()
        sys.exit(0)
    
    def _start_devices(self, device_ids: Optional[List[str]] = None) -> Dict[str, subprocess.Popen]:
        """Start device simulators."""
        if device_ids is None:
            device_ids = self.device_ids
        
        processes = {}
        script_path = Path("devices/device_simulator.py")
        
        print(f"🚀 Starting {len(device_ids)} devices...")
        for device_id in device_ids:
            try:
                process = subprocess.Popen(
                    [sys.executable, str(script_path), device_id, BROKER_HOST, str(BROKER_PORT)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                processes[device_id] = process
                time.sleep(0.1)  # Small delay between starts
            except Exception as e:
                print(f"   ❌ Failed to start {device_id}: {e}")
        
        # Wait for devices to connect
        print("   ⏳ Waiting for devices to connect...")
        time.sleep(5)
        
        return processes
    
    def _stop_devices(self, device_ids: List[str]):
        """Stop specific devices by terminating their processes."""
        for device_id in device_ids:
            if device_id in self.device_processes:
                try:
                    self.device_processes[device_id].terminate()
                    self.device_processes[device_id].wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.device_processes[device_id].kill()
                except Exception as e:
                    print(f"   ⚠️  Error stopping {device_id}: {e}")
                finally:
                    del self.device_processes[device_id]
    
    def _stop_all_devices(self):
        """Stop all running devices."""
        for device_id in list(self.device_processes.keys()):
            self._stop_devices([device_id])
    
    def _disconnect_devices(self, device_ids: List[str]):
        """Disconnect devices by stopping their processes (simulates network partition)."""
        print(f"   🔌 Disconnecting {len(device_ids)} devices...")
        self._stop_devices(device_ids)
        print(f"   ✅ {len(device_ids)} devices disconnected")
    
    def _reconnect_devices(self, device_ids: List[str]):
        """Reconnect devices by restarting their processes."""
        print(f"   🔌 Reconnecting {len(device_ids)} devices...")
        new_processes = self._start_devices(device_ids)
        self.device_processes.update(new_processes)
        print(f"   ✅ {len(device_ids)} devices reconnected")
    
    def _get_queue_size(self, device_id: str) -> int:
        """Get current queue size for a device."""
        queue_path = QUEUE_DIR / f"{device_id}_queue.db"
        if not queue_path.exists():
            return 0
        
        try:
            conn = sqlite3.connect(str(queue_path))
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM messages')
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception as e:
            print(f"   ⚠️  Error reading queue for {device_id}: {e}")
            return 0
    
    def _get_all_queue_sizes(self, device_ids: List[str]) -> Dict[str, int]:
        """Get queue sizes for multiple devices."""
        return {device_id: self._get_queue_size(device_id) for device_id in device_ids}
    
    def _monitor_queue_growth(self, device_ids: List[str], duration_seconds: float, 
                              interval: float = 1.0) -> Dict:
        """Monitor queue growth during disconnection."""
        start_time = time.time()
        end_time = start_time + duration_seconds
        
        queue_history = {"times": [], "sizes": []}
        initial_sizes = self._get_all_queue_sizes(device_ids)
        max_queue = max(initial_sizes.values()) if initial_sizes else 0
        
        last_log_time = start_time
        
        while time.time() < end_time and self.running:
            current_time = time.time()
            elapsed = current_time - start_time
            
            # Check queue sizes
            current_sizes = self._get_all_queue_sizes(device_ids)
            total_queue = sum(current_sizes.values())
            current_max = max(current_sizes.values()) if current_sizes else 0
            
            # Record data point
            queue_history["times"].append(elapsed)
            queue_history["sizes"].append(total_queue)
            
            # Update max
            if current_max > max_queue:
                max_queue = current_max
            
            # Log progress every 10 seconds
            if current_time - last_log_time >= 10:
                print(f"   ⏱️  {elapsed:.1f}s: Total queued={total_queue}, Max per device={current_max}")
                last_log_time = current_time
            
            time.sleep(interval)
        
        # Final check
        final_sizes = self._get_all_queue_sizes(device_ids)
        total_final = sum(final_sizes.values())
        
        return {
            "initial_sizes": initial_sizes,
            "final_sizes": final_sizes,
            "max_queue_per_device": max_queue,
            "total_queued": total_final,
            "queue_history": queue_history,
            "queue_growth_rate": total_final / duration_seconds if duration_seconds > 0 else 0
        }
    
    def _measure_flush_time(self, device_ids: List[str], timeout: float = 300.0) -> Dict:
        """Measure time to flush queues after reconnection."""
        print(f"   ⏱️  Measuring flush time for {len(device_ids)} devices...")
        
        # Get initial queue sizes
        initial_sizes = self._get_all_queue_sizes(device_ids)
        total_initial = sum(initial_sizes.values())
        
        if total_initial == 0:
            return {
                "flush_time_seconds": 0.0,
                "all_messages_flushed": True,
                "final_queue_size": 0,
                "messages_flushed": 0,
                "flush_rate_msg_per_sec": 0.0
            }
        
        # Reconnect devices
        self._reconnect_devices(device_ids)
        
        # Wait a moment for reconnection
        time.sleep(2)
        
        # Monitor queue flush
        start_time = time.time()
        check_interval = 0.5  # Check every 0.5 seconds
        last_size = total_initial
        
        while time.time() - start_time < timeout:
            current_sizes = self._get_all_queue_sizes(device_ids)
            current_total = sum(current_sizes.values())
            
            # Check if queues are empty
            if current_total == 0:
                flush_time = time.time() - start_time
                messages_flushed = total_initial
                flush_rate = messages_flushed / flush_time if flush_time > 0 else 0
                
                print(f"   ✅ All queues flushed in {flush_time:.2f}s ({flush_rate:.1f} msg/s)")
                return {
                    "flush_time_seconds": flush_time,
                    "all_messages_flushed": True,
                    "final_queue_size": 0,
                    "messages_flushed": messages_flushed,
                    "flush_rate_msg_per_sec": flush_rate
                }
            
            # Check if queue is still decreasing (flush in progress)
            if current_total < last_size:
                # Still flushing, continue monitoring
                last_size = current_total
                elapsed = time.time() - start_time
                if elapsed % 5 < check_interval:  # Log every ~5 seconds
                    print(f"   ⏳ {elapsed:.1f}s: {current_total} messages remaining...")
            elif current_total == last_size and time.time() - start_time > 10:
                # Queue not decreasing for 10+ seconds - might be stuck
                print(f"   ⚠️  Queue flush appears stuck at {current_total} messages")
            
            time.sleep(check_interval)
        
        # Timeout
        final_sizes = self._get_all_queue_sizes(device_ids)
        final_total = sum(final_sizes.values())
        messages_flushed = total_initial - final_total
        flush_time = timeout
        
        print(f"   ⚠️  Flush timeout after {timeout:.1f}s")
        print(f"   📊 Flushed: {messages_flushed}/{total_initial} messages ({final_total} remaining)")
        
        return {
            "flush_time_seconds": flush_time,
            "all_messages_flushed": False,
            "final_queue_size": final_total,
            "messages_flushed": messages_flushed,
            "flush_rate_msg_per_sec": messages_flushed / flush_time if flush_time > 0 else 0
        }
    
    def _check_queue_limits(self, device_ids: List[str]) -> Dict:
        """Check if any devices hit queue limits."""
        devices_at_limit = []
        max_queue_reached = 0
        
        for device_id in device_ids:
            queue_size = self._get_queue_size(device_id)
            if queue_size >= MAX_QUEUE_SIZE:
                devices_at_limit.append(device_id)
                max_queue_reached = max(max_queue_reached, queue_size)
        
        return {
            "devices_at_queue_limit": len(devices_at_limit),
            "device_ids_at_limit": devices_at_limit,
            "max_queue_size_reached": max_queue_reached
        }
    
    def run_test_scenario(self, scenario: Dict) -> Dict:
        """Run a single test scenario."""
        scenario_name = scenario["name"]
        disconnect_percent = scenario["disconnect_percent"]
        duration_minutes = scenario["duration_minutes"]
        duration_seconds = duration_minutes * 60
        
        print("\n" + "=" * 70)
        print(f"🧪 TEST SCENARIO: {scenario_name}")
        print("=" * 70)
        print(f"   Disconnect: {disconnect_percent}% of devices")
        print(f"   Duration: {duration_minutes} minutes ({duration_seconds} seconds)")
        print()
        
        # Calculate devices to disconnect
        num_to_disconnect = int(self.num_devices * disconnect_percent / 100)
        devices_to_disconnect = self.device_ids[:num_to_disconnect]
        devices_remaining = self.device_ids[num_to_disconnect:]
        
        print(f"   📊 Devices: {num_to_disconnect} disconnected, {len(devices_remaining)} remaining")
        
        # Get initial queue sizes
        initial_queue_sizes = self._get_all_queue_sizes(devices_to_disconnect)
        initial_total = sum(initial_queue_sizes.values())
        
        # Disconnect devices
        disconnect_start = time.time()
        self._disconnect_devices(devices_to_disconnect)
        
        # Monitor queue growth during disconnection
        print(f"\n   📈 Monitoring queue growth for {duration_minutes} minutes...")
        queue_monitoring = self._monitor_queue_growth(devices_to_disconnect, duration_seconds)
        
        disconnect_end = time.time()
        actual_disconnect_duration = disconnect_end - disconnect_start
        
        # Check queue limits
        queue_limit_check = self._check_queue_limits(devices_to_disconnect)
        
        # Get final queue sizes before reconnection
        final_queue_sizes_before = self._get_all_queue_sizes(devices_to_disconnect)
        final_total_before = sum(final_queue_sizes_before.values())
        messages_queued = final_total_before - initial_total
        
        print(f"\n   📊 Queue Statistics:")
        print(f"      Initial: {initial_total} messages")
        print(f"      Final: {final_total_before} messages")
        print(f"      Queued during disconnect: {messages_queued}")
        print(f"      Max per device: {queue_monitoring['max_queue_per_device']}")
        print(f"      Growth rate: {queue_monitoring['queue_growth_rate']:.2f} msg/s")
        
        if queue_limit_check["devices_at_queue_limit"] > 0:
            print(f"      ⚠️  {queue_limit_check['devices_at_queue_limit']} devices at queue limit!")
        
        # Measure flush time
        print(f"\n   🔄 Reconnecting and measuring flush time...")
        flush_results = self._measure_flush_time(devices_to_disconnect, timeout=300.0)
        
        # Final queue check
        final_queue_sizes_after = self._get_all_queue_sizes(devices_to_disconnect)
        final_total_after = sum(final_queue_sizes_after.values())
        
        # Compile results
        result = {
            "scenario": scenario_name,
            "timestamp": datetime.now().isoformat(),
            "device_count": self.num_devices,
            "disconnect_percent": disconnect_percent,
            "disconnect_duration_seconds": actual_disconnect_duration,
            "disconnect_duration_minutes": actual_disconnect_duration / 60,
            "devices_disconnected": num_to_disconnect,
            "devices_remaining": len(devices_remaining),
            
            # Queue statistics
            "initial_queue_size": initial_total,
            "final_queue_size_before_reconnect": final_total_before,
            "messages_queued_during_disconnect": messages_queued,
            "max_queue_per_device": queue_monitoring["max_queue_per_device"],
            "queue_growth_rate_msg_per_sec": queue_monitoring["queue_growth_rate"],
            
            # Flush results
            "flush_time_seconds": flush_results["flush_time_seconds"],
            "all_messages_flushed": flush_results["all_messages_flushed"],
            "final_queue_size_after_flush": final_total_after,
            "messages_flushed": flush_results["messages_flushed"],
            "flush_rate_msg_per_sec": flush_results["flush_rate_msg_per_sec"],
            
            # Queue limits
            "devices_at_queue_limit": queue_limit_check["devices_at_queue_limit"],
            "device_ids_at_limit": queue_limit_check["device_ids_at_limit"],
            "max_queue_size_reached": queue_limit_check["max_queue_size_reached"],
            
            # Queue history
            "queue_history": queue_monitoring["queue_history"],
            
            # Target compliance
            "flush_time_target_met": flush_results["flush_time_seconds"] < 30.0,
            "target_seconds": 30.0
        }
        
        # Print summary
        print(f"\n   📋 Test Summary:")
        print(f"      Messages queued: {messages_queued}")
        print(f"      Flush time: {flush_results['flush_time_seconds']:.2f}s")
        print(f"      Target (<30s): {'✅ MET' if result['flush_time_target_met'] else '❌ MISSED'}")
        print(f"      All flushed: {'✅ YES' if flush_results['all_messages_flushed'] else '❌ NO'}")
        
        return result
    
    def run_all_tests(self, scenarios: Optional[List[Dict]] = None):
        """Run all test scenarios."""
        if scenarios is None:
            scenarios = TEST_SCENARIOS
        
        print("=" * 70)
        print("🌐 NETWORK FAILURE TESTING SUITE")
        print("=" * 70)
        print(f"Number of Devices: {self.num_devices}")
        print(f"Test Scenarios: {len(scenarios)}")
        print(f"Results Directory: {RESULTS_DIR}")
        print()
        
        # Start all devices
        print("🚀 Starting all devices...")
        self.device_processes = self._start_devices()
        print(f"✅ {len(self.device_processes)} devices started")
        
        # Wait for devices to stabilize
        print("\n⏳ Waiting for devices to stabilize (10 seconds)...")
        time.sleep(10)
        
        # Run each test scenario
        for i, scenario in enumerate(scenarios, 1):
            if not self.running:
                break
            
            print(f"\n{'='*70}")
            print(f"Test {i}/{len(scenarios)}")
            print(f"{'='*70}")
            
            try:
                result = self.run_test_scenario(scenario)
                self.test_results.append(result)
                
                # Save results after each test
                self._save_results()
                
                # Wait between tests
                if i < len(scenarios):
                    print(f"\n⏳ Waiting 30 seconds before next test...")
                    time.sleep(30)
            
            except KeyboardInterrupt:
                print("\n\n🛑 Test interrupted by user")
                break
            except Exception as e:
                print(f"\n❌ Error running test: {e}")
                import traceback
                traceback.print_exc()
        
        # Cleanup
        print("\n🧹 Cleaning up...")
        self._stop_all_devices()
        
        # Final summary
        self._print_summary()
    
    def _save_results(self):
        """Save test results to JSON file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = RESULTS_DIR / f"network_failure_test_{timestamp}.json"
        
        with open(filename, 'w') as f:
            json.dump(self.test_results, f, indent=2)
        
        print(f"\n💾 Results saved to: {filename}")
    
    def _print_summary(self):
        """Print test summary."""
        if not self.test_results:
            return
        
        print("\n" + "=" * 70)
        print("📊 TEST SUMMARY")
        print("=" * 70)
        
        for result in self.test_results:
            scenario = result["scenario"]
            messages = result["messages_queued_during_disconnect"]
            flush_time = result["flush_time_seconds"]
            target_met = result["flush_time_target_met"]
            all_flushed = result["all_messages_flushed"]
            
            status = "✅" if (target_met and all_flushed) else "⚠️" if all_flushed else "❌"
            
            print(f"\n{status} {scenario}:")
            print(f"   Messages: {messages}")
            print(f"   Flush time: {flush_time:.2f}s (target: <30s)")
            print(f"   Target met: {'✅' if target_met else '❌'}")
            print(f"   All flushed: {'✅' if all_flushed else '❌'}")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Network Failure Testing Suite")
    parser.add_argument(
        "--devices", 
        type=int, 
        default=50,
        help="Number of devices to simulate (default: 50)"
    )
    parser.add_argument(
        "--scenario",
        type=str,
        help="Run specific scenario by name (e.g., '20% Devices - 5 minutes')"
    )
    parser.add_argument(
        "--custom",
        action="store_true",
        help="Run custom scenario (interactive)"
    )
    
    args = parser.parse_args()
    
    tester = NetworkFailureTester(num_devices=args.devices)
    
    if args.scenario:
        # Run specific scenario
        scenario = next((s for s in TEST_SCENARIOS if s["name"] == args.scenario), None)
        if scenario:
            tester.run_all_tests([scenario])
        else:
            print(f"❌ Scenario '{args.scenario}' not found")
            print(f"Available scenarios: {[s['name'] for s in TEST_SCENARIOS]}")
    elif args.custom:
        # Interactive custom scenario
        print("Custom Test Scenario")
        print("=" * 70)
        disconnect_percent = int(input("Disconnect percentage (20-50): "))
        duration_minutes = int(input("Duration in minutes (5-30): "))
        
        scenario = {
            "name": f"Custom - {disconnect_percent}% Devices - {duration_minutes} minutes",
            "disconnect_percent": disconnect_percent,
            "duration_minutes": duration_minutes
        }
        tester.run_all_tests([scenario])
    else:
        # Run all scenarios
        tester.run_all_tests()


if __name__ == "__main__":
    main()
