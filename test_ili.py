from agents.Reconciliation.graph import sf_client
from core.helper import ensure_sf_connected

ensure_sf_connected(sf_client)
res = sf_client.sf.query("SELECT Id, Start_Date__c, End_Date__c FROM Custom_Invoice_Line_Item__c WHERE Id = 'a0Gg8000001csLNEAY'")
print(res)
