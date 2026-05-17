from __future__ import annotations

from django.shortcuts import render
from django.views.generic import View
from utilities.views import ConditionalLoginRequiredMixin


class PackerHomeView(ConditionalLoginRequiredMixin, View):
    """Placeholder home page for the Packer plugin."""

    def get(self, request, *args, **kwargs):
        return render(request, "netbox_packer/home.html", {})
