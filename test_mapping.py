import asyncio
from agents.Reconciliation.graph import generate_semantic_mapping

async def test():
    resolved_ili_obj = "Custom_Invoice_Line_Item__c"
    
    # Data extraction target keys
    cili_target_keys = {
        "billed_amount": "The final billed price of the line item (Note: strictly prioritize 'Price' fields over 'Amount' fields)",
    }
    cili_mapping = await generate_semantic_mapping(cili_target_keys, resolved_ili_obj)
    print("DATA MAP:", cili_mapping)

asyncio.run(test())
