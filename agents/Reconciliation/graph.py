import logging
import math
import os
import json
from decimal import Decimal
from langgraph.graph import StateGraph, END
from agents.Reconciliation.state import ReconcillationState
from core.helper import (
    ensure_sf_connected,
    SalesforceClient,
    call_llm,
    fetch_prompt_metadata,
    resolve_placeholders,
    _get_nested_value
)

logger = logging.getLogger(__name__)
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

    base_dir = os.path.dirname(os.path.abspath(__file__))
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
        import os, json
        base_dir = os.path.dirname(os.path.abspath(__file__))
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
        import logging
        logging.getLogger(__name__).error(f"Error resolving object name: {e}")
        return preferred_names[0] if preferred_names else ""

def get_query_fields(object_name: str) -> str:
    try:
        logger.info(f"PS349011 [Reconciliation")
        base_dir = os.path.dirname(os.path.abspath(__file__))
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


async def fetch_delivery_data_node(state: ReconcillationState) -> ReconcillationState:
    """
    Fetch grouped custom invoice lines (1 line per product),
    fetch matching OrderItems for that product under the order,
    and fetch delivery data within the invoice line period.

    Assumption:
    Advertiser name = Billing Account name on Custom_Invoice__c.
    """
    order_id = state.get("record_id")

    if not order_id:
        logger.error("❌ No record_id found in state. Cannot fetch data.")
        state["error"] = "No Order ID provided context."
        return state

    logger.info(f"🔍 [Reconciliation] Fetching grouped Invoice + Line Items for Order: '{order_id}'...")

    delivery_info = {
        "order_id": order_id,
        "invoice_id": None,
        "invoice_name": None,
        "line_items": []
    }

    state["invoice_data"] = {}

    try:
        if not ensure_sf_connected(sf_client):
            logger.error("❌ Salesforce connection failed")
            state["error"] = "Salesforce connection failed"
            return state

        # ---- DYNAMIC ORDER ITEM RESOLUTION ----
        resolved_oi_obj = resolve_object_name(["OrderItem", "Order_Item__c"])
        
        oi_where_target = {"order_id_field": "The reference field linking to the Order ID"}
        oi_where_map = await generate_semantic_mapping(oi_where_target, resolved_oi_obj)
        oi_order_field = oi_where_map.get("order_id_field", ["OrderId"])[0] if oi_where_map else "OrderId"

        order_items_fields = get_query_fields(resolved_oi_obj)
        order_items_soql = f"""
            SELECT {order_items_fields}
            FROM {resolved_oi_obj}
            WHERE {oi_order_field} = '{order_id}'
        """
        order_items_results = sf_client.sf.query(order_items_soql)
        order_item_records = order_items_results.get("records", [])

        if not order_item_records:
            logger.warning(f"⚠️ No items found for Order '{order_id}' in {resolved_oi_obj}")
            state["error"] = f"No {resolved_oi_obj}s found for Order {order_id}"
            return state

        product_to_orderitems = {}
        orderitem_details_map = {}

        target_keys_desc = {
            "orderItemNumber": "The identifying number for the order line item",
            "quoteLineItemId": "The ID of the related quote line item",
            "quoteLineNumber": "The line number of the related quote line item",
            "rate": "The rate or unit price of the item",
            "pricingModel": "The pricing model used, e.g., CPM",
            "productName": "The name of the related product"
        }
        
        oi_mapping = await generate_semantic_mapping(target_keys_desc, resolved_oi_obj)
        logger.info(f"✅ newmap 2333 ps234455 oi_mappingnew:  {oi_mapping} ")

        for oi in order_item_records:
            product_id = oi.get("Product2Id")
            if not product_id:
                continue

            product_to_orderitems.setdefault(product_id, []).append(oi)

            # Map values dynamically based on AI mapping
            details = {
                "orderItemNumber": None,
                "quoteLineItemId": None,
                "quoteLineNumber": None,
                "rate": Decimal("0"),
                "pricingModel": "CPM",
                "productName": None
            }
            
            if oi_mapping:
                for t_key, sf_fields in oi_mapping.items():
                    for sf_field in sf_fields:
                        val = _get_nested_value(oi, sf_field)
                        if val is not None:
                            if t_key == "rate":
                                details[t_key] = _to_decimal(val)
                            else:
                                details[t_key] = val
                            break
                            
            orderitem_details_map[oi.get("Id")] = details

        # ---- DYNAMIC INVOICE LINE ITEM RESOLUTION ----
        resolved_ili_obj = resolve_object_name(["Custom_Invoice_Line_Item__c", "InvoiceLine"])
        
        ili_where_target = {"order_id_field": "The field or relationship path linking this invoice line item to the Order ID"}
        ili_where_map = await generate_semantic_mapping(ili_where_target, resolved_ili_obj)
        ili_order_field = ili_where_map.get("order_id_field", ["Invoice__r.Order__c"])[0] if ili_where_map else "Invoice__r.Order__c"

        ili_fields = get_query_fields(resolved_ili_obj)
        ili_soql = f"""
            SELECT {ili_fields}
            FROM {resolved_ili_obj}
            WHERE {ili_order_field} = '{order_id}'
        """
        ili_results = sf_client.sf.query(ili_soql)
        ili_records = ili_results.get("records", [])

        if not ili_records:
            logger.warning(f"⚠️ No Invoice Line Items found for Order '{order_id}' in {resolved_ili_obj}")
            state["error"] = f"No {resolved_ili_obj}s found for Order {order_id}"
            return state

        # Define target mapping for Invoice/Line Items
        cili_target_keys = {
            "invoice_name": "The name of the invoice",
            "invoice_date": "The date of the invoice",
            "total_amount": "The total amount or total charges of the invoice",
            "invoice_status": "The status of the invoice",
            "invoice_start": "The start date of the invoice",
            "invoice_end": "The end date of the invoice",
            "advertiser_id": "The ID of the billing account or advertiser",
            "advertiser_name": "The name of the billing account or advertiser",
            "line_name": "The name of the invoice line item",
            "billed_impressions": "The billed impressions for the line item",
            "billed_amount": "The final billed price of the line item (Note: strictly prioritize 'Price' fields over 'Amount' fields)",
            "pricing_model": "The pricing model used, e.g., CPM",
            "effective_rate": "The effective rate or eCPM",
            "period_start": "The start date of the line item",
            "period_end": "The end date of the line item"
        }
        cili_mapping = await generate_semantic_mapping(cili_target_keys, resolved_ili_obj)
        
        # Define target mapping for Delivery Data
        resolved_delivery_obj = resolve_object_name(["Delivery_Data__c", "DeliveryData"])
        
        delivery_target_keys = {
            "date": "The date of delivery",
            "gross": "The gross impressions delivered",
            "ivt": "The invalid traffic percentage",
            "viewability": "The viewability percentage",
            "orderProductId": "The ID of the related order product"
        }
        delivery_mapping = await generate_semantic_mapping(delivery_target_keys, resolved_delivery_obj)

        first_ili = ili_records[0]
        invoice_r = first_ili.get("Invoice__r") or {}
        invoice_id = first_ili.get("Invoice__c")
        
        # 1. Map invoice_data defaults
        inv_details = {
            "id": invoice_id,
            "name": invoice_id,
            "invoice_date": None,
            "total_amount": Decimal("0"),
            "status": None,
            "start": None,
            "end": None,
            "advertiser_id": None,
            "advertiser_name": "Unknown"
        }
        
        if cili_mapping:
            mapping_to_state = {
                "invoice_name": "name",
                "invoice_date": "invoice_date",
                "total_amount": "total_amount",
                "invoice_status": "status",
                "invoice_start": "start",
                "invoice_end": "end",
                "advertiser_id": "advertiser_id",
                "advertiser_name": "advertiser_name"
            }
            for t_key, sf_fields in cili_mapping.items():
                if t_key in mapping_to_state:
                    state_key = mapping_to_state[t_key]
                    for sf_field in sf_fields:
                        val = _get_nested_value(first_ili, sf_field)
                        if val is not None:
                            if state_key == "total_amount":
                                inv_details[state_key] = _to_decimal(val)
                            else:
                                inv_details[state_key] = val
                            break
                            
        state["invoice_data"] = inv_details
        delivery_info["invoice_id"] = invoice_id
        delivery_info["invoice_name"] = inv_details["name"]
        
        logger.info(f"✅ Found Invoice: ({invoice_id}) and {len(ili_records)} grouped line item(s) from {resolved_ili_obj}")

        for ili in ili_records:
            ili_id = ili.get("Id")
            product_id = ili.get("Product__c")

            if not product_id:
                logger.warning(f"⚠️ Invoice Line Item {ili_id} has no Product__c. Skipping.")
                continue
                
            # 2. Map item_data defaults
            ili_details = {
                "line_name": ili_id,
                "billed_impressions": Decimal("0"),
                "billed_amount": Decimal("0"),
                "pricing_model": "CPM",
                "effective_rate": Decimal("0"),
                "period_start": None,
                "period_end": None
            }
            
            if cili_mapping:
                mapping_to_item = ["line_name", "billed_impressions", "billed_amount", "pricing_model", "effective_rate", "period_start", "period_end"]
                for t_key, sf_fields in cili_mapping.items():
                    if t_key in mapping_to_item:
                        for sf_field in sf_fields:
                            val = _get_nested_value(ili, sf_field)
                            if val is not None:
                                if t_key in ["billed_impressions", "billed_amount", "effective_rate"]:
                                    ili_details[t_key] = _to_decimal(val)
                                else:
                                    ili_details[t_key] = val
                                break

            # Use the mapped values
            line_name = ili_details["line_name"]
            billed_impressions = ili_details["billed_impressions"]
            billed_amount = ili_details["billed_amount"]
            pricing_model = ili_details["pricing_model"]
            effective_rate = ili_details["effective_rate"]
            period_start = ili_details["period_start"]
            period_end = ili_details["period_end"]

            matching_order_items = product_to_orderitems.get(product_id, [])
            if not matching_order_items:
                logger.warning(
                    f"⚠️ No matching OrderItems found for product {product_id} on invoice line {ili_id}"
                )
                continue

            product_name = None
            oi_numbers = []
            for oi in matching_order_items:
                if oi.get("Product2") and oi["Product2"].get("Name"):
                    product_name = oi["Product2"]["Name"]
                if oi.get("OrderItemNumber"):
                    oi_numbers.append(oi.get("OrderItemNumber"))

            product_name = product_name or line_name
            matching_oi_ids = [oi["Id"] for oi in matching_order_items if oi.get("Id")]
            in_clause = _safe_in_clause(matching_oi_ids)

            if not in_clause:
                logger.warning(f"⚠️ No valid OrderItem ids found for invoice line {ili_id}")
                continue

            if not period_start or not period_end:
                logger.warning(f"⚠️ Missing invoice line date range for grouped line {ili_id}. Skipping.")
                continue

            logger.info(
                f"   Fetching delivery blocks for product: {product_name} "
                f"(OrderItems: {', '.join(oi_numbers) if oi_numbers else 'N/A'}) "
                f"from {period_start} to {period_end} in {resolved_delivery_obj}"
            )

            del_where_target = {
                "order_product_field": "The reference field linking to the Order Product or Order Item ID",
                "date_field": "The field representing the delivery date"
            }
            del_where_map = await generate_semantic_mapping(del_where_target, resolved_delivery_obj)
            
            del_op_field = del_where_map.get("order_product_field", ["Order_Product__c"])[0] if del_where_map else "Order_Product__c"
            del_date_field = del_where_map.get("date_field", ["Date__c"])[0] if del_where_map else "Date__c"

            delivery_fields = get_query_fields(resolved_delivery_obj)
            delivery_soql = f"""
                SELECT {delivery_fields}
                FROM {resolved_delivery_obj}
                WHERE {del_op_field} IN ({in_clause})
                  AND {del_date_field} >= {period_start}
                  AND {del_date_field} <= {period_end}
                ORDER BY {del_date_field} ASC
            """
            delivery_results = sf_client.sf.query_all(delivery_soql)
            delivery_records = delivery_results.get("records", [])

            item_data = {
                "ili_id": ili_id,
                "ili_name": product_name,
                "product_id": product_id,
                "oli_ids": matching_oi_ids,
                "oli_names": oi_numbers,
                "pricing_model": pricing_model,
                "effective_rate": effective_rate,
                "billed_impressions": billed_impressions,
                "billed_amount": billed_amount,
                "period_start": period_start,
                "period_end": period_end,
                "daily_blocks": []
            }

            # 3. Map daily_blocks
            for rec in delivery_records:
                order_product_id = rec.get(del_op_field) or rec.get("Order_Product__c")
                
                block_details = {
                    "date": None,
                    "gross": Decimal("0"),
                    "ivt": Decimal("0"),
                    "viewability": Decimal("0"),
                    "orderProductId": order_product_id
                }
                
                if delivery_mapping:
                    for t_key, sf_fields in delivery_mapping.items():
                        for sf_field in sf_fields:
                            val = _get_nested_value(rec, sf_field)
                            if val is not None:
                                if t_key == "gross":
                                    block_details[t_key] = _to_decimal(val)
                                elif t_key in ["ivt", "viewability"]:
                                    block_details[t_key] = _to_decimal(val, "0") / Decimal("100")
                                else:
                                    block_details[t_key] = val
                                break
                                
                order_product_id = block_details["orderProductId"]
                order_details = orderitem_details_map.get(order_product_id, {})
                
                item_data["daily_blocks"].append({
                    "date": block_details["date"],
                    "gross": block_details["gross"],
                    "ivt": block_details["ivt"],
                    "viewability": block_details["viewability"],
                    "orderProductId": order_product_id,
                    "orderLineItemNumber": order_details.get("orderItemNumber") or "N/A",
                    "quoteLineItemId": order_details.get("quoteLineItemId"),
                    "quoteLineNumber": order_details.get("quoteLineNumber") or "N/A",
                    "orderLineRate": _to_decimal(order_details.get("rate")),
                    "orderLinePricingModel": order_details.get("pricingModel") or pricing_model
                })

            delivery_info["line_items"].append(item_data)
            logger.info(f"      ✅ Added {len(delivery_records)} delivery block(s) for grouped product line {product_name}")

    except Exception as e:
        logger.error(f"❌ Salesforce query error: {e}")
        state["error"] = f"Salesforce query error: {str(e)}"

    state["delivery_data"] = delivery_info
    logger.info(f"📦 [DEBUG] FETCH DELIVERY DATA NODE OUTPUT:\n{json.dumps(delivery_info, indent=2, default=str)}")
    return state


