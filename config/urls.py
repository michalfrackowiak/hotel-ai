from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.http import require_safe


@require_safe
def health(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz/", health, name="health"),
    path("admin/", admin.site.urls),
    path("api/", include("bookings.urls")),
]
