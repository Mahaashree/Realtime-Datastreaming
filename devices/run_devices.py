"""
Script to run 10 device simulators concurrently.
Each device runs in a separate process to simulate independent devices.
"""

import subprocess
import sys
import os
import time
import signal
from dotenv import load_dotenv

load_dotenv()

# Get broker configuration from environment
broker_host = os.getenv("MQTT_BROKER_HOST", "localhost")
broker_port = os.getenv("MQTT_BROKER_PORT", "1883")

# List of processes
processes = []


def signal_handler(sig, frame):
    """Handle Ctrl+C to gracefully stop all devices."""
    print("\nStopping all devices...")
    for process in processes:
        try:
            process.terminate()
        except:
            pass
    
    # Wait for processes to terminate
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    
    print("All devices stopped.")
    sys.exit(0)


def main():
    """Start 10 device simulators."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print(f"Starting 10 device simulators...")
    print(f"MQTT Broker: {broker_host}:{broker_port}")
    print("Press Ctrl+C to stop all devices\n")
    
    # Get the path to device_simulator.py
    script_dir = os.path.dirname(os.path.abspath(__file__))
    simulator_script = os.path.join(script_dir, "device_simulator.py")
    
    # Start 10 device processes
    for i in range(1, 11):
        device_id = f"vehicle_{i:02d}"
        print(f"Starting device: {device_id}")
        
        process = subprocess.Popen(
            [sys.executable, simulator_script, device_id, broker_host, str(broker_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        processes.append(process)
        time.sleep(0.5)  # Small delay between starting devices
    
    print(f"\nAll 10 devices started. Monitoring...")
    print("=" * 60)
    
    # Monitor processes
    try:
        while True:
            # Check if any process has died
            for i, process in enumerate(processes):
                if process.poll() is not None:
                    device_id = f"vehicle_{i+1:02d}"
                    print(f"WARNING: Device {device_id} has stopped (exit code: {process.returncode})")
                    # Restart the device
                    print(f"Restarting device {device_id}...")
                    new_process = subprocess.Popen(
                        [sys.executable, simulator_script, device_id, broker_host, str(broker_port)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    processes[i] = new_process
            
            time.sleep(5)  # Check every 5 seconds
    
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == "__main__":
    main()

