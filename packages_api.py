from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from db import supabase

router = APIRouter(prefix="/api/admin", tags=["Packages"])

# Pydantic model to validate incoming data from the frontend
class PackageModel(BaseModel):
    id: Optional[str] = None
    name: str
    type: str
    price: float
    duration_seconds: int
    speed_download_kbps: int
    speed_upload_kbps: int
    devices: int
    is_active: bool

@router.get("/packages")
def get_packages():
    """Fetch all packages to display in the table"""
    res = supabase.table("packages").select("*").order("created_at", desc=True).execute()
    return res.data or []

@router.post("/packages")
def save_package(pkg: PackageModel):
    """Create a new package or update an existing one"""
    data = {
        "name": pkg.name,
        "type": pkg.type,
        "price": pkg.price,
        "duration_seconds": pkg.duration_seconds,
        "speed_download_kbps": pkg.speed_download_kbps,
        "speed_upload_kbps": pkg.speed_upload_kbps,
        "devices": pkg.devices,
        "is_active": pkg.is_active
    }
    
    if pkg.id:
        # If an ID is provided, update the existing package
        res = supabase.table("packages").update(data).eq("id", pkg.id).execute()
    else:
        # No ID means it's a new package to insert
        res = supabase.table("packages").insert(data).execute()
        
    if not res.data:
        raise HTTPException(status_code=400, detail="Failed to save package")
    return res.data[0]

@router.patch("/packages/{pkg_id}/status")
def toggle_status(pkg_id: str, is_active: bool):
    """Quickly toggle a package between Live and Hidden"""
    res = supabase.table("packages").update({"is_active": is_active}).eq("id", pkg_id).execute()
    return {"status": "success"}

@router.delete("/packages/{pkg_id}")
def delete_package(pkg_id: str):
    """Delete a package from the database"""
    res = supabase.table("packages").delete().eq("id", pkg_id).execute()
    return {"status": "deleted"}