import os
import random
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import httpx

from db import supabase

# Router setup
router = APIRouter(prefix="/api/admin", tags=["Admin Auth"])

# Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- PYDANTIC SCHEMAS ---
class EmailRequest(BaseModel):
    email: EmailStr

class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str

class RegisterRequest(BaseModel):
    full_name: str
    email: EmailStr
    company_name: str
    phone: str
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

# --- EMAIL UTILITY ---
async def send_brevo_email(to_email: str, code: str):
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": os.getenv("BREVO_API_KEY"),
        "content-type": "application/json"
    }
    
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 10px;">
        <h2 style="color: #10b981; text-align: center;">ISPConnect Activation</h2>
        <p>Hello,</p>
        <p>Your master admin verification code is:</p>
        <div style="background: #f3f4f6; padding: 15px; text-align: center; border-radius: 8px; margin: 20px 0;">
            <span style="font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #111827;">{code}</span>
        </div>
        <p style="color: #6b7280; font-size: 14px;">This code expires in 10 minutes.</p>
    </div>
    """
    
    payload = {
        "sender": {"email": os.getenv("SENDER_EMAIL")},
        "to": [{"email": to_email}],
        "subject": "ISPConnect - Verification Code",
        "htmlContent": html_content
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code != 201:
                print(f"Brevo Error: {response.status_code} - {response.text}")
            else:
                print("Brevo Email Sent Successfully!")
        except Exception as e:
            # Using repr(e) forces Python to print the exact error object even if the string is empty
            print(f"Email Exception: {repr(e)}")

# --- ROUTES ---
@router.post("/send-email-code")
async def send_email_code(request: EmailRequest, background_tasks: BackgroundTasks):
    # 1. Check if an admin already exists (Single-ISP constraint)
    existing_admin = supabase.table("admins").select("id").limit(1).execute()
    if existing_admin.data:
        raise HTTPException(status_code=400, detail="System is already activated by a master admin.")

    # 2. Generate code and hash it
    plain_code = str(random.randint(100000, 999999))
    hashed_code = pwd_context.hash(plain_code)
    
    # 3. Calculate expiration (10 minutes from now)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    # 4. Remove old codes for this email, then insert the new one
    supabase.table("verification_codes").delete().eq("email", request.email).execute()
    supabase.table("verification_codes").insert({
        "email": request.email,
        "code_hash": hashed_code,
        "expires_at": expires_at
    }).execute()

    # 5. Send email in the background so the API responds instantly
    background_tasks.add_task(send_brevo_email, request.email, plain_code)
    
    return {"status": "success", "message": "Verification code sent."}

@router.post("/verify-email-code")
def verify_email_code(request: VerifyCodeRequest):
    # Fetch the code record
    response = supabase.table("verification_codes").select("*").eq("email", request.email).execute()
    records = response.data
    
    if not records:
        raise HTTPException(status_code=404, detail="No verification code requested for this email.")
        
    record = records[0]
    
    # Check expiration
    expires_at = datetime.fromisoformat(record["expires_at"].replace('Z', '+00:00'))
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="Code has expired. Please request a new one.")
        
    # Verify the hash
    if not pwd_context.verify(request.code, record["code_hash"]):
        raise HTTPException(status_code=400, detail="Invalid verification code.")
        
    # Clean up the used code
    supabase.table("verification_codes").delete().eq("id", record["id"]).execute()
    
    return {"status": "success", "message": "Email verified successfully."}

@router.post("/register")
def register_admin(request: RegisterRequest):
    # Double-check if an admin already exists to prevent race conditions
    existing_admin = supabase.table("admins").select("id").limit(1).execute()
    if existing_admin.data:
        raise HTTPException(status_code=400, detail="Master admin is already registered.")

    hashed_password = pwd_context.hash(request.password)

    # Insert Admin (is_active is False by default until payment is made)
    admin_data = supabase.table("admins").insert({
        "full_name": request.full_name,
        "email": request.email,
        "phone": request.phone,
        "password_hash": hashed_password,
        "is_active": False
    }).execute()

    # Insert System Settings
    supabase.table("system_settings").insert({
        "company_name": request.company_name,
        "support_phone": request.phone
    }).execute()

    return {"status": "success", "message": "Admin created. Pending activation payment."}

@router.post("/login")
def login_admin(request: LoginRequest):
    # 1. Fetch the admin record
    response = supabase.table("admins").select("*").eq("email", request.email).execute()
    admins = response.data

    if not admins:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    
    admin = admins[0]

    # 2. Verify the hashed password
    if not pwd_context.verify(request.password, admin["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    # 3. Ensure the account was activated via PayHero
    if not admin["is_active"]:
        raise HTTPException(status_code=403, detail="Account pending activation. Please complete your setup payment.")

    # 4. Success! 
    return {
        "status": "success", 
        "message": "Login successful",
        "admin_name": admin["full_name"]
    }