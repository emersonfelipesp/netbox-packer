"""Seed the Akvorado 2.4.0 all-in-one cloud-init template."""

import hashlib

from django.db import migrations

AKVORADO_CLOUD_CONFIG = r"""#cloud-config
# Akvorado 2.4.0 all-in-one flow-collection template for Ubuntu 24.04.
# Kafka, Valkey, ClickHouse, and every Akvorado component use explicit image
# tags. The host agents are added independently by the build-time injector.
# No integration identity or secret is stored in this image.
package_update: true
package_upgrade: false
packages:
  - ca-certificates
  - curl
  - gnupg
write_files:
  - path: /opt/akvorado/.env
    permissions: "0644"
    owner: root:root
    content: |
      COMPOSE_PROJECT_NAME=akvorado
      AKVORADO_CFG_CONSOLE_BRANDING=false
  - path: /opt/akvorado/docker-compose.yml
    permissions: "0644"
    owner: root:root
    content: |
      services:
        kafka:
          image: apache/kafka:4.2.0
          environment:
            KAFKA_NODE_ID: "1"
            KAFKA_PROCESS_ROLES: controller,broker
            KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
            KAFKA_LISTENERS: CLIENT://:9092,CONTROLLER://:9093
            KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CLIENT:PLAINTEXT,CONTROLLER:PLAINTEXT
            KAFKA_ADVERTISED_LISTENERS: CLIENT://kafka:9092
            KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
            KAFKA_INTER_BROKER_LISTENER_NAME: CLIENT
            KAFKA_DELETE_TOPIC_ENABLE: "true"
            KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: "1"
            KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: "1"
            KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: "1"
            KAFKA_SHARE_COORDINATOR_STATE_TOPIC_REPLICATION_FACTOR: "1"
            KAFKA_SHARE_COORDINATOR_STATE_TOPIC_MIN_ISR: "1"
            KAFKA_LOG_DIRS: /var/lib/kafka/data
          volumes:
            - kafka-data:/var/lib/kafka/data
          restart: unless-stopped
          healthcheck:
            test:
              - CMD
              - /opt/kafka/bin/kafka-topics.sh
              - --list
              - --bootstrap-server
              - kafka:9092
            interval: 20s
            timeout: 10s
            retries: 15
            start_period: 30s

        valkey:
          image: valkey/valkey:9.0
          volumes:
            - valkey-data:/data
          restart: unless-stopped
          healthcheck:
            test: [CMD, valkey-cli, ping]
            interval: 10s
            timeout: 3s
            retries: 15

        clickhouse:
          image: clickhouse/clickhouse-server:26.3
          environment:
            CLICKHOUSE_INIT_TIMEOUT: "60"
            CLICKHOUSE_SKIP_USER_SETUP: "1"
          cap_add:
            - SYS_NICE
          volumes:
            - clickhouse-data:/var/lib/clickhouse
            - ./clickhouse/observability.xml:/etc/clickhouse-server/config.d/observability.xml:ro
            - ./clickhouse/server.xml:/etc/clickhouse-server/config.d/akvorado.xml:ro
          restart: unless-stopped
          stop_grace_period: 30s
          healthcheck:
            test: [CMD, wget, -T, "1", --spider, --no-proxy, http://127.0.0.1:8123/ping]
            interval: 20s
            timeout: 5s
            retries: 15
            start_period: 30s

        akvorado-orchestrator:
          image: quay.io/akvorado/akvorado:2.4.0
          command: orchestrator /etc/akvorado/akvorado.yaml
          depends_on:
            kafka:
              condition: service_healthy
          volumes:
            - ./config:/etc/akvorado:ro
          restart: unless-stopped

        akvorado-console:
          image: quay.io/akvorado/akvorado:2.4.0
          command: console http://akvorado-orchestrator:8080
          depends_on:
            akvorado-orchestrator:
              condition: service_healthy
            valkey:
              condition: service_healthy
            clickhouse:
              condition: service_healthy
          environment:
            AKVORADO_CFG_CONSOLE_DATABASE_DSN: /run/akvorado/console.sqlite
            AKVORADO_CFG_CONSOLE_BRANDING: ${AKVORADO_CFG_CONSOLE_BRANDING:-false}
          volumes:
            - console-db:/run/akvorado
          # Akvorado trusts identity headers from an authenticating reverse
          # proxy; never publish its console directly on a wildcard address.
          ports:
            - 127.0.0.1:8081:8080/tcp
          restart: unless-stopped

        akvorado-inlet:
          image: quay.io/akvorado/akvorado:2.4.0
          command: inlet http://akvorado-orchestrator:8080
          depends_on:
            akvorado-orchestrator:
              condition: service_healthy
            kafka:
              condition: service_healthy
          ports:
            - 2055:2055/udp
            - 4739:4739/udp
            - 6343:6343/udp
          volumes:
            - akvorado-run:/run/akvorado
          restart: unless-stopped

        akvorado-outlet:
          image: quay.io/akvorado/akvorado:2.4.0
          command: outlet http://akvorado-orchestrator:8080
          depends_on:
            akvorado-orchestrator:
              condition: service_healthy
            kafka:
              condition: service_healthy
            clickhouse:
              condition: service_healthy
          environment:
            AKVORADO_CFG_OUTLET_METADATA_CACHEPERSISTFILE: /run/akvorado/metadata.cache
            AKVORADO_CFG_OUTLET_FLOW_STATEPERSISTFILE: /run/akvorado/flow.state
          ports:
            - 10179:10179/tcp
          volumes:
            - akvorado-run:/run/akvorado
          restart: unless-stopped
          stop_grace_period: 30s

      volumes:
        kafka-data:
        valkey-data:
        clickhouse-data:
        console-db:
        akvorado-run:
  - path: /opt/akvorado/config/akvorado.yaml
    permissions: "0644"
    owner: root:root
    content: |
      kafka:
        topic: flows
        brokers:
          - kafka:9092
        topic-configuration:
          num-partitions: 8
          replication-factor: 1
          config-entries:
            segment.bytes: 1073741824
            retention.ms: 86400000
            cleanup.policy: delete
            compression.type: producer
      geoip:
        optional: true
        asn-database:
          - /usr/share/GeoIP/asn.mmdb
        geo-database:
          - /usr/share/GeoIP/country.mmdb
      clickhousedb:
        servers:
          - clickhouse:9000
      clickhouse:
        orchestrator-url: http://akvorado-orchestrator:8080
        prometheus-endpoint: /metrics
        asns: {}
        networks:
          10.0.0.0/8:
            name: private-10
            role: internal
          172.16.0.0/12:
            name: private-172
            role: internal
          192.168.0.0/16:
            name: private-192
            role: internal
        network-sources: []
      inlet: !include "inlet.yaml"
      outlet: !include "outlet.yaml"
      console: !include "console.yaml"
  - path: /opt/akvorado/config/inlet.yaml
    permissions: "0644"
    owner: root:root
    content: |
      flow:
        inputs:
          - type: udp
            decoder: netflow
            listen: :2055
            workers: 4
            receive-buffer: 212992
          - type: udp
            decoder: netflow
            listen: :4739
            workers: 4
            receive-buffer: 212992
          - type: udp
            decoder: sflow
            listen: :6343
            workers: 4
            receive-buffer: 212992
  - path: /opt/akvorado/config/outlet.yaml
    permissions: "0644"
    owner: root:root
    content: |
      metadata:
        providers:
          - type: snmp
            credentials:
              ::/0:
                communities: public
      routing:
        provider:
          type: bmp
          receive-buffer: 212992
      core:
        exporter-classifiers:
          - ClassifySiteRegex(Exporter.Name, "^([^-]+)-", "$1")
          - ClassifyRegion("default")
          - ClassifyTenant("default")
          - ClassifyRole("edge")
        interface-classifiers:
          - |
            ClassifyConnectivityRegex(Interface.Description, "^(?i)(transit|pni|ppni|ix):? ", "$1") &&
            ClassifyProviderRegex(Interface.Description, "^\\S+?\\s(\\S+)", "$1") &&
            ClassifyExternal()
          - ClassifyInternal()
  - path: /opt/akvorado/config/console.yaml
    permissions: "0644"
    owner: root:root
    content: |
      http:
        cache:
          type: redis
          server: valkey:6379
      database:
        saved-filters: []
  - path: /opt/akvorado/clickhouse/server.xml
    permissions: "0644"
    owner: root:root
    content: |
      <clickhouse>
        <asynchronous_metric_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></asynchronous_metric_log>
        <metric_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></metric_log>
        <part_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></part_log>
        <text_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></text_log>
        <query_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></query_log>
        <query_thread_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></query_thread_log>
        <query_metric_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></query_metric_log>
        <query_views_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></query_views_log>
        <trace_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></trace_log>
        <error_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></error_log>
        <latency_log><ttl>event_date + INTERVAL 30 DAY DELETE</ttl></latency_log>
      </clickhouse>
  - path: /opt/akvorado/clickhouse/observability.xml
    permissions: "0644"
    owner: root:root
    content: |
      <clickhouse>
        <prometheus>
          <endpoint>/metrics</endpoint>
          <metrics>true</metrics>
          <events>true</events>
          <asynchronous_metrics>true</asynchronous_metrics>
        </prometheus>
        <logger>
          <level>fatal</level>
          <errorlog remove="1"></errorlog>
          <console>1</console>
          <console_log_level>information</console_log_level>
          <formatting>
            <type>json</type>
            <names>
              <date_time>date_time</date_time>
              <date_time_utc>date_time_utc</date_time_utc>
              <thread_name>thread_name</thread_name>
              <thread_id>thread_id</thread_id>
              <level>level</level>
              <query_id>query_id</query_id>
              <logger_name>logger_name</logger_name>
              <message>message</message>
              <source_file>source_file</source_file>
              <source_line>source_line</source_line>
            </names>
          </formatting>
        </logger>
      </clickhouse>
  - path: /etc/systemd/system/akvorado.service
    permissions: "0644"
    owner: root:root
    content: |
      [Unit]
      Description=Akvorado flow-collection stack
      Requires=docker.service
      Wants=network-online.target
      After=docker.service network-online.target

      [Service]
      Type=oneshot
      RemainAfterExit=yes
      WorkingDirectory=/opt/akvorado
      ExecStart=/usr/bin/docker compose -f /opt/akvorado/docker-compose.yml up -d --wait --wait-timeout 600
      ExecReload=/usr/bin/docker compose -f /opt/akvorado/docker-compose.yml up -d --wait --wait-timeout 600
      ExecStop=/usr/bin/docker compose -f /opt/akvorado/docker-compose.yml down
      TimeoutStartSec=900
      TimeoutStopSec=180

      [Install]
      WantedBy=multi-user.target
  - path: /usr/local/sbin/install-akvorado-stack
    permissions: "0750"
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      export DEBIAN_FRONTEND=noninteractive
      readonly DOCKER_KEY_SHA256='1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570'
      readonly DOCKER_KEY_FINGERPRINT='9DC858229FC7DD38854AE2D88D81803C0EBFCD88'

      test "$(dpkg --print-architecture)" = 'amd64'
      install -d -m 0755 /etc/apt/keyrings
      curl --fail --silent --show-error --location --retry 3 \
        --output /tmp/docker.asc https://download.docker.com/linux/ubuntu/gpg
      printf '%s  %s\n' "${DOCKER_KEY_SHA256}" /tmp/docker.asc | sha256sum --check --strict -
      gpg --batch --show-keys --with-colons /tmp/docker.asc \
        | grep -q "^fpr:::::::::${DOCKER_KEY_FINGERPRINT}:$"
      install -o root -g root -m 0644 /tmp/docker.asc /etc/apt/keyrings/docker.asc
      rm -f /tmp/docker.asc
      cat > /etc/apt/sources.list.d/docker.sources <<'DOCKER_SOURCES'
      Types: deb
      URIs: https://download.docker.com/linux/ubuntu
      Suites: noble
      Components: stable
      Architectures: amd64
      Signed-By: /etc/apt/keyrings/docker.asc
      DOCKER_SOURCES

      apt-get update
      apt-get install -y --no-install-recommends \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
      systemctl enable --now docker.service

      cd /opt/akvorado
      /usr/bin/docker compose -f /opt/akvorado/docker-compose.yml config --quiet
      /usr/bin/docker compose -f /opt/akvorado/docker-compose.yml pull
      systemctl daemon-reload
      systemctl enable --now akvorado.service
      systemctl is-active --quiet akvorado.service
runcmd:
  - [bash, /usr/local/sbin/install-akvorado-stack]
"""

