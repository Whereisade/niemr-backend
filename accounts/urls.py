from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from . import views

urlpatterns = [
    path("register/", views.register),
    path("login/", views.login_password),
    path("google/", views.login_google),
    path("token/refresh/", TokenRefreshView.as_view()),
]
