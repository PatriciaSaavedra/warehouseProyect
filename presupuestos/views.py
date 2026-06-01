from django.shortcuts import render

def presupuestos_view(request):
    return render(request, 'presupuestos/index.html')