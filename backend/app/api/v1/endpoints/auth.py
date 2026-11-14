from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.cookies import clear_auth_cookies, set_auth_cookies
from app.api.deps import CurrentUser, SettingsDep, get_auth_service, request_metadata
from app.core.rate_limit import rate_limit
from app.schemas.auth import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    UpdateProfileRequest,
    UserResponse,
)
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _auth_response(tokens_user: object) -> AuthResponse:
    return AuthResponse(user=UserResponse.model_validate(tokens_user))


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth-register", "auth_rate_limit_per_minute"))],
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    user_agent, ip_address = request_metadata(request)
    tokens = await service.register(
        email=str(payload.email),
        password=payload.password,
        full_name=payload.full_name,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    set_auth_cookies(response, tokens, settings)
    return _auth_response(tokens.user)


@router.post(
    "/login",
    response_model=AuthResponse,
    dependencies=[Depends(rate_limit("auth-login", "auth_rate_limit_per_minute"))],
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    user_agent, ip_address = request_metadata(request)
    tokens = await service.login(
        email=str(payload.email),
        password=payload.password,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    set_auth_cookies(response, tokens, settings)
    return _auth_response(tokens.user)


@router.post(
    "/refresh",
    response_model=AuthResponse,
    dependencies=[Depends(rate_limit("auth-refresh", "auth_rate_limit_per_minute"))],
)
async def refresh(
    request: Request,
    response: Response,
    settings: SettingsDep,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    user_agent, ip_address = request_metadata(request)
    tokens = await service.refresh(
        request.cookies.get(settings.refresh_cookie_name),
        user_agent=user_agent,
        ip_address=ip_address,
    )
    set_auth_cookies(response, tokens, settings)
    return _auth_response(tokens.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    settings: SettingsDep,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    await service.logout(request.cookies.get(settings.refresh_cookie_name))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response


@router.get("/me", response_model=UserResponse, response_model_by_alias=True)
async def me(current_user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    payload: UpdateProfileRequest,
    current_user: CurrentUser,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    user = await service.update_profile(current_user, payload.full_name)
    return UserResponse.model_validate(user)


@router.post(
    "/change-password",
    response_model=AuthResponse,
    dependencies=[Depends(rate_limit("auth-password", "auth_rate_limit_per_minute"))],
)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    settings: SettingsDep,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    user_agent, ip_address = request_metadata(request)
    tokens = await service.change_password(
        current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    set_auth_cookies(response, tokens, settings)
    return _auth_response(tokens.user)