async def calculate_node(state: ReconcillationState) -> ReconcillationState:
    """
    Calculate grouped metrics.
    IMPORTANT: revenue is calculated using actual row-level OrderItem rate.
    """
    logger.info("📊 [Reconciliation] Calculating grouped line metrics...")

    data = state.get("delivery_data", {})
    line_items = data.get("line_items", [])

    total_metrics = {
        "total_gross": Decimal("0.0"),
        "total_invalid": Decimal("0.0"),
        "total_valid": Decimal("0.0"),
        "total_billable_viewable": Decimal("0.0"),
        "line_metrics": []
    }

    for item in line_items:
        blocks = item.get("daily_blocks", [])

        gross = Decimal("0.0")
        invalid = Decimal("0.0")
        valid_impressions = Decimal("0.0")
        billable_viewable = Decimal("0.0")
        calculated_revenue = Decimal("0.0")

        for b in blocks:
            row_gross = _to_decimal(b.get("gross"))
            row_ivt = _to_decimal(b.get("ivt"))
            row_viewability = _to_decimal(b.get("viewability"))
            row_rate = b.get("orderLineRate") or _to_decimal(item.get("effective_rate"))
            row_pricing_model = b.get("orderLinePricingModel") or item.get("pricing_model", "CPM")

            row_invalid = row_gross * row_ivt
            row_valid = row_gross - row_invalid
            row_billable_viewable = row_valid * row_viewability

            if row_pricing_model == "CPM":
                row_revenue = (row_billable_viewable / Decimal("1000")) * row_rate
            else:
                row_revenue = row_billable_viewable * row_rate

            gross += row_gross
            invalid += row_invalid
            valid_impressions += row_valid
            billable_viewable += row_billable_viewable
            calculated_revenue += row_revenue

        ivt_pct = (invalid / gross) if gross > 0 else Decimal("0")
        viewability_pct = (billable_viewable / valid_impressions) if valid_impressions > 0 else Decimal("0")

        item_metrics = {
            "ili_id": item.get("ili_id"),
            "ili_name": item.get("ili_name"),
            "product_id": item.get("product_id"),
            "oli_ids": item.get("oli_ids", []),
            "oli_names": item.get("oli_names", []),
            "effective_rate": _to_decimal(item.get("effective_rate")),
            "pricing_model": item.get("pricing_model", "CPM"),
            "billed_impressions": _to_decimal(item.get("billed_impressions")),
            "billed_amount": _to_decimal(item.get("billed_amount")),
            "gross": gross,
            "invalid": invalid,
            "ivt_pct": ivt_pct,
            "viewability_pct": viewability_pct,
            "valid_impressions": valid_impressions,
            "billable_viewable": billable_viewable,
            "calculated_impressions": billable_viewable,
            "calculated_revenue": calculated_revenue,
        }

        total_metrics["line_metrics"].append(item_metrics)
        total_metrics["total_gross"] += gross
        total_metrics["total_invalid"] += invalid
        total_metrics["total_valid"] += valid_impressions
        total_metrics["total_billable_viewable"] += billable_viewable

    total_metrics["avg_ivt_pct"] = (
        total_metrics["total_invalid"] / total_metrics["total_gross"]
        if total_metrics["total_gross"] > 0 else Decimal("0")
    )

    total_metrics["avg_viewability_pct"] = (
        total_metrics["total_billable_viewable"] / total_metrics["total_valid"]
        if total_metrics["total_valid"] > 0 else Decimal("0")
    )

    state["monthly_metrics"] = total_metrics
    logger.info(f"✅ Grouped monthly metrics calculated: {total_metrics}")
    return state


