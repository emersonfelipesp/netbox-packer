"""Django form tests for PackerTemplateForm OS family <-> version pairing.

Runs under NetBox: ``python manage.py test netbox_packer``.
"""

from django.test import TestCase

from netbox_packer.choices import OSFamilyChoices
from netbox_packer.forms import PackerTemplateForm
from netbox_packer.models import PackerTemplate


def _base_data(**overrides):
    data = {
        "name": "test-template",
        "os_family": OSFamilyChoices.CHOICE_UBUNTU,
        "os_version": "24.04",
        "proxmox_template_id": 9100,
        "proxmox_node": "pve01",
        "build_status": "pending",
    }
    data.update(overrides)
    return data


class PackerTemplateFormOSPairingTest(TestCase):
    def test_valid_family_version_pairing_is_accepted(self):
        form = PackerTemplateForm(data=_base_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_mismatched_family_version_is_rejected(self):
        # Ubuntu family with a Debian version must fail cross-field validation.
        form = PackerTemplateForm(
            data=_base_data(os_family=OSFamilyChoices.CHOICE_UBUNTU, os_version="13"),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("os_version", form.errors)

    def test_version_from_correct_family_is_accepted(self):
        form = PackerTemplateForm(
            data=_base_data(os_family=OSFamilyChoices.CHOICE_DEBIAN, os_version="13"),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_preserves_stored_off_list_version(self):
        # An existing template whose stored version is off-list stays editable.
        template = PackerTemplate.objects.create(
            name="legacy-template",
            os_family=OSFamilyChoices.CHOICE_UBUNTU,
            os_version="18.04",  # not offered in OS_VERSIONS_BY_FAMILY
            proxmox_template_id=9101,
            proxmox_node="pve01",
        )
        form = PackerTemplateForm(
            data=_base_data(
                name="legacy-template",
                os_family=OSFamilyChoices.CHOICE_UBUNTU,
                os_version="18.04",
                proxmox_template_id=9101,
            ),
            instance=template,
        )
        self.assertTrue(form.is_valid(), form.errors)
