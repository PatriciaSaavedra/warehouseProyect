from django.shortcuts import render

def compras_view(request):
    return render(request, 'compras/index.html')