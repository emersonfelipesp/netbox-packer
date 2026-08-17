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
        from netbox_packer.jobs import dispatch_build
        from netbox_packer.models import PackerBuild, PackerTemplate

        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made."))

        checked = 0
        stale = 0
        dispatched = 0

        for template in PackerTemplate.objects.exclude(build_status__in=("building",)):
            checked += 1
            if not template.is_stale:
                self.stdout.write(f"  OK       {template.name}")
                continue

            stale += 1
            msg = f"  STALE    {template.name}  (age={template.age_days}d, max={template.max_age_days}d)"
            self.stdout.write(self.style.WARNING(msg))

            if dry_run:
                continue

            PackerTemplate.objects.filter(pk=template.pk).update(build_status="stale")

            if not template.auto_rebuild:
                continue

            if PackerBuild.objects.filter(template=template, status="running").exists():
                self.stdout.write("           → skipping auto-rebuild (already running build)")
                continue

            build = (
                PackerBuild.objects.filter(template=template, status="queued")
                .order_by("queued_at")
                .first()
            )
            if build is None:
                build = PackerBuild.objects.create(
                    template=template,
                    triggered_by="check_packer_staleness management command",
                    status="queued",
                )
                action = "queued"
            else:
                action = "recovered queued"

            PackerTemplate.objects.filter(pk=template.pk).update(build_status="building")
            dispatch_build(build)
            dispatched += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"           → {action} and dispatched PackerBuild #{build.pk}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {checked} checked, {stale} stale, {dispatched} rebuilds dispatched."
            )
        )
