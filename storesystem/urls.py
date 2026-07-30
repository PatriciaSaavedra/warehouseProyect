from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from usuarios.views import perfil_usuario_view

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
    path('perfil/', perfil_usuario_view, name='perfil'),
    path('usuarios/', include('usuarios.urls')),

    path('auditoria/', include('auditoria.urls')),

]