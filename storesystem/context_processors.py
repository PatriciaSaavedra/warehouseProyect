def theme_processor(request):
    # Lee la cookie 'theme'. Si no existe, por defecto usa 'light'
    theme = request.COOKIES.get('theme', 'light')
    return {'theme': theme}