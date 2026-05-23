"""Sitemap view for netbox-packer — serves a plain-text list of all plugin pages."""

from __future__ import annotations

from importlib.metadata import version as _pkg_version

from django.http import HttpRequest, HttpResponse
from django.views import View
from utilities.views import ConditionalLoginRequiredMixin

_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Templates",
        [
            ("templates-list", "/plugins/packer/templates/"),
            ("templates-add", "/plugins/packer/templates/add/"),
            ("sitemap", "/plugins/packer/sitemap.txt"),
        ],
    ),
    (
        "Builds",
        [
            ("builds-list", "/plugins/packer/builds/"),
            ("builds-add", "/plugins/packer/builds/add/"),
        ],
    ),
    (
        "Installer Configs",
        [
            ("installer-configs-list", "/plugins/packer/installer-configs/"),
            ("installer-configs-add", "/plugins/packer/installer-configs/add/"),
        ],
    ),
    (
        "Build Targets",
        [
            ("build-targets-list", "/plugins/packer/build-targets/"),
            ("build-targets-add", "/plugins/packer/build-targets/add/"),
        ],
    ),
]

# Detail pages that require a {pk} — excluded from the static sitemap.
# /plugins/packer/templates/{pk}/
# /plugins/packer/templates/{pk}/edit/
# /plugins/packer/templates/{pk}/delete/
# /plugins/packer/builds/{pk}/
# /plugins/packer/builds/{pk}/edit/
# /plugins/packer/builds/{pk}/delete/
# /plugins/packer/installer-configs/{pk}/
# /plugins/packer/installer-configs/{pk}/edit/
# /plugins/packer/installer-configs/{pk}/delete/
# /plugins/packer/build-targets/{pk}/
# /plugins/packer/build-targets/{pk}/edit/
# /plugins/packer/build-targets/{pk}/delete/


def _build_sitemap(base: str) -> list[str]:
    lines: list[str] = []
    try:
        version = _pkg_version("netbox-packer")
        lines.append(f"# netbox-packer {version} — plugin sitemap")
    except Exception:  # noqa: BLE001
        lines.append("# netbox-packer — plugin sitemap")
    lines.append(f"# Base: {base}")
    for section, pages in _SECTIONS:
        lines.append("")
        lines.append(f"# {section}")
        for label, path in pages:
            lines.append(f"{base}{path}  # {label}")
    return lines


class SitemapView(ConditionalLoginRequiredMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        base = request.build_absolute_uri("/").rstrip("/")
        body = "\n".join(_build_sitemap(base)) + "\n"
        return HttpResponse(body, content_type="text/plain; charset=utf-8")
