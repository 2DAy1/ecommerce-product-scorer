import json

from django.contrib.auth import authenticate, login, logout
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request: Request) -> Response:
    return Response({"status": "ok"})


@require_GET
@ensure_csrf_cookie
def auth_session(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "authenticated": request.user.is_authenticated,
            "username": request.user.get_username()
            if request.user.is_authenticated
            else "",
        }
    )


@require_POST
@csrf_protect
def auth_login(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"detail": "Invalid JSON payload."}, status=400)

    username = payload.get("username") if isinstance(payload, dict) else None
    password = payload.get("password") if isinstance(payload, dict) else None
    if not isinstance(username, str) or not isinstance(password, str):
        return JsonResponse(
            {"detail": "Username and password are required."},
            status=400,
        )
    user = authenticate(
        request,
        username=username.strip(),
        password=password,
    )
    if user is None or not user.is_active:
        return JsonResponse(
            {"detail": "Invalid username or password."},
            status=401,
        )
    login(request, user)
    return JsonResponse({"authenticated": True, "username": user.get_username()})


@require_POST
@csrf_protect
def auth_logout(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "Authentication required."}, status=401)
    logout(request)
    return JsonResponse({"authenticated": False, "username": ""})
