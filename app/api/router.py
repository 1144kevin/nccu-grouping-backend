from fastapi import APIRouter

from app.api.routes import (
    account_deletion,
    ai,
    auth,
    conversations,
    demo,
    development,
    engagement,
    join_requests,
    moderation,
    notifications,
    posts,
    recommendations,
    reports,
    search,
    stealth,
    stealth_cloud,
    uploads,
    user_blocks,
    users,
)

api_router = APIRouter()
api_router.include_router(development.router)
api_router.include_router(user_blocks.router)
api_router.include_router(account_deletion.router)
api_router.include_router(auth.router)
api_router.include_router(engagement.router)
api_router.include_router(demo.router)
api_router.include_router(demo.clock_router)
api_router.include_router(users.router)
api_router.include_router(posts.router)
api_router.include_router(join_requests.router)
api_router.include_router(conversations.router)
api_router.include_router(notifications.router)
api_router.include_router(uploads.router)
api_router.include_router(ai.router)
api_router.include_router(recommendations.router)
api_router.include_router(reports.router)
api_router.include_router(moderation.router)
api_router.include_router(search.router)
api_router.include_router(stealth.router)
api_router.include_router(stealth_cloud.router)
