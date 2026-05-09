import json
from pathlib import Path

def process_metrics():
    log_file = Path('/Users/hrithikmanojnair/Documents/Hrithik/AI/PacePal-Agent-updated/agents/ioagent/performance_metrics.jsonl')
    readable_file = Path('/Users/hrithikmanojnair/Documents/Hrithik/AI/PacePal-Agent-updated/agents/ioagent/performance_metrics_readable.txt')

    if not log_file.exists():
        print("Log file not found.")
        return

    with open(log_file, 'r') as f:
        lines = f.readlines()

    # Group the logs by continuous runs
    runs = []
    current_run = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
            
        if 'next-set' in data:
            if current_run:
                runs.append(current_run)
                current_run = []
            runs.append([data]) # keep the delimiter
        elif 'identifier' in data:
            current_run.append(data)

    if current_run:
        runs.append(current_run)

    # Calculate exact duration for each step and prepare the outputs
    updated_jsonl_lines = []
    text_lines = []
    
    text_lines.append("=== READABLE PERFORMANCE METRICS ===")
    text_lines.append("This file shows the exact seconds each step took.\n")

    for run in runs:
        # If it's a delimiter
        if len(run) == 1 and 'next-set' in run[0]:
            updated_jsonl_lines.append(json.dumps(run[0]))
            text_lines.append("\n--------------------------------------------------\n")
            continue
            
        # If it's a normal run of events
        if len(run) > 0 and 'identifier' in run[0]:
            identifier = run[0]['identifier']
            text_lines.append(f"▶ RUN ID: {identifier}")
            text_lines.append(f"Started at: {run[0].get('timestamp_iso')}\n")
            
            for i in range(len(run)):
                event = run[i]
                start_time = float(event.get('timestamp_unix', 0))
                
                # Calculate time taken until the next event
                if i < len(run) - 1:
                    end_time = float(run[i+1].get('timestamp_unix', 0))
                    duration = end_time - start_time
                else:
                    duration = 0.0 # Time for the last step is unknown
                
                duration = round(duration, 3)
                
                # 1. Update the JSON data to include exact seconds
                # We place the event_name and duration at the beginning of the dictionary so it's readable
                new_event = {
                    "identifier": event.get("identifier"),
                    "event_name": event.get("event_name"),
                    "duration_seconds": duration,
                    "timestamp_iso": event.get("timestamp_iso"),
                    "timestamp_unix": event.get("timestamp_unix"),
                    "details": event.get("details", {})
                }
                updated_jsonl_lines.append(json.dumps(new_event))
                
                # 2. Append formatted string to readable text file
                text_lines.append(f"   [{duration:>6}s] {event.get('event_name')}")
            
            text_lines.append("\n")

    # 1. Replace the jsonl with the updated JSON (with duration_seconds included)
    # This keeps the system from breaking while providing the exact time logs inside the JSON too.
    with open(log_file, 'w') as f:
        f.write('\n'.join(updated_jsonl_lines) + '\n')

    # 2. Write the highly readable text file alongside it
    with open(readable_file, 'w') as f:
        f.write('\n'.join(text_lines))
        
    print(f"✅ Successfully updated {log_file.name} to contain 'duration_seconds' directly.")
    print(f"✅ Created highly readable text format in {readable_file.name}.")

if __name__ == "__main__":
    process_metrics()