CONFIG_NAME = "akvorado-2.4.0-ubuntu-2404-cloud-config"
CONFIG_VERSION = "2.4.0"
TEMPLATE_NAME = "akvorado-2.4.0-ubuntu-2404"
PROXMOX_ENDPOINT = "https://10.0.30.71:8006"
PROXMOX_NODE = "10.0.30.71"
TEMPLATE_VMID = 9070


def seed_akvorado(apps, schema_editor):
    PackerInstallerConfig = apps.get_model("netbox_packer", "PackerInstallerConfig")
    PackerTemplate = apps.get_model("netbox_packer", "PackerTemplate")

    config_defaults = {
        "os_family": "ubuntu",
        "installer_type": "cloud_config",
        "content": AKVORADO_CLOUD_CONFIG,
        "checksum": hashlib.sha256(AKVORADO_CLOUD_CONFIG.encode()).hexdigest(),
        "description": (
            "Akvorado 2.4.0 all-in-one flow collector with Kafka 4.2.0, "
            "Valkey 9.0, and ClickHouse 26.3. Docker Compose lifecycle is "
            "managed by akvorado.service; no secrets are baked."
        ),
    }
    config, config_created = PackerInstallerConfig.objects.get_or_create(
        name=CONFIG_NAME,
        version=CONFIG_VERSION,
        defaults=config_defaults,
    )
    if not config_created:
        mismatches = [field for field, expected in config_defaults.items() if getattr(config, field) != expected]
        if mismatches:
            raise RuntimeError(
                f"Akvorado seed naming collision: installer config {CONFIG_NAME!r} "
                f"version {CONFIG_VERSION!r} already exists with different values for "
                f"{', '.join(mismatches)}. Rename the existing row, or delete it if it is "
                "genuinely obsolete, then rerun the migration. No existing row was modified."
            )

    template_defaults = {
        "os_family": "ubuntu",
        "os_version": "24.04",
        "proxmox_template_id": TEMPLATE_VMID,
        "proxmox_endpoint": PROXMOX_ENDPOINT,
        "proxmox_node": PROXMOX_NODE,
        "storage_pool": "local",
        "cloud_init_ready": True,
        "build_status": "pending",
        "packer_template_ref": "",
        "install_qemu_guest_agent": True,
        "install_zabbix_agent2": True,
        "zabbix_server": "zabbix.nmulti.cloud",
        "install_nms_agent": True,
        "nms_agent_backend_url": "https://backend.nms.nmulti.cloud",
        "provisions_service": "akvorado",
        "installer_config": config,
        "description": (
            "Akvorado 2.4.0 golden cloud-init template (VMID 9070) on "
            "CLUSTER01-DC01. First boot starts the complete pinned Compose "
            "stack and self-registers the injected NMS host agent."
        ),
    }
    template, template_created = PackerTemplate.objects.get_or_create(
        name=TEMPLATE_NAME,
        defaults=template_defaults,
    )
    if not template_created:
        template_expected_values = {
            field: expected for field, expected in template_defaults.items() if field != "installer_config"
        }
        template_expected_values["installer_config_id"] = config.pk
        mismatches = [
            field for field, expected in template_expected_values.items() if getattr(template, field) != expected
        ]
        if mismatches:
            raise RuntimeError(
                f"Akvorado seed naming collision: template {TEMPLATE_NAME!r} already exists "
                f"with different values for {', '.join(mismatches)}. Rename the existing row, "
                "or delete it if it is genuinely obsolete, then rerun the migration. "
                "No existing row was modified."
            )


def unseed_akvorado(apps, schema_editor):
    # Golden-template seed rollbacks are intentionally non-destructive: an
    # operator may already have built VMID 9070 or edited the database rows.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_packer", "0023_packertemplate_nms_agent_and_service_marker"),
    ]

    operations = [
        migrations.RunPython(seed_akvorado, unseed_akvorado),
    ]
