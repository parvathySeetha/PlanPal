from agents.Reconciliation.graph import sf_client
from core.helper import ensure_sf_connected
import json

ensure_sf_connected(sf_client)
res = sf_client.sf.query("SELECT Id, Name, Price__c, Line_Amount__c, Billed_Impressions__c, eCPM__c FROM Custom_Invoice_Line_Item__c WHERE Id = 'a0Gg8000001csLNEAY'")
print(json.dumps(res, indent=2))
