import json

def get_query_fields(object_name: str) -> str:
    with open("schema_metadata.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    obj_meta = next((item for item in data if item["object"].lower() == object_name.lower()), {})
    fields = obj_meta.get("fields", [])
    apiname_list = [f.get("apiname") for f in fields if f.get("apiname")]
    return ", ".join(apiname_list)

print("OrderItem:", get_query_fields("OrderItem"))
print("Custom_Invoice_Line_Item__c:", get_query_fields("Custom_Invoice_Line_Item__c"))
print("Delivery_Data__c:", get_query_fields("Delivery_Data__c"))
