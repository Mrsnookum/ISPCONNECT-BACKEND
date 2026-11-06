from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import auth
import billing
import dashboard
import packages_api
import ledger_api
import customers_api
import subscriptions_api
import portal_api
import mikrotik_api

app = FastAPI(
    title="ISPConnect API",
    description="Single-ISP Billing & Management API",
    version="1.0.0"
)

# Configure CORS to allow your frontend to communicate with the backend
app.add_middleware(
    CORSMiddleware,
    # UPDATE THIS: Replace with your actual GitHub Pages URL (no trailing slash)
    allow_origins=["https://mrsnookum.github.io"], 
    allow_credentials=False, # MUST be False when using "*" to prevent FastAPI from crashing on startup
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the authentication routes
app.include_router(auth.router)
# Include the billing and payment gateway routes
app.include_router(billing.router)
# Include the dashboard and analytics routes
app.include_router(dashboard.router)
# Include the packages routes
app.include_router(packages_api.router)
# Include the payments ledger routes
app.include_router(ledger_api.router)
# Include the customers routes
app.include_router(customers_api.router)
# Include the subscriptions routes
app.include_router(subscriptions_api.router)
# Include the public portal routes
app.include_router(portal_api.router)
# Include the MikroTik provisioning routes
app.include_router(mikrotik_api.router)

@app.get("/")
def read_root():
    return {"status": "online", "message": "ISPConnect API is running."}

# NEW: Keep-Alive Endpoint for Render Free Tier
@app.get("/health")
def health_check():
    return {"status": "awake"}