async def amendment_node(state: ReconcillationState) -> ReconcillationState:
    """
    Compare grouped billed amount vs grouped calculated revenue.
    """
    logger.info("📑 [Reconciliation] Checking grouped invoice variance vs delivery...")

    metrics = state.get("monthly_metrics", {})
    delivery_data = state.get("delivery_data", {})
    line_results = []

    total_calculated_revenue = Decimal("0.0")
    total_billed_revenue = Decimal("0.0")

    for item_metrics, item_raw in zip(metrics.get("line_metrics", []), delivery_data.get("line_items", [])):
        calculated_imp = _to_decimal(item_metrics.get("calculated_impressions"))
        calculated_revenue = _to_decimal(item_metrics.get("calculated_revenue"))
        billed_revenue = _to_decimal(item_metrics.get("billed_amount"))
        variance = calculated_revenue - billed_revenue

        tolerance = max(Decimal("0.01"), abs(billed_revenue) * Decimal("0.00001"))

        status = "Ok"
        if variance > tolerance:
            status = "Underbilled (Potential Leakage)"
        elif variance < -tolerance:
            status = "Overbilled (Customer Disputed)"

        line_results.append({
            "ili_name": item_metrics.get("ili_name"),
            "product_id": item_raw.get("product_id"),
            "oli_ids": item_raw.get("oli_ids", []),
            "oli_names": item_raw.get("oli_names", []),
            "pricing_model": item_raw.get("pricing_model", "CPM"),
            "effective_rate": _to_decimal(item_raw.get("effective_rate")),
            "calculated_impressions": calculated_imp,
            "billed_impressions": _to_decimal(item_metrics.get("billed_impressions")),
            "calculated_revenue": calculated_revenue,
            "billed_revenue": billed_revenue,
            "variance": variance,
            "status": status
        })

        total_calculated_revenue += calculated_revenue
        total_billed_revenue += billed_revenue

    state["amendment_results"] = {
        "line_results": line_results,
        "calculated_total_revenue": total_calculated_revenue,
        "billed_total_revenue": total_billed_revenue
    }

    return state


async def variance_node(state: ReconcillationState) -> ReconcillationState:
    logger.info("📉 [Reconciliation] Calculating overall invoice variance...")

    results = state.get("amendment_results", {})
    correct = _to_decimal(results.get("calculated_total_revenue"))
    billed = _to_decimal(results.get("billed_total_revenue"))
    variance = correct - billed

    tolerance = max(Decimal("0.01"), abs(billed) * Decimal("0.00001"))

    if abs(variance) < Decimal("0.005"):
        variance = Decimal("0.00")

    state["variance_results"] = {
        "variance": variance,
        "status": "Underbilled" if variance > tolerance else "Overbilled" if variance < -tolerance else "Ok",
        "leakage_detected": variance > tolerance
    }

    return state

