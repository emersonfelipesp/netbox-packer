from __future__ import annotations

from django.urls import path

from netbox_packer import views

app_name = "netbox_packer"

urlpatterns = [
    path("", views.PackerHomeView.as_view(), name="home"),
]
