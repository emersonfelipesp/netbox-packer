# Compatibility Matrix

> `proxbox-api` is a separately deployed backend service, not a Python package dependency.
> `netbox-packer` communicates with it over REST.

| netbox-packer | NetBox | Python | netbox-proxbox | proxbox-api | pydantic |
|---|---|---|---|---|---|
| v0.0.4 | v4.5.8, v4.5.9, v4.6.0-v4.6.4 | ≥3.12 | Optional | Required | ≥2.0.0 |
| v0.0.2.post2 | 4.5.8 – 4.6.x | ≥3.12 | Optional | Required | ≥2.0.0 |
| v0.0.2 | 4.5.x – 4.6.x | ≥3.12 | ≥0.0.16 | Required | ≥2.0.0 |