async def summary_response_node(state: ReconcillationState) -> ReconcillationState:
    logger.info("💬 [Reconciliation] Generating summary response...")

    error = state.get("error")
    user_goal = state.get("user_goal", "")
    variance = state.get("variance_results", {})
    metrics = state.get("monthly_metrics", {})
    amendment = state.get("amendment_results", {})

    system_prompt = None
    user_prompt = None

    # try:
    #     prompt_meta = fetch_prompt_metadata("summary_response_node", "Reconciliation Agent")
    #
    #     if prompt_meta and prompt_meta.get("prompt"):
    #         system_prompt = resolve_placeholders(
    #             prompt_meta["prompt"],
    #             prompt_meta.get("configs", {}),
    #             state
    #         )
    #         user_prompt = ""
    #         logger.info("✅ Using prompt metadata for summary_response_node")
    #     else:
    #         logger.warning("⚠️ Prompt metadata not found. Using fallback prompt.")
    #
    # except Exception as e:
    #     logger.warning(f"⚠️ Failed to fetch/resolve prompt metadata. Using fallback prompt. Error: {e}")

    if not system_prompt:
        if error:
            system_prompt = (
                "You are a specialized Reconciliation Agent. Briefly explain why the reconciliation could not be completed."
            )
            user_prompt = (
                f"User Question: {user_goal}\n"
                f"Error Encountered: {error}\n\n"
                f"Please explain to the user that we couldn't complete the validation and why."
            )
        else:
            system_prompt = (
                "You are a specialized Reconciliation Agent. Provide a very brief summary of the invoice vs expected delivery. "
                "Do NOT explain line-by-line math. Only tell the user whether the invoice is good to proceed, Underbilled, or Overbilled, "
                "and direct them to click 'View Details' for the full breakdown."
            )
            user_prompt = f"""
User Question: {user_goal}

Reconciliation Data:
- Total Gross Impressions: {metrics.get('total_gross', 0):,.0f}
- Total Valid Impressions: {metrics.get('total_valid', 0):,.0f}
- Total Billable Viewable Impressions: {metrics.get('total_billable_viewable', 0):,.0f}
- Billed Revenue (Invoice): ₹{amendment.get('billed_total_revenue', 0):,.2f}
- Calculated Valid Revenue: ₹{amendment.get('calculated_total_revenue', 0):,.2f}
- Variance: ₹{variance.get('variance', 0):,.2f}
- Status: {variance.get('status', 'Unknown')}
"""
        logger.info("✅ Using fallback prompt for summary_response_node")

    try:
        response = await call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_model="gpt-4o",
            default_provider="openai",
            default_temperature=0
        )
        state["final_response"] = response.strip()

        status_raw = variance.get("status", "Unknown")
        variant = "success" if status_raw == "Ok" else "error"
        line_items_card = []

        for idx, res in enumerate(amendment.get("line_results", []), start=1):
            ili_name = res.get("ili_name")
            product_id = res.get("product_id")
            effective_rate = _to_float(res.get("effective_rate"))
            date_str = "N/A"
            daily_blocks_enriched = []
            billed_impressions = 0.0
            period_start = "N/A"
            period_end = "N/A"
            oli_names = res.get("oli_names", [])

            for item in state.get("delivery_data", {}).get("line_items", []):
                if item.get("product_id") == product_id:
                    billed_impressions = _to_float(item.get("billed_impressions"))
                    period_start = item.get("period_start", "N/A")
                    period_end = item.get("period_end", "N/A")

                    dates = [b.get("date") for b in item.get("daily_blocks", []) if b.get("date")]
                    if dates:
                        date_str = f"{min(dates)} to {max(dates)}"

                    for b in item.get("daily_blocks", []):
                        gross = _to_float(b.get("gross"))
                        ivt_pct = _to_float(b.get("ivt"))
                        viewability_pct = _to_float(b.get("viewability"))
                        row_rate = _to_float(b.get("orderLineRate"), effective_rate)
                        row_pricing_model = b.get("orderLinePricingModel") or item.get("pricing_model", "CPM")

                        invalid_impressions = gross * ivt_pct
                        valid_impressions = gross - invalid_impressions
                        viewable_impressions = valid_impressions * viewability_pct

                        if row_pricing_model == "CPM":
                            daily_revenue = (viewable_impressions / 1000) * row_rate
                        else:
                            daily_revenue = viewable_impressions * row_rate

                        daily_blocks_enriched.append({
                            "date": b.get("date"),
                            "gross": gross,
                            "ivtPct": ivt_pct * 100,
                            "viewabilityPct": viewability_pct * 100,
                            "invalidImpressions": invalid_impressions,
                            "validImpressions": valid_impressions,
                            "viewableImpressions": viewable_impressions,
                            "orderLineItem": b.get("orderLineItemNumber") or "N/A",
                            "pricingModel": row_pricing_model,
                            "rate": row_rate,
                            "revenue": daily_revenue,
                            "productName": ili_name,
                            "description": f"Billable Viewable: {viewable_impressions:,.3f}, Revenue: ₹{daily_revenue:,.2f} (Rate: ₹{row_rate:,.4f})"
                        })
                    break

            line_variance = _to_decimal(res.get("variance"))
            gross_total = sum(_to_decimal(b["gross"]) for b in daily_blocks_enriched)
            invalid_total = sum(_to_decimal(b["invalidImpressions"]) for b in daily_blocks_enriched)
            valid_total = sum(_to_decimal(b["validImpressions"]) for b in daily_blocks_enriched)
            viewable_total = sum(_to_decimal(b["viewableImpressions"]) for b in daily_blocks_enriched)
            calculated_revenue = _to_decimal(res.get("calculated_revenue"))
            billed_revenue = _to_decimal(res.get("billed_revenue"))

            segment_map = {}
            for b in daily_blocks_enriched:
                oli_num = b.get("orderLineItem") or "N/A"
                seg = segment_map.setdefault(oli_num, {
                    "rate": _to_decimal(b.get("rate")),
                    "pricingModel": b.get("pricingModel"),
                    "gross": Decimal("0.0"),
                    "invalid": Decimal("0.0"),
                    "valid": Decimal("0.0"),
                    "viewable": Decimal("0.0"),
                    "revenue": Decimal("0.0"),
                    "dates": []
                })
                seg["gross"] += _to_decimal(b.get("gross"))
                seg["invalid"] += _to_decimal(b.get("invalidImpressions"))
                seg["valid"] += _to_decimal(b.get("validImpressions"))
                seg["viewable"] += _to_decimal(b.get("viewableImpressions"))
                seg["dates"].append(b.get("date"))

                if seg["pricingModel"] == "CPM":
                    seg["revenue"] += (_to_decimal(b.get("viewableImpressions")) / Decimal("1000")) * _to_decimal(b.get("rate"))
                else:
                    seg["revenue"] += _to_decimal(b.get("viewableImpressions")) * _to_decimal(b.get("rate"))

            segment_lines = []
            for oli_num, seg in segment_map.items():
                seg_start = min([d for d in seg["dates"] if d]) if seg["dates"] else "N/A"
                seg_end = max([d for d in seg["dates"] if d]) if seg["dates"] else "N/A"
                segment_lines.append(
                    f"{oli_num}: {seg_start} to {seg_end} at ₹{seg['rate']:,.2f} {seg['pricingModel']} → Revenue ₹{seg['revenue']:,.2f}"
                )

            segment_text = "\n".join(segment_lines) if segment_lines else "No amendment segments found."

            billed_imps_val = _to_decimal(res.get("billed_impressions"))
            calc_imps_val = _to_decimal(res.get("calculated_impressions"))
            imp_diff_pct = abs(billed_imps_val - calc_imps_val) / billed_imps_val if billed_imps_val > 0 else Decimal("0")
            is_rate_mismatch = imp_diff_pct < Decimal("0.0001") and abs(line_variance) > Decimal("0.01")

            if abs(line_variance) < Decimal("0.01"):
                description = (
                    f"Status: Ok.\n\n"
                    f"Gross Impressions: {gross_total:,.0f}\n"
                    f"Invalid Traffic Removed: {invalid_total:,.0f}\n"
                    f"Valid Impressions: {valid_total:,.0f}\n"
                    f"Billable Viewable Impressions: {viewable_total:,.3f}\n\n"
                    f"Effective CPM: ₹{effective_rate:,.4f} {res.get('pricing_model', 'CPM')}\n"
                    f"Calculated Revenue: ₹{calculated_revenue:,.2f}\n"
                    f"Billed Revenue: ₹{billed_revenue:,.2f}\n"
                    f"Variance: ₹{line_variance:,.2f}\n\n"
                    f"Amendment Breakdown:\n{segment_text}\n\n"
                    f"This grouped product line is ok because the billed amount exactly matches the revenue derived "
                    f"from verified delivery data after IVT and viewability adjustments."
                )
            elif line_variance > 0:
                rate_reason = " (primarily due to a rate mismatch)" if is_rate_mismatch else ""
                description = (
                    f"Status: Underbilled.\n\n"
                    f"Gross Impressions: {gross_total:,.0f}\n"
                    f"Invalid Traffic Removed: {invalid_total:,.0f}\n"
                    f"Valid Impressions: {valid_total:,.0f}\n"
                    f"Billable Viewable Impressions: {viewable_total:,.3f}\n\n"
                    f"Effective CPM: ₹{effective_rate:,.4f} {res.get('pricing_model', 'CPM')}\n"
                    f"Calculated Revenue: ₹{calculated_revenue:,.2f}\n"
                    f"Billed Revenue: ₹{billed_revenue:,.2f}\n"
                    f"Variance: ₹{line_variance:,.2f}\n\n"
                    f"Amendment Breakdown:\n{segment_text}\n\n"
                    f"This grouped product line is underbilled{rate_reason} because the delivery-derived revenue is higher than the billed amount."
                )
            else:
                rate_reason = " (primarily due to a rate mismatch)" if is_rate_mismatch else ""
                description = (
                    f"Status: Overbilled.\n\n"
                    f"Gross Impressions: {gross_total:,.0f}\n"
                    f"Invalid Traffic Removed: {invalid_total:,.0f}\n"
                    f"Valid Impressions: {valid_total:,.0f}\n"
                    f"Billable Viewable Impressions: {viewable_total:,.3f}\n\n"
                    f"Effective CPM: ₹{effective_rate:,.4f} {res.get('pricing_model', 'CPM')}\n"
                    f"Calculated Revenue: ₹{calculated_revenue:,.2f}\n"
                    f"Billed Revenue: ₹{billed_revenue:,.2f}\n"
                    f"Variance: ₹{line_variance:,.2f}\n\n"
                    f"Amendment Breakdown:\n{segment_text}\n\n"
                    f"This grouped product line is overbilled{rate_reason} because the billed amount is higher than the revenue "
                    f"derived from verified delivery data after IVT and viewability adjustments."
                )

            line_items_card.append({
                "lineNumber": idx,
                "name": ili_name,
                "effectiveRate": _to_float(effective_rate),
                "rate": _to_float(effective_rate),
                "dates": date_str,
                "periodStart": period_start,
                "periodEnd": period_end,
                "revenue": _to_float(calculated_revenue),
                "billedRevenue": _to_float(billed_revenue),
                "billedImpressions": _to_float(billed_impressions),
                "calculatedImpressions": _to_float(res.get("calculated_impressions")),
                "pricingModel": res.get("pricing_model"),
                "status": res.get("status"),
                "orderLineItems": oli_names,
                "grossImpressions": _to_float(gross_total),
                "invalidImpressions": _to_float(invalid_total),
                "validImpressions": _to_float(valid_total),
                "viewableImpressions": _to_float(viewable_total),
                "dailyBlocks": daily_blocks_enriched,
                "description": description
            })

        state["structured_summary"] = {
            "status": status_raw,
            "statusVariant": variant,
            "currencyCode": "INR",
            "currencySymbol": "₹",
            "totalImpressions": _to_float(state.get("monthly_metrics", {}).get("total_gross")),
            "totalValidImpressions": _to_float(state.get("monthly_metrics", {}).get("total_valid")),
            "totalViewableImpressions": _to_float(state.get("monthly_metrics", {}).get("total_billable_viewable")),
            "totalRevenue": _to_float(amendment.get("calculated_total_revenue")),
            "totalBilled": _to_float(amendment.get("billed_total_revenue")),
            "variance": _to_float(variance.get("variance")),
            "lineItems": line_items_card,
            "invoiceId": state.get("invoice_data", {}).get("id", "Unknown"),
            "invoiceName": state.get("invoice_data", {}).get("name", "Unknown"),
            "invoiceDate": state.get("invoice_data", {}).get("invoice_date"),
            "invoiceStartDate": state.get("invoice_data", {}).get("start"),
            "invoiceEndDate": state.get("invoice_data", {}).get("end"),
            "advertiserName": state.get("invoice_data", {}).get("advertiser_name", "Unknown"),
            "advertiserId": state.get("invoice_data", {}).get("advertiser_id"),
            "orderId": state.get("record_id", "Unknown")
        }

        logger.info(f"✅ Structured summary populated: {variant}")

    except Exception as e:
        logger.error(f"Error in summary generation: {e}")
        if error:
            state["final_response"] = f"Technical Error: {error}. I was unable to complete the reconciliation process."
        else:
            state["final_response"] = (
                f"Reconciliation Complete. Status: {variance.get('status', 'Unknown')}. "
                f"Variance: ₹{_to_float(variance.get('variance')):,.2f}."
            )

    state["next_action"] = "complete"
    return state


