 

# import os
# from dotenv import load_dotenv
# load_dotenv()
# from vault_utils import read_secret
# # Load both org secrets only once
# ORG_SECRETS = {
#     "marketing": {}, # User requested to use Env vars for marketing
#     "agent": read_secret("agent_salesforce_org"),
#      "demo": read_secret("demo_salesforce_org")   
# }

# def get_salesforce_config(org_type: str) -> dict:
#     """
#     Returns the Salesforce credentials for the given org type.
#     Prioritizes Vault secrets if present, then Environment Variables.
#     """
#     if org_type not in ORG_SECRETS:
#         raise ValueError(f"Unknown org_type: {org_type}. Use 'marketing' or 'agent'.")

#     secrets = ORG_SECRETS[org_type]
    
#     # Helper to get from secrets OR env
#     def get_val(key, default=""):
#         return secrets.get(key) or os.getenv(key, default)

#     return {
#         "SALESFORCE_USERNAME": get_val("SALESFORCE_USERNAME"),
#         "SALESFORCE_PASSWORD": get_val("SALESFORCE_PASSWORD"),
#         "SALESFORCE_SECURITY_TOKEN": get_val("SALESFORCE_SECURITY_TOKEN"),
#         "SALESFORCE_INSTANCE_URL": get_val("SALESFORCE_INSTANCE_URL"),
#         "SALESFORCE_DOMAIN": get_val("SALESFORCE_DOMAIN", "login")
#     }

import os
from dotenv import load_dotenv
load_dotenv()
from vault_utils import read_secret
# Load both org secrets only once
ORG_SECRETS = {
    "marketing": {}, # User requested to use Env vars for marketing
    "agent": read_secret("agent_salesforce_org"),
    "demo": read_secret("demo_salesforce_org"),
}

def get_salesforce_config(org_type: str) -> dict:
    """
    Returns the Salesforce credentials for the given org type.
    Prioritizes Vault secrets if present, then Environment Variables.
    """
    if org_type not in ORG_SECRETS:
        raise ValueError(f"Unknown org_type: {org_type}. Use 'marketing', 'agent' or 'demo'.")

    secrets = ORG_SECRETS[org_type]
    prefix = "" if org_type == "marketing" else f"{org_type.upper()}_"
   
    # Helper to get from secrets OR env
    def get_val(key, default=""):
        # First try the specific prefixed env var (e.g., DEMO_SALESFORCE_USERNAME)
        prefixed_key = f"{prefix}{key}"
        return secrets.get(key) or os.getenv(prefixed_key) or os.getenv(key, default)

    return {
        "SALESFORCE_USERNAME": get_val("SALESFORCE_USERNAME"),
        "SALESFORCE_PASSWORD": get_val("SALESFORCE_PASSWORD"),
        "SALESFORCE_SECURITY_TOKEN": get_val("SALESFORCE_SECURITY_TOKEN"),
        "SALESFORCE_INSTANCE_URL": get_val("SALESFORCE_INSTANCE_URL"),
        "SALESFORCE_DOMAIN": get_val("SALESFORCE_DOMAIN", "login")
    }