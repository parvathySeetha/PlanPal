import logging
import json
from decimal import Decimal
from agents.Reconciliation.state import ReconcillationState
from core.helper import ensure_sf_connected, _get_nested_value
from .utils import (
    sf_client,
    generate_semantic_mapping,
    resolve_object_name,
    get_query_fields,
    _to_decimal,
    _safe_in_clause
)

logger = logging.getLogger(__name__)

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