def should_continue(state: ReconcillationState) -> str:
    if state.get("error"):
        logger.warning(f"🚨 Error detected in graph state: {state['error']}. Routing to summary.")
        return "summary"
    return "continue"


def build_reconcillation_graph(checkpointer=None):
    workflow = StateGraph(ReconcillationState)

    workflow.add_node("fetchdeliverydata", fetch_delivery_data_node)
    workflow.add_node("Calculate", calculate_node)
    workflow.add_node("Amendment", amendment_node)
    workflow.add_node("Variance", variance_node)
    workflow.add_node("summaryresponse", summary_response_node)

    workflow.set_entry_point("fetchdeliverydata")

    workflow.add_conditional_edges(
        "fetchdeliverydata",
        should_continue,
        {
            "continue": "Calculate",
            "summary": "summaryresponse"
        }
    )

    workflow.add_conditional_edges(
        "Calculate",
        should_continue,
        {
            "continue": "Amendment",
            "summary": "summaryresponse"
        }
    )

    workflow.add_conditional_edges(
        "Amendment",
        should_continue,
        {
            "continue": "Variance",
            "summary": "summaryresponse"
        }
    )

    workflow.add_edge("Variance", "summaryresponse")
    workflow.add_edge("summaryresponse", END)

    return workflow.compile(checkpointer=checkpointer)











# testttttttttttttttttt


# import logging
# import math
# import os
# import json
# from decimal import Decimal
# from langgraph.graph import StateGraph, END
# from agents.Reconciliation.state import ReconcillationState
# from core.helper import (
#     ensure_sf_connected,
#     SalesforceClient,
#     call_llm,
#     fetch_prompt_metadata,
#     resolve_placeholders,
# )

# logger = logging.getLogger(__name__)
# sf_client = SalesforceClient("demo")


# def _to_float(value, default=0.0):
#     try:
#         if value is None:
#             return default
#         f_val = float(value)
#         if math.isnan(f_val):
#             return default
#         return f_val
#     except Exception:
#         return default


# def _to_decimal(value, default="0"):
#     try:
#         if value is None:
#             return Decimal(str(default))
#         return Decimal(str(value))
#     except Exception:
#         return Decimal(str(default))


# def _safe_in_clause(ids):
#     safe_ids = [f"'{str(i)}'" for i in ids if i]
#     return ",".join(safe_ids)


# def get_query_fields(object_name: str) -> str:
#     try:
#         logger.info(f"PS349011 [Reconciliation")
#         base_dir = os.path.dirname(os.path.abspath(__file__))
#         project_root = os.path.dirname(os.path.dirname(base_dir))
#         schema_path = os.path.join(project_root, "schema_metadata.json")
        
#         if not os.path.exists(schema_path):
#             schema_path = os.path.join(os.getcwd(), "schema_metadata.json")
            
#         with open(schema_path, "r", encoding="utf-8") as f:
#             data = json.load(f)
            
#         obj_meta = next((item for item in data if item["object"].lower() == object_name.lower()), {})
#         fields = obj_meta.get("fields", [])
        
#         apiname_list = [f.get("apiname") for f in fields if f.get("apiname")]
        
#         # Always ensure 'Id' is included
#         if "Id" not in apiname_list and "id" not in apiname_list:
#             apiname_list.insert(0, "Id")
            
#         if not apiname_list:
#             logger.warning(f"⚠️ No fields found for object {object_name} in schema_metadata.json")
#             return "Id"  # Fallback
            
#         return ", ".join(apiname_list)
        
#     except Exception as e:
#         logger.error(f"Error reading schema for {object_name}: {e}")
#         return "Id"


# async def fetch_delivery_data_node(state: ReconcillationState) -> ReconcillationState:
#     """
#     Fetch grouped custom invoice lines (1 line per product),
#     fetch matching OrderItems for that product under the order,
#     and fetch delivery data within the invoice line period.

#     Assumption:
#     Advertiser name = Billing Account name on Custom_Invoice__c.
#     """
#     order_id = state.get("record_id")

#     if not order_id:
#         logger.error("❌ No record_id found in state. Cannot fetch data.")
#         state["error"] = "No Order ID provided context."
#         return state

#     logger.info(f"🔍 [Reconciliation] Fetching grouped Custom Invoice + Line Items for Order: '{order_id}'...")

#     delivery_info = {
#         "order_id": order_id,
#         "invoice_id": None,
#         "invoice_name": None,
#         "line_items": []
#     }

#     state["invoice_data"] = {}

#     try:
#         if not ensure_sf_connected(sf_client):
#             logger.error("❌ Salesforce connection failed")
#             state["error"] = "Salesforce connection failed"
#             return state

#         order_items_fields = get_query_fields("OrderItem")
#         order_items_soql = f"""
#             SELECT {order_items_fields}
#             FROM OrderItem
#             WHERE OrderId = '{order_id}'
#         """
#         order_items_results = sf_client.sf.query(order_items_soql)
#         order_item_records = order_items_results.get("records", [])

#         if not order_item_records:
#             logger.warning(f"⚠️ No OrderItems found for Order '{order_id}'")
#             state["error"] = f"No OrderItems found for Order {order_id}"
#             return state

#         product_to_orderitems = {}
#         orderitem_details_map = {}

#         for oi in order_item_records:
#             product_id = oi.get("Product2Id")
#             if not product_id:
#                 continue

#             product_to_orderitems.setdefault(product_id, []).append(oi)

#             orderitem_details_map[oi.get("Id")] = {
#     "orderItemNumber": oi.get("OrderItemNumber"),
#     "quoteLineItemId": oi.get("QuoteLineItemId"),
#     "quoteLineNumber": (oi.get("QuoteLineItem") or {}).get("LineNumber"),
#     "rate": _to_decimal(oi.get("Rate__c")) if oi.get("Rate__c") is not None else _to_decimal(oi.get("UnitPrice")),
#     "pricingModel": oi.get("Pricing_Model__c") or "CPM",
#     "productName": (oi.get("Product2") or {}).get("Name")
# }

#         ili_fields = get_query_fields("Custom_Invoice_Line_Item__c")
#         ili_soql = f"""
#             SELECT {ili_fields}
#             FROM Custom_Invoice_Line_Item__c
#             WHERE Invoice__r.Order__c = '{order_id}'
#         """
#         ili_results = sf_client.sf.query(ili_soql)
#         ili_records = ili_results.get("records", [])

#         if not ili_records:
#             logger.warning(f"⚠️ No Custom Invoice Line Items found for Order '{order_id}'")
#             state["error"] = f"No Custom Invoice Line Items found for Order {order_id}"
#             return state

#         first_ili = ili_records[0]
#         invoice_r = first_ili.get("Invoice__r") or {}
#         invoice_id = first_ili.get("Invoice__c")

#         delivery_info["invoice_id"] = invoice_id
#         delivery_info["invoice_name"] = invoice_r.get("Name") or invoice_id

#         advertiser_name = ((invoice_r.get("Billing_Account__r") or {}).get("Name")) or "Unknown"

#         state["invoice_data"] = {
#             "id": invoice_id,
#             "name": invoice_r.get("Name") or invoice_id,
#             "invoice_date": invoice_r.get("Invoice_Date__c"),
#             "total_amount": _to_decimal(invoice_r.get("Total_Amount__c") or invoice_r.get("Total_Charges__c")),
#             "status": invoice_r.get("Status__c"),
#             "start": invoice_r.get("Start_Date__c") or first_ili.get("Start_Date__c"),
#             "end": invoice_r.get("End_Date__c") or first_ili.get("End_Date__c"),
#             "advertiser_id": invoice_r.get("Billing_Account__c"),
#             "advertiser_name": advertiser_name,
#         }

#         logger.info(f"✅ Found Custom Invoice: ({invoice_id}) and {len(ili_records)} grouped line item(s)")

#         for ili in ili_records:
#             ili_id = ili.get("Id")
#             product_id = ili.get("Product__c")

#             if not product_id:
#                 logger.warning(f"⚠️ Custom Invoice Line Item {ili_id} has no Product__c. Skipping.")
#                 continue

#             line_name = ili.get("Name") or ili_id
#             billed_impressions = _to_decimal(ili.get("Billed_Impressions__c"))
#             billed_amount = _to_decimal(ili.get("Price__c") or ili.get("Line_Amount__c"))
#             pricing_model = ili.get("Pricing_Model__c") or "CPM"
#             effective_rate = _to_decimal(ili.get("eCPM__c"))
#             period_start = ili.get("Start_Date__c")
#             period_end = ili.get("End_Date__c")

#             matching_order_items = product_to_orderitems.get(product_id, [])
#             if not matching_order_items:
#                 logger.warning(
#                     f"⚠️ No matching OrderItems found for product {product_id} on invoice line {ili_id}"
#                 )
#                 continue

