from django.urls import path

from . import views

urlpatterns = [
    path("sign-in/", views.sign_in, name="sign_in"),
    path("sign-in/sent/", views.sign_in_sent, name="sign_in_sent"),
    path("sign-in/link/<str:token>/", views.sign_in_link, name="sign_in_link"),
    path("sign-out/", views.sign_out, name="sign_out"),
]
