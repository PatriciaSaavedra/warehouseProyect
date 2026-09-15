from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from usuarios.views import perfil_usuario_view
from django.views.generic import RedirectView
urlpatterns = [

    path('admin/', admin.site.urls),

    path('', include('authentication.urls')),

    # path(
    #     'logout/',
    #     auth_views.LogoutView.as_view(
    #         next_page='login'
    #     ),
    #     name='logout'
    # ),
    
    path('dashboard/', include('dashboard.urls')),

    path('inventario/', include('inventario.urls')),

    path('solicitudes/', include('solicitudes.urls')),

    path('compras/', include('compras.urls')),

    path('presupuestos/', include('presupuestos.urls')),
    path('usuarios/', include('usuarios.urls')),

    path('auditoria/', include('auditoria.urls')),
    
    path('perfil/', RedirectView.as_view(url='/usuarios/perfil/', permanent=False)),

]