#             product_name = None
#             oi_numbers = []
#             for oi in matching_order_items:
#                 if oi.get("Product2") and oi["Product2"].get("Name"):
#                     product_name = oi["Product2"]["Name"]
#                 if oi.get("OrderItemNumber"):
#                     oi_numbers.append(oi.get("OrderItemNumber"))

#             product_name = product_name or line_name
#             matching_oi_ids = [oi["Id"] for oi in matching_order_items if oi.get("Id")]
#             in_clause = _safe_in_clause(matching_oi_ids)

#             if not in_clause:
#                 logger.warning(f"⚠️ No valid OrderItem ids found for invoice line {ili_id}")
#                 continue

#             if not period_start or not period_end:
#                 logger.warning(f"⚠️ Missing invoice line date range for grouped line {ili_id}. Skipping.")
#                 continue

#             logger.info(
#                 f"   Fetching delivery blocks for product: {product_name} "
#                 f"(OrderItems: {', '.join(oi_numbers) if oi_numbers else 'N/A'}) "
#                 f"from {period_start} to {period_end}"
#             )

#             delivery_fields = get_query_fields("Delivery_Data__c")
#             delivery_soql = f"""
#                 SELECT {delivery_fields}
#                 FROM Delivery_Data__c
#                 WHERE Order_Product__c IN ({in_clause})
#                   AND Date__c >= {period_start}
#                   AND Date__c <= {period_end}
#                 ORDER BY Date__c ASC
#             """
#             delivery_results = sf_client.sf.query_all(delivery_soql)
#             delivery_records = delivery_results.get("records", [])

#             item_data = {
#                 "ili_id": ili_id,
#                 "ili_name": product_name,
#                 "product_id": product_id,
#                 "oli_ids": matching_oi_ids,
#                 "oli_names": oi_numbers,
#                 "pricing_model": pricing_model,
#                 "effective_rate": effective_rate,
#                 "billed_impressions": billed_impressions,
#                 "billed_amount": billed_amount,
#                 "period_start": period_start,
#                 "period_end": period_end,
#                 "daily_blocks": []
#             }

#             for rec in delivery_records:
#                 order_product_id = rec.get("Order_Product__c")
#                 order_details = orderitem_details_map.get(order_product_id, {})

#                 item_data["daily_blocks"].append({
#     "date": rec.get("Date__c"),
#     "gross": _to_decimal(rec.get("Gross__c")),
#     "ivt": _to_decimal(rec.get("IVT__c"), "0") / Decimal("100") if rec.get("IVT__c") is not None else Decimal("0"),
#     "viewability": _to_decimal(rec.get("Viewability__c"), "0") / Decimal("100") if rec.get("Viewability__c") is not None else Decimal("0"),
#     "orderProductId": order_product_id,
#     "orderLineItemNumber": order_details.get("orderItemNumber") or "N/A",
#     "quoteLineItemId": order_details.get("quoteLineItemId"),
#     "quoteLineNumber": order_details.get("quoteLineNumber") or "N/A",
#     "orderLineRate": _to_decimal(order_details.get("rate")),
#     "orderLinePricingModel": order_details.get("pricingModel") or pricing_model
# })

#             delivery_info["line_items"].append(item_data)
#             logger.info(f"      ✅ Added {len(delivery_records)} delivery block(s) for grouped product line {product_name}")

#     except Exception as e:
#         logger.error(f"❌ Salesforce query error: {e}")
#         state["error"] = f"Salesforce query error: {str(e)}"

#     state["delivery_data"] = delivery_info
#     return state


# async def calculate_node(state: ReconcillationState) -> ReconcillationState:
#     """
#     Calculate grouped metrics.
#     IMPORTANT: revenue is calculated using actual row-level OrderItem rate.
#     """
#     logger.info("📊 [Reconciliation] Calculating grouped line metrics...")

#     data = state.get("delivery_data", {})
#     line_items = data.get("line_items", [])

#     total_metrics = {
#         "total_gross": Decimal("0.0"),
#         "total_invalid": Decimal("0.0"),
#         "total_valid": Decimal("0.0"),
#         "total_billable_viewable": Decimal("0.0"),
#         "line_metrics": []
#     }

#     for item in line_items:
#         blocks = item.get("daily_blocks", [])

#         gross = Decimal("0.0")
#         invalid = Decimal("0.0")
#         valid_impressions = Decimal("0.0")
#         billable_viewable = Decimal("0.0")
#         calculated_revenue = Decimal("0.0")

#         for b in blocks:
#             row_gross = _to_decimal(b.get("gross"))
#             row_ivt = _to_decimal(b.get("ivt"))
#             row_viewability = _to_decimal(b.get("viewability"))
#             row_rate = b.get("orderLineRate") or _to_decimal(item.get("effective_rate"))
#             row_pricing_model = b.get("orderLinePricingModel") or item.get("pricing_model", "CPM")

#             row_invalid = row_gross * row_ivt
#             row_valid = row_gross - row_invalid
#             row_billable_viewable = row_valid * row_viewability

#             if row_pricing_model == "CPM":
#                 row_revenue = (row_billable_viewable / Decimal("1000")) * row_rate
#             else:
#                 row_revenue = row_billable_viewable * row_rate

#             gross += row_gross
#             invalid += row_invalid
#             valid_impressions += row_valid
#             billable_viewable += row_billable_viewable
#             calculated_revenue += row_revenue

#         ivt_pct = (invalid / gross) if gross > 0 else Decimal("0")
#         viewability_pct = (billable_viewable / valid_impressions) if valid_impressions > 0 else Decimal("0")

#         item_metrics = {
#             "ili_id": item.get("ili_id"),
#             "ili_name": item.get("ili_name"),
#             "product_id": item.get("product_id"),
#             "oli_ids": item.get("oli_ids", []),
#             "oli_names": item.get("oli_names", []),
#             "effective_rate": _to_decimal(item.get("effective_rate")),
#             "pricing_model": item.get("pricing_model", "CPM"),
#             "billed_impressions": _to_decimal(item.get("billed_impressions")),
#             "billed_amount": _to_decimal(item.get("billed_amount")),
#             "gross": gross,
#             "invalid": invalid,
#             "ivt_pct": ivt_pct,
#             "viewability_pct": viewability_pct,
#             "valid_impressions": valid_impressions,
#             "billable_viewable": billable_viewable,
#             "calculated_impressions": billable_viewable,
#             "calculated_revenue": calculated_revenue,
#         }

#         total_metrics["line_metrics"].append(item_metrics)
#         total_metrics["total_gross"] += gross
#         total_metrics["total_invalid"] += invalid
#         total_metrics["total_valid"] += valid_impressions
#         total_metrics["total_billable_viewable"] += billable_viewable

#     total_metrics["avg_ivt_pct"] = (
#         total_metrics["total_invalid"] / total_metrics["total_gross"]
#         if total_metrics["total_gross"] > 0 else Decimal("0")
#     )

#     total_metrics["avg_viewability_pct"] = (
#         total_metrics["total_billable_viewable"] / total_metrics["total_valid"]
#         if total_metrics["total_valid"] > 0 else Decimal("0")
#     )

#     state["monthly_metrics"] = total_metrics
#     logger.info(f"✅ Grouped monthly metrics calculated: {total_metrics}")
#     return state


# async def amendment_node(state: ReconcillationState) -> ReconcillationState:
#     """
#     Compare grouped billed amount vs grouped calculated revenue.
#     """
#     logger.info("📑 [Reconciliation] Checking grouped invoice variance vs delivery...")

#     metrics = state.get("monthly_metrics", {})
#     delivery_data = state.get("delivery_data", {})
#     line_results = []

#     total_calculated_revenue = Decimal("0.0")
#     total_billed_revenue = Decimal("0.0")

#     for item_metrics, item_raw in zip(metrics.get("line_metrics", []), delivery_data.get("line_items", [])):
#         calculated_imp = _to_decimal(item_metrics.get("calculated_impressions"))
#         calculated_revenue = _to_decimal(item_metrics.get("calculated_revenue"))
#         billed_revenue = _to_decimal(item_metrics.get("billed_amount"))
#         variance = calculated_revenue - billed_revenue

#         tolerance = max(Decimal("0.01"), abs(billed_revenue) * Decimal("0.00001"))

#         status = "Ok"
#         if variance > tolerance:
#             status = "Underbilled (Potential Leakage)"
#         elif variance < -tolerance:
#             status = "Overbilled (Customer Disputed)"

