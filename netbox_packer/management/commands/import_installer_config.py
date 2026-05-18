"""Management command to import a Packer installer config from a local file."""
import hashlib
import os

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Import a Packer installer config file (autoinstall/kickstart/preseed) "
        "into the PackerInstallerConfig catalog."
    )

    def add_arguments(self, parser):
        parser.add_argument("file", help="Path to the installer config file")
        parser.add_argument("--name", required=True, help="Config name")
        parser.add_argument(
            "--os-family",
            required=True,
            choices=["ubuntu", "rhel", "debian"],
            help="OS family (ubuntu/rhel/debian)",
        )
        parser.add_argument(
            "--installer-type",
            required=True,
            choices=["autoinstall", "kickstart", "preseed"],
            help="Installer type",
        )
        parser.add_argument("--version", default="1.0.0", help="Config version (default: 1.0.0)")
        parser.add_argument("--description", default="", help="Optional description")

    def handle(self, *args, **options):
        path = options["file"]
        if not os.path.isfile(path):
            raise CommandError(f"File not found: {path}")

        with open(path, encoding="utf-8") as fh:
            content = fh.read()

        from netbox_packer.models import PackerInstallerConfig

        obj, created = PackerInstallerConfig.objects.get_or_create(
            name=options["name"],
            version=options["version"],
            defaults={
                "os_family": options["os_family"],
                "installer_type": options["installer_type"],
                "content": content,
                "description": options["description"],
            },
        )

        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created PackerInstallerConfig '{obj.name}' v{obj.version} "
                    f"(checksum: {obj.checksum[:12]}...)"
                )
            )
        else:
            # Update content if changed
            new_checksum = hashlib.sha256(content.encode()).hexdigest()
            if obj.checksum != new_checksum:
                obj.content = content
                obj.os_family = options["os_family"]
                obj.installer_type = options["installer_type"]
                if options["description"]:
                    obj.description = options["description"]
                obj.save()
                self.stdout.write(
                    self.style.WARNING(
                        f"Updated PackerInstallerConfig '{obj.name}' v{obj.version} "
                        f"(new checksum: {obj.checksum[:12]}...)"
                    )
                )
            else:
                self.stdout.write(
                    f"PackerInstallerConfig '{obj.name}' v{obj.version} unchanged (checksum matches)."
                )
