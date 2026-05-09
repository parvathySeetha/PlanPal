import json

with open("schema_metadata.json", "r") as f:
    data = json.load(f)

for obj in data:
    if obj.get("object") == "Custom_Invoice_Line_Item__c":
        fields = obj.get("fields", [])
        
        # Add Invoice__r.Order__c
        if not any(f.get("apiname") == "Invoice__r.Order__c" for f in fields):
            fields.append({
                "apiname": "Invoice__r.Order__c",
                "datatype": "Lookup (Order)",
                "FieldLabel": "Invoice Order"
            })
            print("Added Invoice__r.Order__c to Custom_Invoice_Line_Item__c")

        obj["fields"] = fields
        break

with open("schema_metadata.json", "w") as f:
    json.dump(data, f, indent=2)
