"""DB-backed regression coverage for the File Server credential authorization flag.

Runs under NetBox: ``python manage.py test netbox_packer``.

``unique=True`` on ``PackerTemplate.name`` (migration 0018) only stops two rows
sharing the trusted name *simultaneously*. It does not stop the trusted row
being renamed away and a different row later reclaiming the freed name — that
row would then pass a name-based credential guard. These tests exercise the
actual database behavior of ``is_fileserver_golden_template`` (migration 0019)
against that exact rename/delete + reclamation sequence, rather than only
simulating the two resulting states as booleans.
"""

from django.test import TestCase

from netbox_packer.api.serializers import PackerTemplateSerializer
from netbox_packer.choices import OSFamilyChoices
from netbox_packer.forms import PackerTemplateForm
from netbox_packer.models import PackerTemplate
from netbox_packer.package_index import FILESERVER_TEMPLATE_NAME


def _template_data(**overrides):
    data = {
        "name": "some-other-template",
        "os_family": OSFamilyChoices.CHOICE_UBUNTU,
        "os_version": "24.04",
        "proxmox_template_id": 9200,
        "proxmox_node": "pve01",
        "build_status": "pending",
    }
    data.update(overrides)
    return data


class MigrationSeedsGoldenTemplateTest(TestCase):
    def test_migration_0019_flags_the_seeded_fileserver_row(self):
        template = PackerTemplate.objects.get(name=FILESERVER_TEMPLATE_NAME)
        self.assertTrue(template.is_fileserver_golden_template)


class RenameThenReclaimTest(TestCase):
    """Reproduce the exact bypass migration 0019 exists to close."""

    def test_renaming_the_golden_template_preserves_its_flag(self):
        template = PackerTemplate.objects.get(name=FILESERVER_TEMPLATE_NAME)
        template.name = "renamed-golden-template"
        template.save()

        reloaded = PackerTemplate.objects.get(pk=template.pk)
        self.assertTrue(reloaded.is_fileserver_golden_template)

    def test_a_new_row_reclaiming_the_freed_name_defaults_to_untrusted(self):
        golden = PackerTemplate.objects.get(name=FILESERVER_TEMPLATE_NAME)
        golden.name = "renamed-golden-template"
        golden.save()

        impostor = PackerTemplate.objects.create(**_template_data(name=FILESERVER_TEMPLATE_NAME))

        self.assertFalse(impostor.is_fileserver_golden_template)

    def test_deleting_and_recreating_the_golden_name_defaults_to_untrusted(self):
        golden = PackerTemplate.objects.get(name=FILESERVER_TEMPLATE_NAME)
        golden.delete()

        impostor = PackerTemplate.objects.create(**_template_data(name=FILESERVER_TEMPLATE_NAME))

        self.assertFalse(impostor.is_fileserver_golden_template)


class GoldenTemplateFlagIsReadOnlyToUserInputTest(TestCase):
    """The flag must be settable only by a migration, never through user-facing input.

    ``test_form_input_cannot_set_the_flag`` is deliberately a fields-membership
    check rather than a full ``is_valid()``/``save()`` round trip: NetBox core's
    ``CheckLastUpdatedMixin.clean()`` (``netbox/forms/mixins.py``) returns
    ``None`` for any new-instance form submission that omits ``_init_time``,
    which breaks ``PackerTemplateForm.clean()``'s ``super().clean()`` chain
    independently of this plugin's code — the identical, pre-existing failure
    reproduces on ``main`` in ``PackerTemplateFormOSPairingTest`` (this same
    package). A Django ``ModelForm`` can only populate ``cleaned_data``/save an
    attribute for a field present in ``self.fields`` (driven by ``Meta.fields``),
    so proving the flag is absent from the form's field set is sufficient to
    prove no POSTed value for it is ever read or persisted, without depending
    on that unrelated framework bug.
    """

    def test_form_input_cannot_set_the_flag(self):
        form = PackerTemplateForm()
        self.assertNotIn("is_fileserver_golden_template", form.fields)

    def test_serializer_does_not_expose_the_flag_as_a_field(self):
        serializer = PackerTemplateSerializer()
        self.assertNotIn("is_fileserver_golden_template", serializer.fields)

    def test_serializer_input_cannot_set_the_flag(self):
        serializer = PackerTemplateSerializer(
            data=_template_data(name="attacker-template-2", is_fileserver_golden_template=True),
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("is_fileserver_golden_template", serializer.validated_data)
        instance = serializer.save()
        self.assertFalse(instance.is_fileserver_golden_template)
