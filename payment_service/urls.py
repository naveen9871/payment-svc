from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse

# Fallback simple health check — optional backup
def health(request):
    return JsonResponse({"status": "ok"})

def readiness(request):
    return JsonResponse({"status": "ready"})

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('payments.urls')),  # 👈 include your payments app routes
    path('health', health),
    path('health/ready', readiness),
]
