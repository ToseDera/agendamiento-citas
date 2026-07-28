from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('cuentas/', include('django.contrib.auth.urls')),
    path('preview-login/', TemplateView.as_view(template_name='registration/login.html'), name='login'),
    path('preview-registro/', TemplateView.as_view(template_name='registration/registro.html'), name='registro'),
]
