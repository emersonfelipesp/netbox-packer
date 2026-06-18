# Installation

## Requirements

- NetBox 4.5.8, 4.5.9, and 4.6.0 through 4.6.3
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
