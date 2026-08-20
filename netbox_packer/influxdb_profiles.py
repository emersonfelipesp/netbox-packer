"""Supported, version-pinned InfluxDB cloud-init template profiles."""

INFLUXDB_PROFILES = (
    {
        "id": "oss2-proxmox-metrics-2.9.1",
        "family": "oss2",
        "product": "InfluxDB OSS",
        "version": "2.9.1",
        "purpose": "Proxmox metrics and Flux workloads",
        "port": 8086,
        "template_name": "influxdb-oss-2.9.1-ubuntu-2404-proxmox-metrics",
        "installer_config_name": "influxdb-oss-2.9.1-ubuntu-2404-cloud-config",
        "default_vmid": 9050,
        "token_access": ["query", "writer"],
    },
    {
        "id": "core3-general-3.11.0",
        "family": "core3",
        "product": "InfluxDB 3 Core",
        "version": "3.11.0",
        "purpose": "General-purpose SQL, InfluxQL, and processing-engine workloads",
        "port": 8181,
        "template_name": "influxdb-core-3.11.0-ubuntu-2404",
        "installer_config_name": "influxdb-core-3.11.0-ubuntu-2404-cloud-config",
        "default_vmid": 9051,
        "token_access": ["admin"],
    },
)


__all__ = ["INFLUXDB_PROFILES"]