#         line_results.append({
#             "ili_name": item_metrics.get("ili_name"),
#             "product_id": item_raw.get("product_id"),
#             "oli_ids": item_raw.get("oli_ids", []),
#             "oli_names": item_raw.get("oli_names", []),
#             "pricing_model": item_raw.get("pricing_model", "CPM"),
#             "effective_rate": _to_decimal(item_raw.get("effective_rate")),
#             "calculated_impressions": calculated_imp,
#             "billed_impressions": _to_decimal(item_metrics.get("billed_impressions")),
#             "calculated_revenue": calculated_revenue,
#             "billed_revenue": billed_revenue,
#             "variance": variance,
#             "status": status
#         })

#         total_calculated_revenue += calculated_revenue
#         total_billed_revenue += billed_revenue

#     state["amendment_results"] = {
#         "line_results": line_results,
#         "calculated_total_revenue": total_calculated_revenue,
#         "billed_total_revenue": total_billed_revenue
#     }

#     return state


# async def variance_node(state: ReconcillationState) -> ReconcillationState:
#     logger.info("📉 [Reconciliation] Calculating overall invoice variance...")

#     results = state.get("amendment_results", {})
#     correct = _to_decimal(results.get("calculated_total_revenue"))
#     billed = _to_decimal(results.get("billed_total_revenue"))
#     variance = correct - billed

#     tolerance = max(Decimal("0.01"), abs(billed) * Decimal("0.00001"))

#     if abs(variance) < Decimal("0.005"):
#         variance = Decimal("0.00")

#     state["variance_results"] = {
#         "variance": variance,
#         "status": "Underbilled" if variance > tolerance else "Overbilled" if variance < -tolerance else "Ok",
#         "leakage_detected": variance > tolerance
#     }

#     return state

# async def summary_response_node(state: ReconcillationState) -> ReconcillationState:
#     logger.info("💬 [Reconciliation] Generating summary response...")

#     error = state.get("error")
#     user_goal = state.get("user_goal", "")
#     variance = state.get("variance_results", {})
#     metrics = state.get("monthly_metrics", {})
#     amendment = state.get("amendment_results", {})

#     system_prompt = None
#     user_prompt = None

#     # try:
#     #     prompt_meta = fetch_prompt_metadata("summary_response_node", "Reconciliation Agent")
#     #
#     #     if prompt_meta and prompt_meta.get("prompt"):
#     #         system_prompt = resolve_placeholders(
#     #             prompt_meta["prompt"],
#     #             prompt_meta.get("configs", {}),
#     #             state
#     #         )
#     #         user_prompt = ""
#     #         logger.info("✅ Using prompt metadata for summary_response_node")
#     #     else:
#     #         logger.warning("⚠️ Prompt metadata not found. Using fallback prompt.")
#     #
#     # except Exception as e:
#     #     logger.warning(f"⚠️ Failed to fetch/resolve prompt metadata. Using fallback prompt. Error: {e}")

#     if not system_prompt:
#         if error:
#             system_prompt = (
#                 "You are a specialized Reconciliation Agent. Briefly explain why the reconciliation could not be completed."
#             )
#             user_prompt = (
#                 f"User Question: {user_goal}\n"
#                 f"Error Encountered: {error}\n\n"
#                 f"Please explain to the user that we couldn't complete the validation and why."
#             )
#         else:
#             system_prompt = (
#                 "You are a specialized Reconciliation Agent. Provide a very brief summary of the invoice vs expected delivery. "
#                 "Do NOT explain line-by-line math. Only tell the user whether the invoice is good to proceed, Underbilled, or Overbilled, "
#                 "and direct them to click 'View Details' for the full breakdown."
#             )
#             user_prompt = f"""
# User Question: {user_goal}

# Reconciliation Data:
# - Total Gross Impressions: {metrics.get('total_gross', 0):,.0f}
# - Total Valid Impressions: {metrics.get('total_valid', 0):,.0f}
# - Total Billable Viewable Impressions: {metrics.get('total_billable_viewable', 0):,.0f}
# - Billed Revenue (Invoice): ₹{amendment.get('billed_total_revenue', 0):,.2f}
# - Calculated Valid Revenue: ₹{amendment.get('calculated_total_revenue', 0):,.2f}
# - Variance: ₹{variance.get('variance', 0):,.2f}
# - Status: {variance.get('status', 'Unknown')}
# """
#         logger.info("✅ Using fallback prompt for summary_response_node")

#     try:
#         response = await call_llm(
#             system_prompt=system_prompt,
#             user_prompt=user_prompt,
#             default_model="gpt-4o",
#             default_provider="openai",
#             default_temperature=0
#         )
#         state["final_response"] = response.strip()

#         status_raw = variance.get("status", "Unknown")
#         variant = "success" if status_raw == "Ok" else "error"
#         line_items_card = []

#         for idx, res in enumerate(amendment.get("line_results", []), start=1):
#             ili_name = res.get("ili_name")
#             product_id = res.get("product_id")
#             effective_rate = _to_float(res.get("effective_rate"))
#             date_str = "N/A"
#             daily_blocks_enriched = []
#             billed_impressions = 0.0
#             period_start = "N/A"
#             period_end = "N/A"
#             oli_names = res.get("oli_names", [])

#             for item in state.get("delivery_data", {}).get("line_items", []):
#                 if item.get("product_id") == product_id:
#                     billed_impressions = _to_float(item.get("billed_impressions"))
#                     period_start = item.get("period_start", "N/A")
#                     period_end = item.get("period_end", "N/A")

#                     dates = [b.get("date") for b in item.get("daily_blocks", []) if b.get("date")]
#                     if dates:
#                         date_str = f"{min(dates)} to {max(dates)}"

#                     for b in item.get("daily_blocks", []):
#                         gross = _to_float(b.get("gross"))
#                         ivt_pct = _to_float(b.get("ivt"))
#                         viewability_pct = _to_float(b.get("viewability"))
#                         row_rate = _to_float(b.get("orderLineRate"), effective_rate)
#                         row_pricing_model = b.get("orderLinePricingModel") or item.get("pricing_model", "CPM")

#                         invalid_impressions = gross * ivt_pct
#                         valid_impressions = gross - invalid_impressions
#                         viewable_impressions = valid_impressions * viewability_pct

#                         if row_pricing_model == "CPM":
#                             daily_revenue = (viewable_impressions / 1000) * row_rate
#                         else:
#                             daily_revenue = viewable_impressions * row_rate

#                         daily_blocks_enriched.append({
#                             "date": b.get("date"),
#                             "gross": gross,
#                             "ivtPct": ivt_pct * 100,
#                             "viewabilityPct": viewability_pct * 100,
#                             "invalidImpressions": invalid_impressions,
#                             "validImpressions": valid_impressions,
#                             "viewableImpressions": viewable_impressions,
#                             "orderLineItem": b.get("orderLineItemNumber") or "N/A",
#                             "pricingModel": row_pricing_model,
#                             "rate": row_rate,
#                             "revenue": daily_revenue,
#                             "productName": ili_name,
#                             "description": f"Billable Viewable: {viewable_impressions:,.3f}, Revenue: ₹{daily_revenue:,.2f} (Rate: ₹{row_rate:,.4f})"
#                         })
#                     break

#             line_variance = _to_decimal(res.get("variance"))
#             gross_total = sum(_to_decimal(b["gross"]) for b in daily_blocks_enriched)
#             invalid_total = sum(_to_decimal(b["invalidImpressions"]) for b in daily_blocks_enriched)
#             valid_total = sum(_to_decimal(b["validImpressions"]) for b in daily_blocks_enriched)
#             viewable_total = sum(_to_decimal(b["viewableImpressions"]) for b in daily_blocks_enriched)
#             calculated_revenue = _to_decimal(res.get("calculated_revenue"))
#             billed_revenue = _to_decimal(res.get("billed_revenue"))

#             segment_map = {}
#             for b in daily_blocks_enriched:
#                 oli_num = b.get("orderLineItem") or "N/A"
#                 seg = segment_map.setdefault(oli_num, {
#                     "rate": _to_decimal(b.get("rate")),
#                     "pricingModel": b.get("pricingModel"),
#                     "gross": Decimal("0.0"),
#                     "invalid": Decimal("0.0"),
#                     "valid": Decimal("0.0"),
#                     "viewable": Decimal("0.0"),
#                     "revenue": Decimal("0.0"),
#                     "dates": []
#                 })
#                 seg["gross"] += _to_decimal(b.get("gross"))
#                 seg["invalid"] += _to_decimal(b.get("invalidImpressions"))
#                 seg["valid"] += _to_decimal(b.get("validImpressions"))
#                 seg["viewable"] += _to_decimal(b.get("viewableImpressions"))
#                 seg["dates"].append(b.get("date"))

