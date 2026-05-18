"""Management command to run the PackerStalenessCheckJob synchronously."""
import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger("netbox_packer")


class Command(BaseCommand):
    help = (
        "Scan all PackerTemplate objects for staleness and optionally queue auto-rebuilds. "
        "Equivalent to running PackerStalenessCheckJob directly without RQ."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report stale templates without updating statuses or queuing rebuilds.",
        )

    def handle(self, *args, **options):
        from netbox_packer.models import PackerBuild, PackerTemplate

        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made."))

        checked = 0
        stale = 0
        queued = 0

        for template in PackerTemplate.objects.exclude(
            build_status__in=("building",)
        ).exclude(max_age_days=None):
            checked += 1
            if not template.is_stale:
                self.stdout.write(f"  OK       {template.name}")
                continue

            stale += 1
            self.stdout.write(
                self.style.WARNING(f"  STALE    {template.name}  (age={template.age_days}d, max={template.max_age_days}d)")
            )

            if dry_run:
                continue

            PackerTemplate.objects.filter(pk=template.pk).update(build_status="stale")

            if not template.auto_rebuild:
                continue

            active = PackerBuild.objects.filter(
                template=template, status__in=("queued", "running")
            ).exists()
            if active:
                self.stdout.write(f"           → skipping auto-rebuild (already active build)")
                continue

            build = PackerBuild.objects.create(
                template=template,
                triggered_by="check_packer_staleness management command",
                status="queued",
            )
            queued += 1
            self.stdout.write(
                self.style.SUCCESS(f"           → queued rebuild as PackerBuild #{build.pk}")
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {checked} checked, {stale} stale, {queued} rebuilds queued."
            )
        )
