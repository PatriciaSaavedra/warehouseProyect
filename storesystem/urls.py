from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views

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

]