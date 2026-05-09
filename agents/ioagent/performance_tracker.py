import time
import json
import logging
from datetime import datetime
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "performance_metrics.jsonl"
READABLE_LOG_FILE = BASE_DIR / "performance_metrics_readable.txt"

logger = logging.getLogger(__name__)

class PerformanceMonitor:
    # A class-level dictionary to keep track of the *last* event's timestamp per identifier.
    # This helps calculate the time difference in real-time.
    _last_event_timestamps = {}

    @staticmethod
    def log_event(identifier: str, event_name: str, details: dict = None):
        """
        Logs a performance event with a timestamp to a JSONL file,
        calculates the exact duration since the previous step automatically,
        and appends a human-readable entry to a text file.
        :param identifier: Could be case_id, order_id, or session_id to group events.
        :param event_name: The name of the event being tracked.
        :param details: Additional optional metadata to log.
        """
        try:
            current_time_unix = time.time()
            identifier_key = identifier or "unknown"
            
            # Compute real-time duration elapsed since the last event
            duration = 0.0
            is_first_event = False
            if identifier_key in PerformanceMonitor._last_event_timestamps:
                last_time = PerformanceMonitor._last_event_timestamps[identifier_key]
                duration = current_time_unix - last_time
            else:
                is_first_event = True
                
            # Update the latest time tracking
            PerformanceMonitor._last_event_timestamps[identifier_key] = current_time_unix
            
            duration_rounded = round(duration, 3)

            record = {
                "identifier": identifier_key,
                "event_name": event_name,
                "duration_seconds": duration_rounded,
                "timestamp_iso": datetime.now().isoformat(),
                "timestamp_unix": current_time_unix,
                "details": details or {}
            }
            
            # Write to JSONL
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")
                
            # Automatically write to readable text file
            with open(READABLE_LOG_FILE, "a") as f:
                if is_first_event:
                    f.write(f"\n▶ RUN ID: {identifier_key}\n")
                    f.write(f"Started at: {record['timestamp_iso']}\n")
                f.write(f"   [{duration_rounded:>6}s] {event_name}\n")

            logger.info(f"⏱️ Performance Tracked: {event_name} for ID {identifier}")
        except Exception as e:
            logger.error(f"Failed to log performance track: {e}")

    @staticmethod
    def get_time_difference(identifier: str, start_event: str, end_event: str):
        """
        Helper method to calculate the time difference between two events.
        """
        if not LOG_FILE.exists():
            return None
        
        start_time = None
        end_time = None
        
        try:
            with open(LOG_FILE, "r") as f:
                for line in f:
                    data = json.loads(line)
                    if data.get("identifier") == identifier:
                        if data.get("event_name") == start_event:
                            start_time = data.get("timestamp_unix")
                        elif data.get("event_name") == end_event:
                            end_time = data.get("timestamp_unix")
                            
            if start_time and end_time:
                return end_time - start_time
        except Exception as e:
            logger.error(f"Failed to calculate time difference: {e}")
            
        return None
