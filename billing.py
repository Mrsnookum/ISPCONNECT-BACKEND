import os
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import httpx

from db import supabase

router = APIRouter(prefix="/api/billing", tags=["Billing"])

class PaymentRequest(BaseModel):
    phone: str
    email: str  # We pass the email to link the payment to the correct admin account

@router.post("/stk-push")
async def trigger_stk_push(req: PaymentRequest):
    url = "https://backend.payhero.co.ke/api/v2/payments"
    
    # Render automatically sets RENDER_EXTERNAL_URL. If not available, use your fallback.
    backend_url = os.getenv("RENDER_EXTERNAL_URL", "https://ispconnect-api.onrender.com")

    payload = {
        "amount": 2,
        "phone_number": req.phone,
        "channel_id": int(os.getenv("PAYHERO_CHANNEL_ID")),
        "provider": "m-pesa",
        "external_reference": req.email, # Using email as reference to identify who paid
        # UPDATE THIS: Now points dynamically to your live production server
        "callback_url": f"{backend_url}/api/billing/webhook" 
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": os.getenv("PAYHERO_API_KEY") 
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
        except Exception as e:
            print(f"Network request failed: {repr(e)}")
            raise HTTPException(status_code=500, detail="Payment gateway is unreachable.")
            
        # 1. Print the exact PayHero response to the terminal
        print(f"PAYHERO STK PUSH RESPONSE: {response.status_code}")
        print(response.text)

        # 2. Stop the process if PayHero rejects the request
        if response.status_code not in (200, 201):
            try:
                error_data = response.json()
                error_msg = error_data.get("error_message", "Unknown PayHero Error")
            except:
                error_msg = response.text
            raise HTTPException(status_code=400, detail=f"PayHero is currently down: {error_msg}")

        # 3. If successful, save to Supabase
        supabase.table("payments").insert({
            "phone": req.phone,
            "amount": 2,
            "status": "pending"
        }).execute()

        return response.json()

@router.post("/webhook")
async def payhero_callback(request: Request):
    """PayHero hits this endpoint automatically when the user enters their PIN."""
    data = await request.json()
    
    print("PAYHERO WEBHOOK RECEIVED:")
    print(data)
    
    response_data = data.get("response", {})

    if response_data.get("Status") == "Success":
        admin_email = response_data.get("ExternalReference")

        if admin_email:
            # 1. Flip the master admin to active
            supabase.table("admins").update({"is_active": True}).eq("email", admin_email).execute()

            # 2. Fetch only the SINGLE most recent pending payment to update
            recent_payment = supabase.table("payments").select("id").eq("phone", response_data.get("Phone")).eq("status", "pending").order("created_at", desc=True).limit(1).execute()
            
            if recent_payment.data:
                payment_id = recent_payment.data[0]["id"]
                supabase.table("payments").update({
                    "status": "completed",
                    "mpesa_receipt_number": response_data.get("MpesaReceiptNumber"),
                    "raw_callback": data
                }).eq("id", payment_id).execute()
            
    elif response_data.get("Status") == "Failed":
        print(f"M-Pesa Error: {response_data.get('ResultDesc')}")
        
        recent_payment = supabase.table("payments").select("id").eq("phone", response_data.get("Phone")).eq("status", "pending").order("created_at", desc=True).limit(1).execute()
        
        if recent_payment.data:
            payment_id = recent_payment.data[0]["id"]
            supabase.table("payments").update({
                "status": "failed",
                "raw_callback": data
            }).eq("id", payment_id).execute()

    return {"status": "received"}

@router.get("/status/{phone}")
def check_payment_status(phone: str):
    """Allows the frontend to poll for payment completion."""
    # Fetch the most recent payment attempt for this phone number
    response = supabase.table("payments").select("status").eq("phone", phone).order("created_at", desc=True).limit(1).execute()
    
    if response.data:
        return {"status": response.data[0]["status"]}
    
    return {"status": "not_found"}