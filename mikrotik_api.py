from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
import secrets
import uuid
from datetime import datetime, timedelta, timezone
import routeros_api
import os

from db import supabase 

router = APIRouter(
    prefix="/api/provision",
    tags=["MikroTik Provisioning"]
)

class InitRequest(BaseModel):
    identity: str
    backend_url: str

@router.post("/init")
async def init_provisioning(req: InitRequest):
    token = f"prv_{secrets.token_hex(8)}"
    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    
    data = {
        "token": token,
        "router_identity": req.identity,
        "is_used": False,
        "expires_at": expires
    }
    
    supabase.table("provisioning_tokens").insert(data).execute()
    
    command = (
        f'/tool fetch url="{req.backend_url}/api/provision/bootstrap" '
        f'http-method=post http-header-field="Authorization: Bearer {token}" '
        f'dst-path=setup.rsc; :delay 2s; /import setup.rsc; /file remove setup.rsc'
    )
    
    return {"status": "success", "command": command, "token": token}

@router.post("/bootstrap", response_class=PlainTextResponse)
async def serve_bootstrap_script(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    
    token = authorization.split(" ")[1]
    
    response = supabase.table("provisioning_tokens").select("*").eq("token", token).execute()
    if not response.data or response.data[0]['is_used']:
        raise HTTPException(status_code=403, detail="Token invalid or already used")
    
    router_identity = response.data[0]['router_identity']
    backend_url = os.getenv("RENDER_EXTERNAL_URL", "https://ispconnect-api.onrender.com")
    
    rsc_script = f"""
    :log info "ISPConnect: Starting Zero-Touch Provisioning Sequence..."
    /system identity set name="{router_identity}"
    /ip service set api disabled=no
    
    # 1. Create API User for Future Injections
    :do {{
        /user add name="ispconnect_api" group="full" password="{token}"
    }} on-error={{
        /user set ispconnect_api password="{token}"
    }}

    # 2. Build the Master Bridge and Enslave LAN/WLAN Ports (Excluding ether1)
    :log info "ISPConnect: Configuring Bridge..."
    :local bridgeName "ispconnect-bridge"
    :do {{ /interface bridge add name=$bridgeName }} on-error={{}}
    
    :foreach i in=[/interface find type=ether name!=ether1] do={{
        :do {{ /interface bridge port add bridge=$bridgeName interface=[/interface get $i name] }} on-error={{}}
    }}
    :foreach i in=[/interface find type=wlan] do={{
        :do {{ /interface bridge port add bridge=$bridgeName interface=[/interface get $i name] }} on-error={{}}
    }}

    # 3. Apply IP, DHCP, DNS, and NAT Masquerade
    :log info "ISPConnect: Applying Network & NAT Rules..."
    :local baseIp "10.0.0.1"
    :local netCidr "10.0.0.0/24"
    :local poolRange "10.0.0.2-10.0.0.254"

    :do {{ /ip address add address="10.0.0.1/24" interface=$bridgeName }} on-error={{}}
    :do {{ /ip pool add name="hs-pool" ranges=$poolRange }} on-error={{}}
    :do {{ /ip dhcp-server network add address=$netCidr gateway=$baseIp dns-server=$baseIp }} on-error={{}}
    :do {{ /ip dhcp-server add name="hs-dhcp" interface=$bridgeName address-pool="hs-pool" disabled=no }} on-error={{}}
    
    :do {{ /ip dns set allow-remote-requests=yes }} on-error={{}}
    :do {{ /ip firewall nat add chain=srcnat src-address=$netCidr action=masquerade }} on-error={{}}

    # 4. Determine Hardware Storage Structure (flash/hotspot vs hotspot)
    :local basePath "hotspot"
    :if ([:len [/file find name="flash"]] > 0) do={{
        :set basePath "flash/hotspot"
    }}

    # 5. Deploy the Hotspot on the Bridge
    :log info "ISPConnect: Deploying Hotspot Engine..."
    :do {{ /ip hotspot profile add name="hsprof1" hotspot-address=$baseIp dns-name="login.ispconnect.co.ke" html-directory=$basePath login-by="mac,cookie,http-chap" }} on-error={{}}
    :do {{ /ip hotspot add name="hs-server" interface=$bridgeName address-pool="hs-pool" profile="hsprof1" disabled=no }} on-error={{}}

    # 6. Allow Walled Garden for Payments
    :do {{ /ip hotspot walled-garden add dst-host="*.payhero.co.ke" }} on-error={{}}
    :do {{ /ip hotspot walled-garden add dst-host="*.onrender.com" }} on-error={{}}
    :do {{ /ip hotspot walled-garden add dst-host="*.github.io" }} on-error={{}}

    # 7. Fetch Portal Login Template Dynamically
    :log info "ISPConnect: Fetching Captive Portal UI..."
    /tool fetch url="{backend_url}/api/provision/login-template" dst-path="$basePath/login.html" keep-result=yes

    # 8. Notify Backend of Success
    :local justip "192.168.88.1";
    :do {{
        :local myip [/ip dhcp-client get [find status=bound] address];
        :set justip [:pick $myip 0 [:find $myip "/"]];
    }} on-error={{}}
    
    /tool fetch url="{backend_url}/api/provision/complete/{token}?ip=$justip" keep-result=no
    
    :log info "ISPConnect: Provisioning Complete. Router is now ACTIVE!"
    """
    
    return rsc_script

@router.get("/complete/{token}")
async def provision_complete(token: str, ip: str = "192.168.88.1"):
    response = supabase.table("provisioning_tokens").select("*").eq("token", token).execute()
    if not response.data:
        return {"status": "ignored"}
    
    router_identity = response.data[0]['router_identity']
    current_time = datetime.now(timezone.utc).isoformat()
    
    existing = supabase.table("routers").select("id").eq("identity", router_identity).execute()
    
    if existing.data:
        supabase.table("routers").update({
            "ip_address": ip,  
            "radius_secret": secrets.token_hex(16),
            "status": "Online",
            "provisioning_status": "Active",
            "updated_at": current_time
        }).eq("identity", router_identity).execute()
    else:
        router_data = {
            "id": str(uuid.uuid4()),
            "name": router_identity,
            "location": "Unassigned",
            "identity": router_identity,
            "ip_address": ip,  
            "routeros_version": "Unknown",
            "radius_secret": secrets.token_hex(16),
            "status": "Online",
            "provisioning_status": "Active",
            "last_seen_at": current_time,
            "created_at": current_time,
            "updated_at": current_time
        }
        supabase.table("routers").insert(router_data).execute()
        
    supabase.table("provisioning_tokens").update({"is_used": True}).eq("token", token).execute()
    
    return {"status": "success"}

@router.get("/status/{token}")
async def check_status(token: str):
    response = supabase.table("provisioning_tokens").select("is_used").eq("token", token).execute()
    if response.data and response.data[0]['is_used']:
        return {"status": "connected"}
    return {"status": "waiting"}

@router.get("/routers")
async def get_all_routers():
    try:
        response = supabase.table("routers").select("*").execute()
        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/login-template", response_class=PlainTextResponse)
async def get_login_template():
    """Provides the raw HTML for the MikroTik to download and save as hotspot/login.html"""
    
    # UPDATE THIS TO YOUR GITHUB PAGES URL
    frontend_url = "https://mrsnookum.github.io/ispconnect2"
    
    html_content = f"""<html>
<head><title>Redirecting...</title>
<script>window.location.href="{frontend_url}/portal.html?mac=$(mac)&ip=$(ip)&link-login=$(link-login)";</script>
</head><body></body></html>"""
    
    return html_content
