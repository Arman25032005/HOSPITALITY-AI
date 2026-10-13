from fastapi import APIRouter

from app.api.routes import auth, chat, health, knowledge, operations, staff_ops

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/api/v1")
api_router.include_router(operations.router)
api_router.include_router(knowledge.router)
api_router.include_router(chat.router)
api_router.include_router(staff_ops.router)
