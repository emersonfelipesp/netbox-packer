"""Add tags to PackerPluginSettings and align custom_field_data encoder with NetBox 4.6."""

import taggit.managers
import utilities.json
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0002_squashed_0059"),
        ("netbox_packer", "0004_proxbox_api_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="packerpluginsettings",
            name="tags",
            field=taggit.managers.TaggableManager(
                through="extras.TaggedItem",
                to="extras.Tag",
            ),
        ),
        migrations.AlterField(
            model_name="packerpluginsettings",
            name="custom_field_data",
            field=models.JSONField(
                blank=True,
                default=dict,
                encoder=utilities.json.CustomFieldJSONEncoder,
            ),
        ),
    ]
