# Upgrading

## Retired integrations

The migration following `0032_update_endpoint_authorization_descriptions`
adds the public `provisions_service` state when it is missing and stamps the
three public service markers used by current image profiles. Its database
reverse is intentionally a no-op, so rolling backward and forward does not
discard marker values or operator data.

Marker stamping applies only when an exact public template name has a blank
marker. A nonblank value, including an operator-defined value, is preserved.

Existing databases are handled non-destructively. Columns and rows created by
older releases remain untouched in the database, but current models, forms,
serializers, jobs, and package code do not expose or execute them. Fresh
installations remain clean because the historical migrations that formerly
created those surfaces are graph-preserving no-ops.

Operators may review and archive old persisted data under their own retention
policy. The plugin does not delete templates, installer configurations,
credentials, build artifacts, descriptions, or unknown columns during this
upgrade.
