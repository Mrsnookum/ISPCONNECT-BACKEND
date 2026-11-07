import os
import requests
import string
import random
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from db import supabase
import routeros_api

router = APIRouter(prefix="/api/portal", tags=["Portal"])

class PaymentRequest(BaseModel):
    phone: str
    package_id: str
    mac: str = ""
    ip: str = ""

def generate_voucher(length=6):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def inject_voucher_to_mikrotik(router_ip: str, voucher_code: str, duration_seconds: int, mac_address: str = ""):
    print(f"🔄 Injecting {voucher_code} into {router_ip}...")
    try:
        router_res = supabase.table("routers").select("identity").eq("ip_address", router_ip).execute()
        if not router_res.data:
            print(f"❌ Cannot inject: No router found in DB with IP {router_ip}")
            return
            
        router_identity = router_res.data[0]["identity"]
        
        token_res = supabase.table("provisioning_tokens").select("token").eq("router_identity", router_identity).order("created_at", desc=True).limit(1).execute()
        if not token_res.data:
            print(f"❌ Cannot inject: No token found for {router_identity}")
            return

        router_password = token_res.data[0]["token"]
        
        connection = routeros_api.RouterOsApiPool(
            router_ip, 
            username="ispconnect_api", 
            password=router_password, 
            plaintext_login=True
        )
        api = connection.get_api()

        uptime_limit = f"{duration_seconds}s"
        
        user_params = {
            "name": voucher_code,
            "password": voucher_code,
            "profile": "default",
            "limit-uptime": uptime_limit
        }
        
        if mac_address:
            user_params["mac-address"] = mac_address

        api.get_resource('/ip/hotspot/user').add(**user_params)
        connection.disconnect()
        print(f"✅ INJECTED: {voucher_code} (MAC: {mac_address}). Expiry: {uptime_limit}.")

    except Exception as e:
        print(f"🔥 MikroTik Injection Error: {str(e)}")

@router.get("/packages")
def get_public_packages():
    try:
        res = supabase.table("packages").select("*").eq("is_active", True).order("price").execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/pay")
def trigger_payhero_payment(req: PaymentRequest):
    pkg_res = supabase.table("packages").select("price, name").eq("id", req.package_id).execute()
    if not pkg_res.data:
        raise HTTPException(status_code=404, detail="Package not found")

    amount = pkg_res.data[0]['price']
    payhero_api_key = os.getenv("PAYHERO_API_KEY")
    payhero_channel_id = os.getenv("PAYHERO_CHANNEL_ID")

    try:
        supabase.table("payments").insert({
            "package_id": req.package_id,
            "amount": amount,
            "phone": req.phone, 
            "status": "pending",
            "notes": f"RouterIP:{req.ip}|MAC:{req.mac}" 
        }).execute()

        payhero_url = "https://backend.payhero.co.ke/api/v2/payments"
        headers = {
            "Content-Type": "application/json",
            "Authorization": payhero_api_key 
        }
        
        backend_url = os.getenv("RENDER_EXTERNAL_URL", "https://ispconnect-backend.onrender.com")
        payload = {
            "amount": amount,
            "phone_number": req.phone,
            "channel_id": int(payhero_channel_id),
            "provider": "m-pesa",
            "external_reference": req.package_id,
            "callback_url": f"{backend_url}/api/portal/callback" 
        }

        response = requests.post(payhero_url, json=payload, headers=headers)
        if response.status_code not in [200, 201]:
            raise HTTPException(status_code=response.status_code, detail="PayHero API rejected")

        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Payment failed")

@router.get("/verify/{phone}")
def verify_payment_status(phone: str):
    try:
        five_mins_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        res = supabase.table("subscriptions") \
            .select("username, customers!inner(phone)") \
            .eq("customers.phone", phone) \
            .eq("status", "active") \
            .gte("created_at", five_mins_ago) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        
        if res.data:
            return {"status": "success", "voucher": res.data[0]["username"]}
        return {"status": "pending"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Verification failed")

@router.post("/callback")
async def payhero_callback(request: Request):
    try:
        data = await request.json()
        print(f"\n--- 💸 WEBHOOK RECEIVED ---")

        payment_data = data.get("response", {})
        result_code = payment_data.get("ResultCode")
        
        if str(result_code) != "0":
            print(f"❌ Webhook Ignored: Payment failed/cancelled (Code: {result_code}).")
            return {"status": "ignored", "message": "Payment failed"}

        phone = payment_data.get("Phone")
        amount = payment_data.get("Amount")
        raw_receipt = payment_data.get("MpesaReceiptNumber") 
        receipt = raw_receipt if raw_receipt else f"TEST_{uuid.uuid4().hex[:8].upper()}"
        package_id = payment_data.get("ExternalReference") 

        if not phone or not package_id:
            return {"status": "error", "message": "Missing data"}

        pkg_res = supabase.table("packages").select("*").eq("id", package_id).execute()
        if not getattr(pkg_res, 'data', None):
            return {"status": "error", "message": "Package not found"}
        
        duration_seconds = pkg_res.data[0].get('duration_seconds', 3600)
        now = datetime.utcnow()

        pending_res = supabase.table("payments").select("id, notes").eq("phone", phone).eq("status", "pending").order("created_at", desc=True).limit(1).execute()
        router_ip = ""
        mac_address = ""
        payment_id = None
        
        if pending_res.data:
            payment_id = pending_res.data[0]['id']
            notes = pending_res.data[0].get('notes', '')
            if "RouterIP:" in notes:
                router_ip = notes.split("RouterIP:")[1].split("|")[0]
            if "MAC:" in notes:
                mac_address = notes.split("MAC:")[1]

        customer_res = supabase.table("customers").select("id").eq("phone", phone).execute()
        if getattr(customer_res, 'data', None):
            customer_id = customer_res.data[0]['id']
            if mac_address:
                supabase.table("customers").update({"mac_address": mac_address}).eq("id", customer_id).execute()
        else:
            new_cust_data = {"phone": phone, "name": "Portal User", "status": "active"}
            if mac_address:
                new_cust_data["mac_address"] = mac_address
            new_cust = supabase.table("customers").insert(new_cust_data).execute()
            customer_id = new_cust.data[0]['id']

        if payment_id:
            supabase.table("payments").update({
                "customer_id": customer_id,
                "mpesa_receipt_number": receipt, 
                "status": "completed",
                "paid_at": now.isoformat()
            }).eq("id", payment_id).execute()

        voucher_code = generate_voucher()
        expires_at = now + timedelta(seconds=duration_seconds)

        supabase.table("subscriptions").insert({
            "customer_id": customer_id,
            "package_id": package_id,
            "username": voucher_code,
            "password": voucher_code,
            "started_at": now.isoformat(), 
            "expires_at": expires_at.isoformat(),
            "status": "active"
        }).execute()
        print(f"🎟️ DB Sub created: {voucher_code}")

        if not router_ip:
            fallback = supabase.table("routers").select("ip_address").limit(1).execute()
            if fallback.data:
                router_ip = fallback.data[0]['ip_address']
                
        if router_ip:
            inject_voucher_to_mikrotik(router_ip, voucher_code, duration_seconds, mac_address)

        print("--- ✅ WEBHOOK COMPLETE --- \n")
        return {"status": "success"}

    except Exception as e:
        print(f"🔥 CRITICAL CALLBACK ERROR: {e}")
        return {"status": "error"}
