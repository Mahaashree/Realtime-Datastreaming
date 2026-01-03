#!/usr/bin/env python3
"""
Analyze network failure test results and generate summary reports.
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict

RESULTS_DIR = Path(__file__).parent / "results"


def load_latest_result() -> List[Dict]:
    """Load the most recent test result file."""
    result_files = sorted(RESULTS_DIR.glob("network_failure_test_*.json"), reverse=True)
    
    if not result_files:
        print("❌ No test results found in testing/results/")
        sys.exit(1)
    
    latest_file = result_files[0]
    print(f"📂 Loading: {latest_file.name}")
    
    with open(latest_file, 'r') as f:
        return json.load(f)


def load_all_results() -> List[Dict]:
    """Load all test result files."""
    result_files = sorted(RESULTS_DIR.glob("network_failure_test_*.json"), reverse=True)
    
    if not result_files:
        print("❌ No test results found in testing/results/")
        sys.exit(1)
    
    all_results = []
    for result_file in result_files:
        with open(result_file, 'r') as f:
            all_results.extend(json.load(f))
    
    return all_results


def analyze_results(results: List[Dict]):
    """Analyze and print summary of test results."""
    print("=" * 70)
    print("📊 NETWORK FAILURE TEST RESULTS ANALYSIS")
    print("=" * 70)
    print()
    
    for i, result in enumerate(results, 1):
        scenario = result["scenario"]
        disconnect_percent = result["disconnect_percent"]
        duration_minutes = result["disconnect_duration_minutes"]
        messages_queued = result["messages_queued_during_disconnect"]
        flush_time = result["flush_time_seconds"]
        target_met = result["flush_time_target_met"]
        all_flushed = result["all_messages_flushed"]
        devices_at_limit = result["devices_at_queue_limit"]
        flush_rate = result["flush_rate_msg_per_sec"]
        
        # Status indicators
        status = "✅" if (target_met and all_flushed and devices_at_limit == 0) else "⚠️" if all_flushed else "❌"
        
        print(f"{status} Test {i}: {scenario}")
        print(f"   Disconnect: {disconnect_percent}% for {duration_minutes:.1f} minutes")
        print(f"   Messages Queued: {messages_queued}")
        print(f"   Flush Time: {flush_time:.2f}s (target: <30s)")
        print(f"   Flush Rate: {flush_rate:.1f} msg/s")
        print(f"   Target Met: {'✅ YES' if target_met else '❌ NO'}")
        print(f"   All Flushed: {'✅ YES' if all_flushed else '❌ NO'}")
        
        if devices_at_limit > 0:
            print(f"   ⚠️  {devices_at_limit} devices hit queue limit (10,000 messages)")
        
        # Performance assessment
        if messages_queued < 200:
            category = "Small backlog"
            expected_time = "< 30s"
        elif messages_queued < 500:
            category = "Medium backlog"
            expected_time = "< 60s"
        elif messages_queued < 1000:
            category = "Large backlog"
            expected_time = "< 120s"
        else:
            category = "Very large backlog"
            expected_time = "< 180s"
        
        print(f"   Category: {category} (expected: {expected_time})")
        
        if flush_time > 180:
            print(f"   ⚠️  WARNING: Flush time exceeds reasonable limit")
        
        print()
    
    # Overall statistics
    print("=" * 70)
    print("📈 OVERALL STATISTICS")
    print("=" * 70)
    
    total_tests = len(results)
    target_met_count = sum(1 for r in results if r["flush_time_target_met"])
    all_flushed_count = sum(1 for r in results if r["all_messages_flushed"])
    devices_at_limit_count = sum(1 for r in results if r["devices_at_queue_limit"] > 0)
    
    avg_flush_time = sum(r["flush_time_seconds"] for r in results) / total_tests
    avg_messages = sum(r["messages_queued_during_disconnect"] for r in results) / total_tests
    avg_flush_rate = sum(r["flush_rate_msg_per_sec"] for r in results) / total_tests
    
    print(f"Total Tests: {total_tests}")
    print(f"Target Met: {target_met_count}/{total_tests} ({target_met_count/total_tests*100:.1f}%)")
    print(f"All Flushed: {all_flushed_count}/{total_tests} ({all_flushed_count/total_tests*100:.1f}%)")
    print(f"Devices at Limit: {devices_at_limit_count} tests")
    print()
    print(f"Average Flush Time: {avg_flush_time:.2f}s")
    print(f"Average Messages Queued: {avg_messages:.0f}")
    print(f"Average Flush Rate: {avg_flush_rate:.1f} msg/s")
    print()
    
    # Recommendations
    print("=" * 70)
    print("💡 RECOMMENDATIONS")
    print("=" * 70)
    
    if target_met_count < total_tests * 0.8:
        print("⚠️  Flush time targets not consistently met:")
        print("   - Consider optimizing queue flush mechanism")
        print("   - Check MQTT broker throughput")
        print("   - Review network latency")
    
    if devices_at_limit_count > 0:
        print("⚠️  Some devices hit queue limit:")
        print("   - Consider increasing max_queue_size for extended offline scenarios")
        print("   - Implement message expiration/dropping strategy")
        print("   - Monitor devices offline for >30 minutes")
    
    if avg_flush_rate < 10:
        print("⚠️  Low flush rate detected:")
        print("   - Check MQTT broker performance")
        print("   - Verify network connectivity")
        print("   - Consider batch publishing optimization")
    
    if all_flushed_count == total_tests:
        print("✅ All messages successfully flushed - system is reliable!")
    
    print()


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Analyze network failure test results")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Analyze all test results (not just latest)"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Analyze specific result file"
    )
    
    args = parser.parse_args()
    
    if args.file:
        # Load specific file
        file_path = RESULTS_DIR / args.file
        if not file_path.exists():
            print(f"❌ File not found: {file_path}")
            sys.exit(1)
        
        with open(file_path, 'r') as f:
            results = json.load(f)
    elif args.all:
        # Load all results
        results = load_all_results()
    else:
        # Load latest
        results = load_latest_result()
    
    analyze_results(results)


if __name__ == "__main__":
    main()

