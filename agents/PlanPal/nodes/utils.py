import logging
from core.helper import SalesforceClient, call_llm

logger = logging.getLogger(__name__)

# Shared instance for all nodes
sf_client = SalesforceClient("demo")
