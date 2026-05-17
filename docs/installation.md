# Installation

## Requirements

- NetBox 4.5.x – 4.6.x
- Python 3.12+
- [`netbox-proxbox`](https://github.com/emersonfelipesp/netbox-proxbox) `>=0.0.16`
- A reachable [`proxbox-api`](https://github.com/emersonfelipesp/proxbox-api)
  instance with Packer-aware endpoints

## Install

```bash
pip install netbox-packer
```

In `configuration.py`:

```python
PLUGINS = [
    "netbox_proxbox",
    "netbox_packer",
]
```

```bash
python manage.py migrate
```
