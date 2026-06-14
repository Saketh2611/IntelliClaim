from fastapi import APIRouter, Depends, HTTPException
from api.dependencies import verify_api_key
from db import supabase

router = APIRouter(prefix="/members", tags=["Members"])


@router.get("/{member_id}")
async def get_member(member_id: str, _: str = Depends(verify_api_key)):
    member = supabase.table("members").select("*").eq("member_id", member_id).single().execute()
    if not member.data:
        raise HTTPException(status_code=404, detail=f"Member {member_id} not found")
    return member.data