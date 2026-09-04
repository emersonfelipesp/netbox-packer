"""Disposable PostgreSQL/Redis configuration for real NetBox integration tests.

Used by the `netbox-compatibility` CI job, which checks out a NetBox tag from
source and proves this plugin migrates, passes system checks, and reports the
expected support tier on it. The NetBox 4.7 compatibility job uses an exact
source checkout; the image-based `e2e.yml` matrix is retained for the
established Docker targets.

Host and port come from the environment so the same file works against CI
service containers on the standard ports and against a local disposable stack
bound elsewhere.
"""

from __future__ import annotations

import os

from netbox.configuration_testing import *  # noqa: F403
from netbox.configuration_testing import PLUGINS as BASE_PLUGINS

# netbox-packer is the only plugin in the Proxbox family that declares no
# `required_plugins`, so it loads standalone. That is what lets this job run
# without a netbox-proxbox release carrying the same NetBox ceiling — the
# companions cannot start at all until one exists, because plugin version
# validation raises while settings.py is still executing.
PLUGINS = [*BASE_PLUGINS, "netbox_packer"]

DATABASES["default"]["HOST"] = os.environ.get("NETBOX_TEST_DB_HOST", "127.0.0.1")  # noqa: F405
DATABASES["default"]["PORT"] = int(os.environ.get("NETBOX_TEST_DB_PORT", "5432"))  # noqa: F405
for redis_config in REDIS.values():  # noqa: F405
    redis_config["HOST"] = os.environ.get("NETBOX_TEST_REDIS_HOST", "127.0.0.1")
    redis_config["PORT"] = int(os.environ.get("NETBOX_TEST_REDIS_PORT", "6379"))
