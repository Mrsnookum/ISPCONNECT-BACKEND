from fastapi import APIRouter, HTTPException
from db import supabase

router = APIRouter(prefix="/api/admin", tags=["Payments Ledger"])

@router.get("/payments-ledger")
def get_payments_ledger():
    """Fetch all payments to display in the ledger table."""
    try:
        # Fetch all payments ordered by the newest transactions first
        res = supabase.table("payments").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        print(f"Error fetching ledger: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch payments ledger")