#                 if seg["pricingModel"] == "CPM":
#                     seg["revenue"] += (_to_decimal(b.get("viewableImpressions")) / Decimal("1000")) * _to_decimal(b.get("rate"))
#                 else:
#                     seg["revenue"] += _to_decimal(b.get("viewableImpressions")) * _to_decimal(b.get("rate"))

#             segment_lines = []
#             for oli_num, seg in segment_map.items():
#                 seg_start = min([d for d in seg["dates"] if d]) if seg["dates"] else "N/A"
#                 seg_end = max([d for d in seg["dates"] if d]) if seg["dates"] else "N/A"
#                 segment_lines.append(
#                     f"{oli_num}: {seg_start} to {seg_end} at ₹{seg['rate']:,.2f} {seg['pricingModel']} → Revenue ₹{seg['revenue']:,.2f}"
#                 )

#             segment_text = "\n".join(segment_lines) if segment_lines else "No amendment segments found."

#             billed_imps_val = _to_decimal(res.get("billed_impressions"))
#             calc_imps_val = _to_decimal(res.get("calculated_impressions"))
#             imp_diff_pct = abs(billed_imps_val - calc_imps_val) / billed_imps_val if billed_imps_val > 0 else Decimal("0")
#             is_rate_mismatch = imp_diff_pct < Decimal("0.0001") and abs(line_variance) > Decimal("0.01")

#             if abs(line_variance) < Decimal("0.01"):
#                 description = (
#                     f"Status: Ok.\n\n"
#                     f"Gross Impressions: {gross_total:,.0f}\n"
#                     f"Invalid Traffic Removed: {invalid_total:,.0f}\n"
#                     f"Valid Impressions: {valid_total:,.0f}\n"
#                     f"Billable Viewable Impressions: {viewable_total:,.3f}\n\n"
#                     f"Effective CPM: ₹{effective_rate:,.4f} {res.get('pricing_model', 'CPM')}\n"
#                     f"Calculated Revenue: ₹{calculated_revenue:,.2f}\n"
#                     f"Billed Revenue: ₹{billed_revenue:,.2f}\n"
#                     f"Variance: ₹{line_variance:,.2f}\n\n"
#                     f"Amendment Breakdown:\n{segment_text}\n\n"
#                     f"This grouped product line is ok because the billed amount exactly matches the revenue derived "
#                     f"from verified delivery data after IVT and viewability adjustments."
#                 )
#             elif line_variance > 0:
#                 rate_reason = " (primarily due to a rate mismatch)" if is_rate_mismatch else ""
#                 description = (
#                     f"Status: Underbilled.\n\n"
#                     f"Gross Impressions: {gross_total:,.0f}\n"
#                     f"Invalid Traffic Removed: {invalid_total:,.0f}\n"
#                     f"Valid Impressions: {valid_total:,.0f}\n"
#                     f"Billable Viewable Impressions: {viewable_total:,.3f}\n\n"
#                     f"Effective CPM: ₹{effective_rate:,.4f} {res.get('pricing_model', 'CPM')}\n"
#                     f"Calculated Revenue: ₹{calculated_revenue:,.2f}\n"
#                     f"Billed Revenue: ₹{billed_revenue:,.2f}\n"
#                     f"Variance: ₹{line_variance:,.2f}\n\n"
#                     f"Amendment Breakdown:\n{segment_text}\n\n"
#                     f"This grouped product line is underbilled{rate_reason} because the delivery-derived revenue is higher than the billed amount."
#                 )
#             else:
#                 rate_reason = " (primarily due to a rate mismatch)" if is_rate_mismatch else ""
#                 description = (
#                     f"Status: Overbilled.\n\n"
#                     f"Gross Impressions: {gross_total:,.0f}\n"
#                     f"Invalid Traffic Removed: {invalid_total:,.0f}\n"
#                     f"Valid Impressions: {valid_total:,.0f}\n"
#                     f"Billable Viewable Impressions: {viewable_total:,.3f}\n\n"
#                     f"Effective CPM: ₹{effective_rate:,.4f} {res.get('pricing_model', 'CPM')}\n"
#                     f"Calculated Revenue: ₹{calculated_revenue:,.2f}\n"
#                     f"Billed Revenue: ₹{billed_revenue:,.2f}\n"
#                     f"Variance: ₹{line_variance:,.2f}\n\n"
#                     f"Amendment Breakdown:\n{segment_text}\n\n"
#                     f"This grouped product line is overbilled{rate_reason} because the billed amount is higher than the revenue "
#                     f"derived from verified delivery data after IVT and viewability adjustments."
#                 )

#             line_items_card.append({
#                 "lineNumber": idx,
#                 "name": ili_name,
#                 "effectiveRate": _to_float(effective_rate),
#                 "rate": _to_float(effective_rate),
#                 "dates": date_str,
#                 "periodStart": period_start,
#                 "periodEnd": period_end,
#                 "revenue": _to_float(calculated_revenue),
#                 "billedRevenue": _to_float(billed_revenue),
#                 "billedImpressions": _to_float(billed_impressions),
#                 "calculatedImpressions": _to_float(res.get("calculated_impressions")),
#                 "pricingModel": res.get("pricing_model"),
#                 "status": res.get("status"),
#                 "orderLineItems": oli_names,
#                 "grossImpressions": _to_float(gross_total),
#                 "invalidImpressions": _to_float(invalid_total),
#                 "validImpressions": _to_float(valid_total),
#                 "viewableImpressions": _to_float(viewable_total),
#                 "dailyBlocks": daily_blocks_enriched,
#                 "description": description
#             })

#         state["structured_summary"] = {
#             "status": status_raw,
#             "statusVariant": variant,
#             "currencyCode": "INR",
#             "currencySymbol": "₹",
#             "totalImpressions": _to_float(state.get("monthly_metrics", {}).get("total_gross")),
#             "totalValidImpressions": _to_float(state.get("monthly_metrics", {}).get("total_valid")),
#             "totalViewableImpressions": _to_float(state.get("monthly_metrics", {}).get("total_billable_viewable")),
#             "totalRevenue": _to_float(amendment.get("calculated_total_revenue")),
#             "totalBilled": _to_float(amendment.get("billed_total_revenue")),
#             "variance": _to_float(variance.get("variance")),
#             "lineItems": line_items_card,
#             "invoiceId": state.get("invoice_data", {}).get("id", "Unknown"),
#             "invoiceName": state.get("invoice_data", {}).get("name", "Unknown"),
#             "invoiceDate": state.get("invoice_data", {}).get("invoice_date"),
#             "invoiceStartDate": state.get("invoice_data", {}).get("start"),
#             "invoiceEndDate": state.get("invoice_data", {}).get("end"),
#             "advertiserName": state.get("invoice_data", {}).get("advertiser_name", "Unknown"),
#             "advertiserId": state.get("invoice_data", {}).get("advertiser_id"),
#             "orderId": state.get("record_id", "Unknown")
#         }

#         logger.info(f"✅ Structured summary populated: {variant}")

#     except Exception as e:
#         logger.error(f"Error in summary generation: {e}")
#         if error:
#             state["final_response"] = f"Technical Error: {error}. I was unable to complete the reconciliation process."
#         else:
#             state["final_response"] = (
#                 f"Reconciliation Complete. Status: {variance.get('status', 'Unknown')}. "
#                 f"Variance: ₹{_to_float(variance.get('variance')):,.2f}."
#             )

#     state["next_action"] = "complete"
#     return state


# def should_continue(state: ReconcillationState) -> str:
#     if state.get("error"):
#         logger.warning(f"🚨 Error detected in graph state: {state['error']}. Routing to summary.")
#         return "summary"
#     return "continue"


# def build_reconcillation_graph(checkpointer=None):
#     workflow = StateGraph(ReconcillationState)

#     workflow.add_node("fetchdeliverydata", fetch_delivery_data_node)
#     workflow.add_node("Calculate", calculate_node)
#     workflow.add_node("Amendment", amendment_node)
#     workflow.add_node("Variance", variance_node)
#     workflow.add_node("summaryresponse", summary_response_node)

#     workflow.set_entry_point("fetchdeliverydata")

#     workflow.add_conditional_edges(
#         "fetchdeliverydata",
#         should_continue,
#         {
#             "continue": "Calculate",
#             "summary": "summaryresponse"
#         }
#     )

#     workflow.add_conditional_edges(
#         "Calculate",
#         should_continue,
#         {
#             "continue": "Amendment",
#             "summary": "summaryresponse"
#         }
#     )

#     workflow.add_conditional_edges(
#         "Amendment",
#         should_continue,
#         {
#             "continue": "Variance",
#             "summary": "summaryresponse"
#         }
#     )

#     workflow.add_edge("Variance", "summaryresponse")
#     workflow.add_edge("summaryresponse", END)

#     return workflow.compile(checkpointer=checkpointer)