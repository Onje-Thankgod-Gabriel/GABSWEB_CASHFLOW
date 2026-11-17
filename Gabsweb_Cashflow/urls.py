"""
URL configuration for Gabsweb_Cashflow project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static

from core import views as core_views
from core import pwa as core_pwa

urlpatterns = [
    path('admin/', admin.site.urls),
    path('manifest.webmanifest', core_pwa.pwa_manifest, name='pwa_manifest'),
    path('sw.js', core_pwa.pwa_service_worker, name='pwa_service_worker'),
    path('accounts/google/login/', core_views.google_login),
    path('accounts/google/login/callback/', core_views.google_callback),
    path('accounts/', include('allauth.urls')),
    re_path(r'^media/profile-images/.*$', core_views.profile_media_denied),
    path('', include('core.urls')),
]


if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root = settings.STATIC_ROOT)  
    urlpatterns += static(settings.MEDIA_URL, document_root = settings.MEDIA_ROOT)