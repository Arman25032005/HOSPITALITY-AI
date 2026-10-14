from fastapi import APIRouter

from app.api.routes import auth, chat, health, integrations, knowledge, operations, staff_ops, webhooks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/api/v1")
api_router.include_router(operations.router)
api_router.include_router(knowledge.router)
api_router.include_router(chat.router)
api_router.include_router(staff_ops.router)
api_router.include_router(webhooks.router)
api_router.include_router(integrations.router)
