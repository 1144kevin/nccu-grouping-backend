from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.config import get_settings
from app.core_cloud.email_confirmation import confirmation_page
from app.core_cloud.google import GoogleFinishInput, GoogleStartInput, GoogleStartView, google_flows
from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import (
    StoreDep,
    TokenDep,
    UserDep,
    auth_rate_limit,
    require_local_mode,
)
from app.core_local.schemas import (
    AppPasswordInput,
    ConfirmationView,
    LoginInput,
    ProfileInput,
    ProfileView,
    RefreshInput,
    RegisterInput,
    SessionView,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/email-confirmed", include_in_schema=False, dependencies=[Depends(require_local_mode)])
def email_confirmed():
    return confirmation_page()


@router.post("/password", status_code=204, dependencies=[Depends(auth_rate_limit)])
def set_app_password(value: AppPasswordInput, store: StoreDep, token: TokenDep, user: UserDep):
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "password_requires_supabase")
    if not user.email_verified or user.deletion_pending:
        raise HTTPException(403, "app_password_not_available")
    if value.expected_user_id != user.id:
        raise HTTPException(409, "app_password_account_changed")
    store.set_app_password(token, value)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/refresh", response_model=SessionView, dependencies=[Depends(auth_rate_limit)])
def refresh(value: RefreshInput, store: StoreDep, response: Response) -> SessionView:
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "refresh_requires_supabase")
    return store.refresh(value)


@router.post(
    "/google/start", response_model=GoogleStartView, dependencies=[Depends(auth_rate_limit)]
)
def google_start(
    store: StoreDep, response: Response, value: GoogleStartInput | None = None
) -> GoogleStartView:
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "google_login_requires_supabase")
    client = value.client if value else "web"
    settings = getattr(store, "settings", None) or get_settings()
    if client == "ios" and not settings.core_google_ios_enabled:
        raise HTTPException(503, "google_ios_not_configured", headers={"Cache-Control": "no-store"})
    return google_flows.start(store, client=client)


@router.post("/google/finish", response_model=SessionView, dependencies=[Depends(auth_rate_limit)])
def google_finish(value: GoogleFinishInput, store: StoreDep, response: Response) -> SessionView:
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "google_login_requires_supabase")
    return google_flows.finish(store, value)


@router.post(
    "/register",
    response_model=SessionView | ConfirmationView,
    status_code=201,
    dependencies=[Depends(auth_rate_limit)],
)
def register(
    value: RegisterInput, store: StoreDep, response: Response
) -> SessionView | ConfirmationView:
    response.headers["Cache-Control"] = "no-store"
    result = store.register(value)
    if isinstance(result, ConfirmationView):
        response.status_code = 202
    return result


@router.post("/login", response_model=SessionView, dependencies=[Depends(auth_rate_limit)])
def login(value: LoginInput, store: StoreDep, response: Response) -> SessionView:
    response.headers["Cache-Control"] = "no-store"
    return store.login(value)


@router.get("/me", response_model=ProfileView)
def me(user: UserDep, response: Response) -> ProfileView:
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/logout", status_code=204)
def logout(store: StoreDep, token: TokenDep, _: UserDep) -> Response:
    store.logout(token)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.patch("/me", response_model=ProfileView)
def update_me(value: ProfileInput, store: StoreDep, user: UserDep) -> ProfileView:
    updated = store.update_profile(user.id, value)
    updated.auth_provider = user.auth_provider
    return updated
