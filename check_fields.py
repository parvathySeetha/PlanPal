import json

with open("schema_metadata.json", "r", encoding="utf-8") as f:
    data = json.load(f)

def get_fields(obj_name):
    obj_meta = next((item for item in data if item["object"].lower() == obj_name.lower()), {})
    return set([f.get("apiname") for f in obj_meta.get("fields", []) if f.get("apiname")])

oi_fields = get_fields("OrderItem")
cili_fields = get_fields("Custom_Invoice_Line_Item__c")
dd_fields = get_fields("Delivery_Data__c")

oi_needed = {"Product2Id", "OrderItemNumber", "QuoteLineItemId", "QuoteLineItem.LineNumber", "Rate__c", "UnitPrice", "Pricing_Model__c", "Product2.Name", "Id"}
cili_needed = {"Id", "Name", "Product__c", "Billed_Impressions__c", "Price__c", "Line_Amount__c", "Pricing_Model__c", "eCPM__c", "Start_Date__c", "End_Date__c", "Invoice__c", "Invoice__r.Name", "Invoice__r.Invoice_Date__c", "Invoice__r.Total_Amount__c", "Invoice__r.Total_Charges__c", "Invoice__r.Status__c", "Invoice__r.Start_Date__c", "Invoice__r.End_Date__c", "Invoice__r.Billing_Account__c", "Invoice__r.Billing_Account__r.Name"}
dd_needed = {"Id", "Order_Product__c", "Date__c", "Gross__c", "IVT__c", "Viewability__c"}

print("Missing in OrderItem:", oi_needed - oi_fields)
print("Missing in Custom_Invoice_Line_Item__c:", cili_needed - cili_fields)
print("Missing in Delivery_Data__c:", dd_needed - dd_fields)
