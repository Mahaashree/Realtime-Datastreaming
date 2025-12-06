"""
Script to run 10 device simulators concurrently.
Each device runs in a separate process to simulate independent devices.
"""

import subprocess
import sys
import os
import time
import signal
import threading
from dotenv import load_dotenv

load_dotenv()

# Get broker configuration from environment
broker_host = os.getenv("MQTT_BROKER_HOST", "localhost")
broker_port = os.getenv("MQTT_BROKER_PORT", "1883")

# Configuration
SHOW_LOGS = os.getenv("SHOW_DEVICE_LOGS", "true").lower() == "true"  # Set to "false" to hide logs
LOG_PREFIX = os.getenv("LOG_PREFIX", "true").lower() == "true"  # Show device ID prefix in logs

# List of processes and their info
processes = []
process_info = {}  # device_id -> {"process": process, "start_time": time, "restart_count": 0}


def stream_output(process, device_id, stream_type):
    """Stream output from a process to console with device ID prefix."""
    stream = process.stdout if stream_type == "stdout" else process.stderr
    if stream:
        for line in iter(stream.readline, b''):
            if line:
                line_str = line.decode('utf-8', errors='replace').rstrip()
                if LOG_PREFIX:
                    prefix = f"[{device_id}]"
                    print(f"{prefix:15} {line_str}")
                else:
                    print(line_str)


def signal_handler(sig, frame):
    """Handle Ctrl+C to gracefully stop all devices."""
    print("\n\nStopping all devices...")
    for device_id, info in process_info.items():
        process = info["process"]
        try:
            process.terminate()
            print(f"  Stopping {device_id}...")
        except:
            pass
    
    # Wait for processes to terminate
    for device_id, info in process_info.items():
        process = info["process"]
        try:
            process.wait(timeout=5)
            print(f"  ✓ {device_id} stopped")
        except subprocess.TimeoutExpired:
            process.kill()
            print(f"  ✗ {device_id} force killed")
    
    print("\nAll devices stopped.")
    sys.exit(0)


def start_device(device_id, restart_count=0):
    """Start a single device simulator."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    simulator_script = os.path.join(script_dir, "device_simulator.py")
    
    if SHOW_LOGS:
        # Show logs - don't capture output, let it go to console
        process = subprocess.Popen(
            [sys.executable, simulator_script, device_id, broker_host, str(broker_port)],
            stdout=sys.stdout if not LOG_PREFIX else subprocess.PIPE,
            stderr=sys.stderr if not LOG_PREFIX else subprocess.PIPE
        )
        
        # If using prefix, stream output with device ID
        if LOG_PREFIX:
            stdout_thread = threading.Thread(
                target=stream_output, 
                args=(process, device_id, "stdout"),
                daemon=True
            )
            stderr_thread = threading.Thread(
                target=stream_output,
                args=(process, device_id, "stderr"),
                daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()
    else:
        # Hide logs - capture output
        process = subprocess.Popen(
            [sys.executable, simulator_script, device_id, broker_host, str(broker_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    
    process_info[device_id] = {
        "process": process,
        "start_time": time.time(),
        "restart_count": restart_count
    }
    
    return process


def main():
    """Start 10 device simulators."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 70)
    print("Vehicle Device Simulator Manager")
    print("=" * 70)
    print(f"MQTT Broker: {broker_host}:{broker_port}")
    print(f"Logging: {'Enabled' if SHOW_LOGS else 'Disabled'}")
    print(f"Log Prefix: {'Enabled' if LOG_PREFIX else 'Disabled'}")
    print("Press Ctrl+C to stop all devices")
    print("=" * 70)
    print()
    
    # Start 10 device processes
    print("Starting 10 device simulators...")
    for i in range(1, 11):
        device_id = f"vehicle_{i:02d}"
        print(f"  [{i:2}/10] Starting {device_id}...", end=" ", flush=True)
        
        try:
            process = start_device(device_id)
            # Give it a moment to connect
            time.sleep(0.3)
            
            # Check if process is still running (didn't crash immediately)
            if process.poll() is None:
                print("✓ Started")
            else:
                print(f"✗ Failed (exit code: {process.returncode})")
        except Exception as e:
            print(f"✗ Error: {e}")
        
        time.sleep(0.2)  # Small delay between starting devices
    
    print()
    print("=" * 70)
    print("All devices started. Monitoring status...")
    print("=" * 70)
    print()
    
    # Monitor processes
    last_status_time = time.time()
    status_interval = 30  # Show status every 30 seconds
    
    try:
        while True:
            current_time = time.time()
            
            # Check if any process has died
            for device_id, info in list(process_info.items()):
                process = info["process"]
                if process.poll() is not None:
                    exit_code = process.returncode
                    restart_count = info["restart_count"] + 1
                    
                    print(f"\n⚠ WARNING: Device {device_id} has stopped (exit code: {exit_code})")
                    print(f"   Restarting... (attempt #{restart_count})")
                    
                    # Restart the device
                    try:
                        new_process = start_device(device_id, restart_count)
                        time.sleep(0.5)
                        if new_process.poll() is None:
                            print(f"   ✓ {device_id} restarted successfully")
                        else:
                            print(f"   ✗ {device_id} failed to restart")
                    except Exception as e:
                        print(f"   ✗ Error restarting {device_id}: {e}")
            
            # Periodic status update
            if current_time - last_status_time >= status_interval:
                running_count = sum(1 for info in process_info.values() if info["process"].poll() is None)
                print(f"\n📊 Status: {running_count}/10 devices running")
                last_status_time = current_time
            
            time.sleep(5)  # Check every 5 seconds
    
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == "__main__":
    main()

