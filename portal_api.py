import os
import requests
import string
import random
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
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
    """Generates a random uppercase alphanumeric voucher code."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def inject_voucher_to_mikrotik(router_ip: str, voucher_code: str, duration_seconds: int):
    """Background task: Connects to MikroTik and creates the hotspot user."""
    print(f"🔄 Injecting voucher {voucher_code} to router {router_ip}...")
    try:
        # 1. DYNAMICALLY FIND THE CORRECT ROUTER BY IP
        router_res = supabase.table("routers").select("identity").eq("ip_address", router_ip).execute()
        if not router_res.data:
            print(f"❌ Cannot inject: No router found in DB with IP {router_ip}")
            return
            
        router_identity = router_res.data[0]["identity"]
        
        # 2. FETCH THE SPECIFIC TOKEN FOR THIS ROUTER
        token_res = supabase.table("provisioning_tokens").select("token").eq("router_identity", router_identity).order("created_at", desc=True).limit(1).execute()
        
        if not token_res.data:
            print(f"❌ Cannot inject: No provisioning token found for router {router_identity}")
            return

        router_password = token_res.data[0]["token"]
        
        # Connect to the MikroTik API
        connection = routeros_api.RouterOsApiPool(
            router_ip, 
            username="ispconnect_api", 
            password=router_password, 
            plaintext_login=True
        )
        api = connection.get_api()

        # Inject the user into the local Hotspot database
        uptime_limit = f"{duration_seconds}s"
        api.get_resource('/ip/hotspot/user').add(
            name=voucher_code,
            password=voucher_code,
            profile="default",
            **{"limit-uptime": uptime_limit}
        )
        
        connection.disconnect()
        print(f"✅ Voucher {voucher_code} successfully injected into MikroTik {router_identity} ({router_ip})")

    except Exception as e:
        print(f"🔥 MikroTik Injection Error: {str(e)}")

@router.get("/packages")
def get_public_packages():
    """Fetch only active packages for the public captive portal."""
    try:
        # BUG FIX: Only fetch packages that you have marked as ACTIVE in the admin dashboard!
        res = supabase.table("packages").select("*").eq("is_active", True).order("price").execute()
        return res.data or []
    except Exception as e:
        print(f"🔥 Database Error fetching packages: {e}") 
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/pay")
def trigger_payhero_payment(req: PaymentRequest):
    """Trigger M-Pesa STK Push via PayHero API securely."""
    pkg_res = supabase.table("packages").select("price, name").eq("id", req.package_id).execute()
    if not pkg_res.data:
        raise HTTPException(status_code=404, detail="Package not found")

    amount = pkg_res.data[0]['price']

    payhero_api_key = os.getenv("PAYHERO_API_KEY")
    payhero_channel_id = os.getenv("PAYHERO_CHANNEL_ID")

    if not payhero_api_key or not payhero_channel_id:
        raise HTTPException(status_code=500, detail="PayHero API credentials not configured in .env")

    try:
        supabase.table("payments").insert({
            "package_id": req.package_id,
            "amount": amount,
            "phone": req.phone, 
            "status": "pending",
            "notes": f"RouterIP:{req.ip}" 
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
            print(f"PayHero Rejected: {response.text}") 
            raise HTTPException(status_code=response.status_code, detail="PayHero API rejected the request")

        return {"status": "success", "message": "STK Push triggered successfully"}

    except Exception as e:
        print(f"Payment Error: {e}")
        raise HTTPException(status_code=500, detail="Payment processing failed")

@router.get("/verify/{phone}")
def verify_payment_status(phone: str):
    """Checks if a new active subscription exists for this phone number."""
    try:
        res = supabase.table("subscriptions") \
            .select("voucher_code, customers!inner(phone)") \
            .eq("customers.phone", phone) \
            .eq("status", "active") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        
        if res.data:
            return {"status": "success", "voucher": res.data[0]["voucher_code"]}
        return {"status": "pending"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Verification failed")

@router.post("/callback")
async def payhero_callback(request: Request, background_tasks: BackgroundTasks):
    """Receives the payment confirmation from PayHero and automates the CRM."""
    try:
        data = await request.json()
        print(f"\n--- 💸 NEW WEBHOOK RECEIVED ---")

        if not data.get("status"):
            print("❌ Webhook Ignored: Payment status is false")
            return {"status": "ignored", "message": "Payment failed or cancelled"}

        payment_data = data.get("response", {})
        
        phone = payment_data.get("Phone")
        amount = payment_data.get("Amount")
        raw_receipt = payment_data.get("MpesaReceiptNumber") 
        receipt = raw_receipt if raw_receipt else f"TEST_{uuid.uuid4().hex[:8].upper()}"
        package_id = payment_data.get("ExternalReference") 

        if not phone or not package_id:
            print("❌ Webhook Error: Missing phone or package_id")
            return {"status": "error", "message": "Missing crucial data"}

        print(f"🔍 Fetching package ID: {package_id}...")
        pkg_res = supabase.table("packages").select("*").eq("id", package_id).execute()
        if not getattr(pkg_res, 'data', None):
            print("❌ Error: Package not found in database!")
            return {"status": "error", "message": "Package not found"}
        
        duration_seconds = pkg_res.data[0].get('duration_seconds', 3600)

        print(f"🔍 Checking customer with phone: {phone}...")
        customer_res = supabase.table("customers").select("id").eq("phone", phone).execute()
        
        if getattr(customer_res, 'data', None):
            customer_id = customer_res.data[0]['id']
            print(f"✅ Found existing customer: {customer_id}")
        else:
            print("👤 Creating new customer...")
            new_cust = supabase.table("customers").insert({
                "phone": phone,
                "name": "Portal User", 
                "status": "active"
            }).execute()
            customer_id = new_cust.data[0]['id']
            print(f"✅ Created customer: {customer_id}")

        print("💳 Updating payment record...")
        
        pending_res = supabase.table("payments").select("id, notes").eq("phone", phone).eq("status", "pending").order("created_at", desc=True).limit(1).execute()
        router_ip = ""
        
        if pending_res.data:
            payment_id = pending_res.data[0]['id']
            notes = pending_res.data[0].get('notes', '')
            if "RouterIP:" in notes:
                router_ip = notes.split("RouterIP:")[1]
                
            supabase.table("payments").update({
                "customer_id": customer_id,
                "mpesa_receipt_number": receipt, 
                "status": "completed"
            }).eq("id", payment_id).execute()
        else:
            supabase.table("payments").insert({
                "customer_id": customer_id,
                "package_id": package_id,
                "amount": amount,
                "mpesa_receipt_number": receipt, 
                "phone": phone, 
                "status": "completed"
            }).execute()

        print("🎟️ Generating voucher and subscription...")
        voucher_code = generate_voucher()
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=duration_seconds)

        supabase.table("subscriptions").insert({
            "customer_id": customer_id,
            "package_id": package_id,
            "voucher_code": voucher_code,
            "started_at": now.isoformat(), 
            "expires_at": expires_at.isoformat(),
            "status": "active"
        }).execute()
        print(f"✅ Subscription created! Voucher: {voucher_code}")

        if router_ip:
            background_tasks.add_task(inject_voucher_to_mikrotik, router_ip, voucher_code, duration_seconds)
        else:
            print("⚠️ Warning: No Router IP found in payment notes. Voucher created in DB but not injected into router.")

        print("--- ✅ WEBHOOK COMPLETE --- \n")
        return {"status": "success", "message": "User provisioned successfully"}

    except Exception as e:
        print(f"🔥 CRITICAL CALLBACK ERROR: {e}")
        return {"status": "error", "message": str(e)}
