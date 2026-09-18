from fastapi import APIRouter

from app.api.routes import (
    ai,
    conversations,
    join_requests,
    notifications,
    posts,
    recommendations,
    reports,
    uploads,
    users,
)

api_router = APIRouter()
api_router.include_router(users.router)
api_router.include_router(posts.router)
api_router.include_router(join_requests.router)
api_router.include_router(conversations.router)
api_router.include_router(notifications.router)
api_router.include_router(uploads.router)
api_router.include_router(ai.router)
api_router.include_router(recommendations.router)
api_router.include_router(reports.router)
