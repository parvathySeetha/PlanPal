import sys
import logging
from config import get_salesforce_config
from mcp_module.Salesforcemcp.client.sf_client import SalesforceClient

logging.basicConfig(level=logging.INFO)

print("Getting config for demo...")
config = get_salesforce_config("demo")
print(f"Username from config: {config.get('SALESFORCE_USERNAME')}")

sf_client = SalesforceClient("demo")
sf_client.connect()
print(f"Connected to session: {sf_client.sf.session_id[:10]}...")

query = "SELECT Id, Pricebook2Id FROM Quote WHERE Id = '0Q0g8000000ajWTCAY'"
res = sf_client.sf.query(query)
print(res)
