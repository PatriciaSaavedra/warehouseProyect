from django.shortcuts import render

def auditoria_view(request):
    return render(request, 'auditoria/index.html')