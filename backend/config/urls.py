from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include


def health(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", health),
    path("health/", health),
    path("django-admin/", admin.site.urls),
    path("api/", include("core.urls")),
]
