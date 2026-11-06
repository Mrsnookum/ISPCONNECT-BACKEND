from fastapi import APIRouter
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from db import supabase

router = APIRouter(prefix="/api/admin", tags=["Dashboard"])

@router.get("/dashboard-stats")
def get_dashboard_stats(period: str = "7D"):
    now = datetime.now(timezone.utc)
    
    # Determine days based on period toggle
    days = 7
    if period == "1D": days = 1
    elif period == "30D": days = 30
    elif period == "1Y": days = 365

    start_date = now - timedelta(days=days)
    prev_start_date = start_date - timedelta(days=days)

    # 1. Fetch All Payments
    payments_res = supabase.table("payments").select("*").order("created_at", desc=True).execute()
    all_payments = payments_res.data or []

    # Variables for calculations
    current_revenue = 0
    prev_revenue = 0
    active_customers = set()
    prev_customers = set()
    
    revenue_by_date = defaultdict(int)
    package_stats = defaultdict(lambda: {"count": 0, "revenue": 0})
    recent_payments = []

    # 2. Process Data
    for p in all_payments:
        amt = p.get("amount", 0)
        status = p.get("status", "pending").lower()
        phone = p.get("phone", "Unknown")
        created_at = datetime.fromisoformat(p.get("created_at").replace('Z', '+00:00'))

        # Recent Payments Table (Limit 5)
        if len(recent_payments) < 5:
            masked_phone = f"{phone[:4]}***{phone[-3:]}" if len(phone) >= 10 else phone
            recent_payments.append({
                "name": masked_phone,
                "amount": amt,
                "status": status.capitalize()
            })

        # Process only successful payments for Revenue and Packages
        if status == "completed":
            date_str = created_at.strftime("%Y-%m-%d")
            
            # Current Period
            if created_at >= start_date:
                current_revenue += amt
                revenue_by_date[date_str] += amt
                active_customers.add(phone)
                
                # Mock Package logic based on amount
                pkg_name = "1 Hour" if amt == 20 else "6 Hours" if amt == 50 else "24 Hours" if amt == 100 else "Custom"
                package_stats[pkg_name]["count"] += 1
                package_stats[pkg_name]["revenue"] += amt

            # Previous Period (For trend percentage)
            elif prev_start_date <= created_at < start_date:
                prev_revenue += amt
                prev_customers.add(phone)

    # 3. Format Chart Data
    labels = []
    chart_data = []
    
    if days == 1:
        # Hourly for 1D
        for i in range(23, -1, -1):
            labels.append((now - timedelta(hours=i)).strftime("%H:00"))
            chart_data.append(current_revenue / 24) # Simplified spread for 1D
    else:
        # Daily for others
        for i in range(days - 1, -1, -1):
            day = now - timedelta(days=i)
            labels.append(day.strftime("%b %d"))
            chart_data.append(revenue_by_date.get(day.strftime("%Y-%m-%d"), 0))

    # 4. Format Packages Table
    packages_list = []
    for name, stats in package_stats.items():
        percent = int((stats["revenue"] / current_revenue * 100)) if current_revenue > 0 else 0
        packages_list.append({
            "name": name,
            "count": stats["count"],
            "revenue": stats["revenue"],
            "percent": percent
        })
    packages_list = sorted(packages_list, key=lambda x: x["revenue"], reverse=True)[:5]

    # 5. Return JSON Payload
    return {
        "revenue": { "trend": [prev_revenue, current_revenue] },
        "customers": { "trend": [len(prev_customers), len(active_customers)] },
        "online": { "trend": [45, 76] }, 
        "sessions": { "trend": [1150, 1204] }, 
        "chart": { "labels": labels, "data": chart_data },
        "recent_payments": recent_payments,
        "packages": packages_list if packages_list else [{"name": "No data", "count": 0, "percent": 0, "revenue": 0}],
        "router": {
            "main_uptime": "12d 4h 32m",
            "hotspot_active": "76 / 200",
            "radius_uptime": "12d 4h 28m"
        }
    }