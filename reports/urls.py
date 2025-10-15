from django.urls import path
from .views import generate

urlpatterns = [
  path("generate", generate),
]
