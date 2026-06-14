from fastapi import APIRouter
from db import supabase

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health():
    # also checks DB connectivity
    try:
        supabase.table("members").select("member_id").limit(1).execute()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status":    "ok",
        "db":        db_status,
        "version":   "1.0.0",
    }