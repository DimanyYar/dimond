from django.urls import path

from . import views

urlpatterns = [
    path("sign-in/", views.sign_in, name="sign_in"),
    path("sign-in/sent/", views.sign_in_sent, name="sign_in_sent"),
    path("sign-in/link/<str:token>/", views.sign_in_link, name="sign_in_link"),
    path("sign-out/", views.sign_out, name="sign_out"),
    path("clients/", views.clients, name="clients"),
    path("clients/new/", views.new_client, name="new_client"),
    path("clients/<int:pk>/", views.clients, name="client"),
    path("clients/<int:pk>/invite/", views.invite_people, name="invite_people"),
    path("clients/<int:pk>/people/<int:person_pk>/", views.person, name="person"),
    path(
        "clients/<int:pk>/people/<int:person_pk>/save/",
        views.save_person,
        name="save_person",
    ),
    path(
        "clients/<int:pk>/people/<int:person_pk>/sign-in-link/",
        views.send_sign_in_link,
        name="send_sign_in_link",
    ),
    path(
        "clients/<int:pk>/people/<int:person_pk>/sign-out/",
        views.sign_out_devices,
        name="sign_out_devices",
    ),
    path(
        "clients/<int:pk>/people/<int:person_pk>/resend/",
        views.resend_invite,
        name="resend_invite",
    ),
    path(
        "clients/<int:pk>/people/<int:person_pk>/cancel/",
        views.cancel_invite,
        name="cancel_invite",
    ),
    path(
        "clients/<int:pk>/people/<int:person_pk>/revoke/",
        views.revoke_person,
        name="revoke_person",
    ),
    path(
        "clients/<int:pk>/people/<int:person_pk>/restore/",
        views.restore_person,
        name="restore_person",
    ),
]
