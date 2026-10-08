# Installation

## Requirements

- NetBox 4.5.8 through 4.6.x, plus the NetBox 4.7 series (4.7.0 GA through 4.7.99)
- Python 3.12+
- Optional HCP Packer Registry credentials for registry synchronization

## Install

```bash
pip install netbox-packer
```

In `configuration.py`:

```python
PLUGINS = [
    "netbox_packer",
]
```

```bash
python manage.py migrate
```
