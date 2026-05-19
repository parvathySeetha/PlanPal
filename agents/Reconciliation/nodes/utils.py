import logging
import math
import os
import json
from decimal import Decimal
from core.helper import (
    SalesforceClient,
    call_llm
)

logger = logging.getLogger(__name__)

# Shared instance for all nodes
sf_client = SalesforceClient("demo")

def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        f_val = float(value)
        if math.isnan(f_val):
            return default
        return f_val
    except Exception:
        return default

def _to_decimal(value, default="0"):
    try:
        if value is None:
            return Decimal(str(default))
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))

def _safe_in_clause(ids):
    safe_ids = [f"'{str(i)}'" for i in ids if i]
    return ",".join(safe_ids)

_SEMANTIC_MAPPING_CACHE = {}

async def generate_semantic_mapping(target_keys: dict, object_name: str) -> dict:
    global _SEMANTIC_MAPPING_CACHE
    
    # Create a cache key that includes both object name and the specific target keys being requested
    cache_key = f"{object_name}_{'-'.join(sorted(target_keys.keys()))}"
    if cache_key in _SEMANTIC_MAPPING_CACHE:
        return _SEMANTIC_MAPPING_CACHE[cache_key]

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(os.path.dirname(base_dir))
    schema_path = os.path.join(project_root, "schema_metadata.json")
    
    if not os.path.exists(schema_path):
        schema_path = os.path.join(os.getcwd(), "schema_metadata.json")
        
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read schema for AI mapping: {e}")
        return {}
        
    obj_meta = next((item for item in data if item["object"].lower() == object_name.lower()), {})
    fields = obj_meta.get("fields", [])
    
    schema_context = []
    for f in fields:
        schema_context.append(f"{f.get('apiname')} - {f.get('description', 'No description')} ({f.get('datatype')})")
        
    schema_text = "\n".join(schema_context)
    
    system_prompt = (
        "You are an expert Salesforce data architect. "
        "Your task is to map target JSON keys to the most appropriate Salesforce API names (apiname) based on the provided schema.\n"
        "Return a JSON dictionary where the keys are the target keys and the values are lists of Salesforce API names to try in order of preference.\n"
        "If a value is nested (like a relationship field), use dot notation (e.g. 'Product2.Name').\n"
        "ONLY output valid JSON without any markdown formatting or explanation."
    )
    
    user_prompt = f"Target Keys to map:\n{json.dumps(target_keys, indent=2)}\n\nAvailable Salesforce Fields for {object_name}:\n{schema_text}\n\nOutput format:\n{{\n  \"targetKey1\": [\"apiname1\", \"fallback_apiname2\"],\n  \"targetKey2\": [\"apiname3\"]\n}}"
    
    try:
        response = await call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_model="gpt-4o",
            default_provider="openai",
            default_temperature=0
        )
        
        clean_json = response.replace('```json', '').replace('```', '').strip()
        mapping = json.loads(clean_json)
        _SEMANTIC_MAPPING_CACHE[cache_key] = mapping
        logger.info(f"✅ AI Semantic mapping generated for {object_name}")
        return mapping
    except Exception as e:
        logger.error(f"❌ Failed to generate semantic mapping: {e}")
        return {}


def resolve_object_name(preferred_names: list) -> str:
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        project_root = os.path.dirname(os.path.dirname(base_dir))
        schema_path = os.path.join(project_root, "schema_metadata.json")
        
        if not os.path.exists(schema_path):
            schema_path = os.path.join(os.getcwd(), "schema_metadata.json")
            
        with open(schema_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        available_objects = [item["object"].lower() for item in data if "object" in item]
        
        for name in preferred_names:
            if name.lower() in available_objects:
                return name
                
        return preferred_names[0] if preferred_names else ""
    except Exception as e:
        logger.error(f"Error resolving object name: {e}")
        return preferred_names[0] if preferred_names else ""

def get_query_fields(object_name: str) -> str:
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        project_root = os.path.dirname(os.path.dirname(base_dir))
        schema_path = os.path.join(project_root, "schema_metadata.json")
        
        if not os.path.exists(schema_path):
            schema_path = os.path.join(os.getcwd(), "schema_metadata.json")
            
        with open(schema_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        obj_meta = next((item for item in data if item["object"].lower() == object_name.lower()), {})
        fields = obj_meta.get("fields", [])
        
        apiname_list = [f.get("apiname") for f in fields if f.get("apiname")]
        
        # Always ensure 'Id' is included
        if "Id" not in apiname_list and "id" not in apiname_list:
            apiname_list.insert(0, "Id")
            
        if not apiname_list:
            logger.warning(f"⚠️ No fields found for object {object_name} in schema_metadata.json")
            return "Id"  # Fallback
            
        return ", ".join(apiname_list)
        
    except Exception as e:
        logger.error(f"Error reading schema for {object_name}: {e}")
        return "Id"
