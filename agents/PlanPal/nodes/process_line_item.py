import logging
import json
import os
import aiohttp
import re
from agents.PlanPal.state import PlanPalState
from core.helper import ensure_sf_connected, call_llm, fetch_prompt_metadata_mongo
from .utils import sf_client

logger = logging.getLogger(__name__)

async def process_line_item_node(state: PlanPalState) -> PlanPalState:
    """
    Acts as backend for Guided Selling UI.
    Fetches Pricebook, Queries PricebookEntries, matches Product,
    fetches schema fields, and uses LLM to generate the UI JSON.
    """
    record_id = state.get("record_id", "")
    user_goal = state.get("user_goal", "")

    if not record_id:
        logger.error("❌ No record_id found in state. Cannot process line item.")
        state["error"] = "No Record ID provided context."
        return state

    logger.info(f"🔍 [PlanPal] Processing line item request for Record: '{record_id}'...")

    try:
        if not ensure_sf_connected(sf_client):
            logger.error("❌ Salesforce connection failed")
            state["error"] = "Salesforce connection failed"
            return state

        # 1. Determine Object Type based on Prefix
        prefix = str(record_id)[:3]
        if prefix == '0Q0':
            parent_obj = 'Quote'
            child_obj = 'QuoteLineItem'
        elif prefix == '801':
            parent_obj = 'Order'
            child_obj = 'OrderItem'
        else:
            parent_obj = 'Quote'
            child_obj = 'QuoteLineItem'

        logger.info(f"   [PlanPal] Detected Parent Object: {parent_obj}, Child Object: {child_obj}")

        # 2. Get Pricebook2Id
        logger.info(f"   [PlanPal] Querying Pricebook2Id for {parent_obj} Id: {record_id}")
        pb_query = f"SELECT Id, Pricebook2Id FROM {parent_obj} WHERE Id = '{record_id}'"
        pb_results = sf_client.sf.query(pb_query)
        logger.info(f"   [PlanPal] 🚨 Error detected in graph state: {pb_results.get('totalSize', 0)} records.")
        
        if not pb_results.get("records"):
            state["error"] = f"{parent_obj} with ID {record_id} not found."
            return state
            
        record_data = pb_results["records"][0]
        pricebook_id = record_data.get("Pricebook2Id")

        if not pricebook_id:
            logger.warning(f"⚠️ [PlanPal] Record {record_id} has no Pricebook. Searching for Standard Pricebook...")
            std_pb_query = "SELECT Id FROM Pricebook2 WHERE IsStandard = true LIMIT 1"
            std_pb_results = sf_client.sf.query(std_pb_query)
            if std_pb_results.get("records"):
                pricebook_id = std_pb_results["records"][0]["Id"]
                logger.info(f"   [PlanPal] Found Standard Pricebook: {pricebook_id}")
            else:
                logger.error(f"❌ [PlanPal] No Pricebook associated with {parent_obj} and no Standard Pricebook found.")
                state["error"] = f"No Pricebook associated with {parent_obj} and no Standard Pricebook found."
                return state

        logger.info(f"✅ [PlanPal] Using Pricebook2Id: {pricebook_id}")

        # 3. Query PricebookEntry
        logger.info(f"   [PlanPal] Querying active PricebookEntries for Pricebook2Id: {pricebook_id}...")
        pbe_query = f"""
            SELECT Id, Product2.Name, Product2.Family, Product2Id, UnitPrice 
            FROM PricebookEntry 
            WHERE Pricebook2Id = '{pricebook_id}' 
            AND IsActive = true 
            LIMIT 200
        """
        pbe_results = sf_client.sf.query(pbe_query)
        pbe_records = pbe_results.get("records", [])
        
        logger.info(f"   [PlanPal] PricebookEntry Query returned {len(pbe_records)} records.")

        if not pbe_records:
            logger.error(f"❌ [PlanPal] No active PricebookEntries found for Pricebook {pricebook_id}.")
            state["error"] = f"No active PricebookEntries found for Pricebook {pricebook_id}."
            return state

        available_products_text = "\\n".join([f"- {r['Product2']['Name']} (Id: {r['Id']})" for r in pbe_records if r.get('Product2')])

        # 4. Match Product using LLM
        logger.info(f"   [PlanPal] Calling LLM to semantically match the product from user goal: '{user_goal}'")
        match_system_prompt = (
            "You are a helpful assistant. Identify the product the user wants from the list of Available Products.\n"
            "If found, return ONLY a JSON object: {\"found\": true, \"product_name\": \"<name>\", \"pricebook_entry_id\": \"<id>\"}\n"
            "If the user doesn't mention a clear product, or it is not in the list, return: {\"found\": false, \"message\": \"Ask the user which product they want.\"}"
        )
        match_user_prompt = f"User Request: '{user_goal}'\n\nAvailable Products:\n{available_products_text}"
        
        #ps commented on may 13
        # match_response = await call_llm(
        #     system_prompt=match_system_prompt,
        #     user_prompt=match_user_prompt,
        #     default_model="gpt-4o",
        #     default_provider="openai",
        #     default_temperature=0
        # )

        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            payload = {
                "model": "gemma4:latest",
                "messages": [
                    {"role": "system", "content": match_system_prompt},
                    {"role": "user", "content": match_user_prompt}
                ],
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0,
                    "num_predict": 500
                }
            }
            headers = {
                "Content-Type": "application/json",
                "ngrok-skip-browser-warning": "true"
            }
            async with session.post('https://mendy-myological-electronically.ngrok-free.dev/api/chat', json=payload, headers=headers) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                    match_response = data.get("message", {}).get("content", "")
                except json.JSONDecodeError:
                    logger.error(f"❌ [PlanPal] Local API returned non-JSON. Status: {resp.status}, Body: {text}")
                    match_response = "{}"
        
        logger.info(f"   [PlanPal] LLM semantic match raw response: {match_response}")
        
        try:
            match_json = json.loads(match_response.replace('```json', '').replace('```', '').strip())
        except json.JSONDecodeError:
            logger.error("❌ [PlanPal] Failed to parse JSON from LLM semantic match.")
            match_json = {"found": False, "message": "Failed to parse product match."}
            
        if not match_json.get("found"):
            logger.warning(f"⚠️ [PlanPal] Product match not found. Reason: {match_json.get('message')}")
            state["error"] = match_json.get("message", "Please specify a valid product.")
            return state
            
        logger.info(f"✅ [PlanPal] Successfully Matched Product: {match_json.get('product_name')} (PBE ID: {match_json.get('pricebook_entry_id')})")
        
        # Enrich match_json with product_id and family from pbe_records
        matched_pbe_id = match_json.get("pricebook_entry_id")
        matched_pbe_record = next((r for r in pbe_records if r['Id'] == matched_pbe_id), None)
        if matched_pbe_record:
            match_json['product_id'] = matched_pbe_record.get('Product2Id')
            match_json['family'] = matched_pbe_record.get('Product2', {}).get('Family')

        # 5. Fetch OfferUIDefinition from Salesforce
        matched_product_id = match_json.get('product_id')
        logger.info(f"   [PlanPal] Querying OfferUITheme for Product: {matched_product_id}")
        
        offer_ui_query = f"""
            SELECT crma_i_act__OfferUIDefinition__c, crma_i_act__Title__c 
            FROM crma_i_act__OfferUITheme__c 
            WHERE crma_i_act__Product__c = '{matched_product_id}' 
            AND crma_i_act__UITheme__r.Name = 'Guided Selling' 
            AND crma_i_act__IsActive__c = true 
            LIMIT 1
        """
        try:
            offer_ui_results = sf_client.sf.query(offer_ui_query)
            if offer_ui_results.get('totalSize', 0) > 0:
                offer_record = offer_ui_results['records'][0]
                base_offer_definition = offer_record.get('crma_i_act__OfferUIDefinition__c', '{}')
                offer_title = offer_record.get('crma_i_act__Title__c')
                if not offer_title:
                    offer_title = f'Provide line item details {match_json.get("product_name")}'
            else:
                logger.warning(f"⚠️ [PlanPal] No active OfferUITheme found for product {matched_product_id}. Using empty template.")
                base_offer_definition = '{"Section": [{"SectionLabel": "Line Item Details", "SectionName": "LineItemDetails", "DataSource": []}]}'
                offer_title = f'Provide line item details {match_json.get("product_name")}'
        except Exception as e:
            logger.error(f"❌ [PlanPal] Error querying OfferUITheme: {e}")
            base_offer_definition = '{"Section": [{"SectionLabel": "Line Item Details", "SectionName": "LineItemDetails", "DataSource": []}]}'
            offer_title = f'Provide line item details {match_json.get("product_name")}'

        # 6. Extract LineItemDetails fields to keep prompt small
        logger.info(f"   [PlanPal] Building prompt for OfferUIDefinition injection...")
        
        try:
            base_offer_dict = json.loads(base_offer_definition)
            line_item_section = next((sec for sec in base_offer_dict.get("Section", []) if sec.get("SectionName") == "LineItemDetails"), None)
            
            if line_item_section:
                available_fields = [{"name": ds.get("name"), "label": ds.get("label"), "type": ds.get("uiElement")} for ds in line_item_section.get("DataSource", [])]
            else:
                available_fields = []
        except Exception as e:
            logger.error(f"❌ [PlanPal] Failed to parse base_offer_definition: {e}")
            base_offer_dict = {"Section": [{"SectionLabel": "Line Item Details", "SectionName": "LineItemDetails", "DataSource": []}]}
            available_fields = []
            
        system_prompt = (
            "You are a data extraction engine.\n"
            "Your task is to extract field values from the user's request based on the Available Fields provided.\n"
            "Return ONLY a JSON dictionary where keys are the field 'name' and values are the requested values.\n"
            "Example: If user asks 'add internet with quantity 5' and Quantity is an available field, return {\"Quantity\": 5}.\n"
            "If no fields match, return {}."
        )
        
        user_prompt = f"User Question: \"{user_goal}\"\n\nAvailable Fields:\n{json.dumps(available_fields)}"
        
        logger.info(f"   [PlanPal] Calling LLM to extract default values. System prompt length: {len(system_prompt)}. User prompt length: {len(user_prompt)}.")
        
        # 7. Generate UI JSON
        #ps commented on may 13
        # json_response = await call_llm(
        #     system_prompt=system_prompt,
        #     user_prompt=user_prompt,
        #     default_model=prompt_meta.get("model", "gpt-4o") if prompt_meta else "gpt-4o",
        #     default_provider=prompt_meta.get("provider", "openai") if prompt_meta else "openai",
        #     default_temperature=0
        # )

        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            payload = {
                "model": "gemma4:latest",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0,
                    "num_predict": 500
                }
            }
            headers = {
                "Content-Type": "application/json",
                "ngrok-skip-browser-warning": "true"
            }
            async with session.post('https://mendy-myological-electronically.ngrok-free.dev/api/chat', json=payload, headers=headers) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                    json_response = data.get("message", {}).get("content", "")
                except json.JSONDecodeError:
                    logger.error(f"❌ [PlanPal] Local API returned non-JSON. Status: {resp.status}, Body: {text}")
                    json_response = "{}"
        
        logger.info(f"   [PlanPal] LLM JSON generation complete. Raw response:\n{json_response}")
        
        try:
            clean_json = json_response.replace('```json', '').replace('```', '').strip()
            updates = json.loads(clean_json)
            
            # Inject updates into base_offer_dict
            if isinstance(updates, dict) and "Section" in base_offer_dict:
                for sec in base_offer_dict["Section"]:
                    if sec.get("SectionName") == "LineItemDetails":
                        for ds in sec.get("DataSource", []):
                            field_name = ds.get("name")
                            if field_name in updates:
                                ds["defaultValue"] = updates[field_name]
                                
            generated_json = base_offer_dict
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ [PlanPal] Failed to parse generated JSON: {e}")
            # If LLM failed, just use the base definition without defaults
            generated_json = base_offer_dict

        # Remove Product2Id from DataSource arrays
        if "Section" in generated_json and isinstance(generated_json["Section"], list):
            for section in generated_json["Section"]:
                if "DataSource" in section and isinstance(section["DataSource"], list):
                    section["DataSource"] = [
                        item for item in section["DataSource"] 
                        if item.get("name") != "Product2Id"
                    ]

        # 8. Set Output
        state["structured_summary"] = {
            "OfferUIDefinition": json.dumps(generated_json),
            "productName": match_json.get('product_name'),
            "productId": match_json.get('product_id'),
            "priceBookEntryId": match_json.get('pricebook_entry_id'),
            "family": match_json.get('family'),
            # "Title": offer_title
            "Title": ""
        }

        
        logger.info("✅ [PlanPal] Successfully built structured_summary payload for frontend UI.")
        state["insertion_status"] = {"success": True, "ui_generated": True}

    except Exception as e:
        logger.error(f"❌ [PlanPal] Error processing line item: {e}")
        state["error"] = f"Error processing line item: {str(e)}"

    return state
