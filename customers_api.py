from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from db import supabase

router = APIRouter(prefix="/api/admin", tags=["Customers"])

class CustomerUpdateModel(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    mac_address: Optional[str] = None

@router.get("/customers")
def get_customers():
    """Fetch all customers for the CRM"""
    try:
        res = supabase.table("customers").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch customers")

@router.patch("/customers/{customer_id}")
def update_customer(customer_id: str, data: CustomerUpdateModel):
    """Update a customer's name, status, or MAC address"""
    update_data = {}
    if data.name is not None: update_data["name"] = data.name
    if data.status is not None: update_data["status"] = data.status
    if data.mac_address is not None: update_data["mac_address"] = data.mac_address

    try:
        res = supabase.table("customers").update(update_data).eq("id", customer_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Customer not found")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/customers/{customer_id}")
def delete_customer(customer_id: str):
    """Delete a customer"""
    try:
        supabase.table("customers").delete().eq("id", customer_id).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to delete customer")