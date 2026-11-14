from django.urls import Resolver404, resolve

from .wsgi import application as django_application


def application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    try:
        route = resolve(path).view_name
    except Resolver404:
        route = None
    if route in {"health", "telephony:incoming", "telephony:turn"}:
        return django_application(environ, start_response)
    start_response("404 Not Found", [("Content-Length", "0")])
    return [b""]
