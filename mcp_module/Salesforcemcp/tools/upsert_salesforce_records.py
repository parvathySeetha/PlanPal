from typing import List, Dict, Any, Optional
from Error.sf_error import SalesforceApiError
from client.sf_client import SalesforceClient
import logging
import json
import os

logger = logging.getLogger(__name__)

# Lazy initialization
_sf_client = None

def get_client():
    global _sf_client
    if not _sf_client:
        _sf_client = SalesforceClient("marketing")
        _sf_client.connect()
    return _sf_client

async def upsert_salesforce_records(
    object_name: str,
    records: List[Dict[str, Any]]
) -> str:
    """
    Batch create or update multiple Salesforce records in a single operation.
    
    This tool is optimized for bulk operations and should be used when you need to
    create or update multiple records of the same object type.
    
    Args:
        object_name: The Salesforce object API name (e.g., "CampaignMember", "Contact")
        records: List of record dictionaries, each containing:
            - record_id: (Optional) If provided, updates that record; if empty/None, creates new record
            - fields: Dictionary of field names and values to set
            
    Returns:
        JSON string with:
        - success: Overall operation success
        - total_records: Total number of records processed
        - successful: Number of successful operations
        - failed: Number of failed operations
        - results: List of individual results with operation type and record_id
        - errors: List of any errors encountered
    
    Example:
        records = [
            {"record_id": "003xxx", "fields": {"Status": "Sent"}},
            {"record_id": "", "fields": {"FirstName": "John", "LastName": "Doe"}}
        ]
    """
    
    client = get_client()
    sf = client.sf
    
    if not sf:
        return json.dumps({
            "success": False,
            "error": "Salesforce connection not established"
        }, indent=2)
    
    if not object_name:
        return json.dumps({
            "success": False,
            "error": "object_name must be a non-empty string"
        }, indent=2)
    
    if not records or not isinstance(records, list):
        return json.dumps({
            "success": False,
            "error": "records must be a non-empty list"
        }, indent=2)
    
    results = []
    errors = []
    successful_count = 0
    failed_count = 0
    
    # 🔍 DEBUG: Log what we're about to send to Salesforce
    logging.info(f"🔍 [upsert_salesforce_records] object_name: {object_name}")
    logging.info(f"🔍 [upsert_salesforce_records] records count: {len(records)}")
    if records:
        logging.info(f"🔍 [upsert_salesforce_records] First record: {json.dumps(records[0], indent=2)}")
    
    try:
        # --- SMART-SWITCH: REST VS BULK OPTIMIZATION ---
        is_bulk = len(records) > 2  # Only use Bulk for larger batches
        logger.info(f"🚀 Using {'Bulk' if is_bulk else 'REST'} API for {len(records)} records...")
        
        if is_bulk:
            bulk_api = getattr(sf.bulk, object_name)
            creates = []
            updates = []
            idx_map = [] 

            for idx, record in enumerate(records):
                record_id = record.get("record_id", "")
                fields = record.get("fields", {})
                
                if record_id and str(record_id).strip() != "":
                    update_fields = fields.copy()
                    update_fields["Id"] = record_id
                    updates.append(update_fields)
                    idx_map.append({"idx": idx, "op": "update", "id": record_id})
                else:
                    creates.append(fields)
                    idx_map.append({"idx": idx, "op": "create"})

            # Execute Bulk
            if creates:
                create_results = bulk_api.insert(creates)
                for i, res in enumerate(create_results):
                    map_entry = [m for m in idx_map if m["op"] == "create"][i]
                    if res.get("success"):
                        results.append({"index": map_entry["idx"], "success": True, "record_id": res.get("id")})
                        successful_count += 1
                    else:
                        errors.append({"index": map_entry["idx"], "error": res.get("errors")})
                        failed_count += 1
            if updates:
                update_results = bulk_api.update(updates)
                for i, res in enumerate(update_results):
                    map_entry = [m for m in idx_map if m["op"] == "update"][i]
                    if res.get("success"):
                        results.append({"index": map_entry["idx"], "success": True, "record_id": map_entry["id"]})
                        successful_count += 1
                    else:
                        errors.append({"index": map_entry["idx"], "error": res.get("errors")})
                        failed_count += 1
        else:
            # FAST REST PATH for N=1 or N=2
            sobject_api = getattr(sf, object_name)
            for idx, record in enumerate(records):
                record_id = record.get("record_id")
                fields = record.get("fields", {})
                try:
                    if record_id:
                        sobject_api.update(record_id, fields)
                        results.append({"index": idx, "success": True, "record_id": record_id})
                    else:
                        res = sobject_api.create(fields)
                        results.append({"index": idx, "success": True, "record_id": res.get("id")})
                    successful_count += 1
                except Exception as e:
                    errors.append({"index": idx, "error": str(e)})
                    failed_count += 1

        result_json = {
            "success": failed_count == 0,
            "total_records": len(records),
            "successful": successful_count,
            "failed": failed_count,
            "results": sorted(results, key=lambda x: x["index"]),
            "errors": errors if errors else None
        }
        return json.dumps(result_json, indent=2)
        
    except Exception as e:
        logging.exception("Batch upsert failed")
        return json.dumps({
            "success": False,
            "error": f"Failed to access Salesforce object '{object_name}': {str(e)}"
        }, indent=2)
