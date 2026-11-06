from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
from db import supabase

router = APIRouter(prefix="/api/admin", tags=["Subscriptions"])

class ExtendSubscriptionModel(BaseModel):
    add_hours: int

@router.get("/subscriptions")
def get_subscriptions():
    """Fetch all subscriptions with linked customer and package data."""
    try:
        # We use Supabase's foreign key joining to get the names automatically
        res = supabase.table("subscriptions").select(
            "*, customers(name, phone), packages(name)"
        ).order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch subscriptions")

@router.patch("/subscriptions/{sub_id}/revoke")
def revoke_subscription(sub_id: str):
    """Instantly kill an active subscription."""
    try:
        # Mark as revoked and set expiration to right now
        now_str = datetime.utcnow().isoformat()
        res = supabase.table("subscriptions").update({
            "status": "revoked",
            "expires_at": now_str
        }).eq("id", sub_id).execute()
        
        if not res.data:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/subscriptions/{sub_id}/extend")
def extend_subscription(sub_id: str, data: ExtendSubscriptionModel):
    """Manually add time to a customer's subscription."""
    try:
        # 1. Get current subscription
        sub_res = supabase.table("subscriptions").select("expires_at").eq("id", sub_id).execute()
        if not sub_res.data:
            raise HTTPException(status_code=404, detail="Not found")
            
        current_expiry = datetime.fromisoformat(sub_res.data[0]['expires_at'].replace("Z", "+00:00"))
        
        # 2. Add the new time
        new_expiry = current_expiry + timedelta(hours=data.add_hours)
        
        # 3. Update the database
        update_res = supabase.table("subscriptions").update({
            "expires_at": new_expiry.isoformat(),
            "status": "active" # Ensure it's marked active if it was expired
        }).eq("id", sub_id).execute()
        
        return update_res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))