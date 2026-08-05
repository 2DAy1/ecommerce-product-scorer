from django.urls import path

from .views import auth_login, auth_logout, auth_session, health_check

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("auth/session/", auth_session, name="auth-session"),
    path("auth/login/", auth_login, name="auth-login"),
    path("auth/logout/", auth_logout, name="auth-logout"),
]
