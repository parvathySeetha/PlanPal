import json
import asyncio
from mcp_module.Salesforcemcp.client.sf_client import SalesforceClient

def get_query_fields(object_name: str) -> str:
    with open("schema_metadata.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    obj_meta = next((item for item in data if item["object"].lower() == object_name.lower()), {})
    fields = obj_meta.get("fields", [])
    return [f.get("apiname") for f in fields if f.get("apiname")]

client = SalesforceClient("marketing")
client.connect()

fields = get_query_fields("OrderItem")
print(f"Total fields: {len(fields)}")

valid_fields = []
invalid_fields = []

for f in fields:
    query = f"SELECT {f} FROM OrderItem LIMIT 1"
    try:
        res = client.sf.query(query)
        valid_fields.append(f)
    except Exception as e:
        invalid_fields.append(f)

print(f"Valid fields ({len(valid_fields)}): {valid_fields}")
print(f"Invalid fields ({len(invalid_fields)}): {invalid_fields}")
