"""Node affinity validation for Packer template builds."""
import logging

logger = logging.getLogger("netbox_packer.validators")

# x86-64-v2 minimum requirements per OS family / version
MIN_CPU_KNOWN_REQUIREMENTS = {
    "rhel9": "Nehalem",
    "rhel10": "Nehalem",
    "almalinux9": "Nehalem",
    "almalinux10": "Nehalem",
    "rocky9": "Nehalem",
    "rocky10": "Nehalem",
}

# CPU types that satisfy x86-64-v2 (Nehalem and newer)
X86_64_V2_SATISFYING_CPU_TYPES = {
    "host",
    "kvm64",
    "kvm32",
    "Nehalem",
    "Nehalem-IBRS",
    "Westmere",
    "Westmere-IBRS",
    "SandyBridge",
    "SandyBridge-IBRS",
    "IvyBridge",
    "IvyBridge-IBRS",
    "Haswell",
    "Haswell-IBRS",
    "Haswell-noTSX",
    "Haswell-noTSX-IBRS",
    "Broadwell",
    "Broadwell-IBRS",
    "Broadwell-noTSX",
    "Broadwell-noTSX-IBRS",
    "Skylake-Client",
    "Skylake-Client-IBRS",
    "Skylake-Client-noTSX-IBRS",
    "Skylake-Server",
    "Skylake-Server-IBRS",
    "Skylake-Server-noTSX-IBRS",
    "Cascadelake-Server",
    "Cascadelake-Server-noTSX",
    "Cooperlake",
    "Icelake-Client",
    "Icelake-Client-noTSX",
    "Icelake-Server",
    "Icelake-Server-noTSX",
    "Icelake-Server-v3",
    "Icelake-Server-v4",
    "Icelake-Server-v5",
    "Icelake-Server-v6",
    "Denverton",
    "Snowridge",
    "EPYC",
    "EPYC-IBPB",
    "EPYC-Rome",
    "EPYC-Rome-v2",
    "EPYC-Rome-v3",
    "EPYC-Milan",
    "EPYC-Milan-v2",
    "EPYC-Genoa",
    "max",
}


class NodeAffinityValidator:
    """
    Validates that a Proxmox node can satisfy a PackerTemplate's requirements.

    Checks performed:
    1. Node exists and is online
    2. min_cpu_type is satisfied (if specified)
    3. storage_pool is available on the node (if specified)

    Gracefully degrades when Proxmox endpoint is unreachable — logs a warning
    and returns no errors so builds are not blocked by connectivity issues.
    """

    def __init__(self, template):
        self.template = template
        self.errors = []
        self.warnings = []

    def validate(self, proxmox_endpoint=None, proxmox_node=None):
        """
        Run all affinity checks. Returns (is_valid, errors, warnings).

        Args:
            proxmox_endpoint: NMSBackend instance (falls back to template.proxmox_endpoint)
            proxmox_node: str node name (falls back to template.proxmox_node)
        """
        endpoint = proxmox_endpoint or self.template.proxmox_endpoint
        node = proxmox_node or self.template.proxmox_node

        self.errors = []
        self.warnings = []

        if not endpoint:
            self.errors.append("No Proxmox endpoint configured on the template.")
            return False, self.errors, self.warnings

        if not node:
            self.errors.append("No Proxmox node configured on the template.")
            return False, self.errors, self.warnings

        try:
            self._validate_with_proxmox(endpoint, node)
        except Exception as exc:
            # Proxmox unreachable — warn but allow the build to proceed
            logger.warning(
                "NodeAffinityValidator: could not reach Proxmox endpoint '%s': %s",
                endpoint,
                exc,
            )
            self.warnings.append(
                f"Could not reach Proxmox endpoint to validate node '{node}'. "
                "Proceeding without node affinity check."
            )

        is_valid = len(self.errors) == 0
        return is_valid, self.errors, self.warnings

    def _validate_with_proxmox(self, endpoint, node):
        """Inner validation using proxmox-sdk if available."""
        try:
            from proxmox_sdk.sdk import ProxmoxSDK
        except ImportError:
            self.warnings.append(
                "proxmox-sdk not installed — skipping live node validation."
            )
            return

        url = getattr(endpoint, "url", None) or str(endpoint)
        token = getattr(endpoint, "token", None) or getattr(endpoint, "auth_token", None)

        try:
            sdk = ProxmoxSDK(base_url=url, token=token, verify_ssl=False)
        except Exception as exc:
            raise RuntimeError(f"Failed to initialise ProxmoxSDK: {exc}") from exc

        self._check_node_exists(sdk, node)

        if self.template.min_cpu_type:
            self._check_cpu_type(sdk, node)

        if self.template.storage_pool:
            self._check_storage_pool(sdk, node)

    def _check_node_exists(self, sdk, node):
        """Verify the node is online in the Proxmox cluster."""
        try:
            nodes_response = sdk.nodes.get()
            node_names = {n.get("node") for n in (nodes_response or [])}
            if node not in node_names:
                self.errors.append(
                    f"Node '{node}' not found in Proxmox cluster. "
                    f"Available nodes: {', '.join(sorted(node_names)) or 'none'}"
                )
                return
            # Check node is online
            node_data = next(
                (n for n in (nodes_response or []) if n.get("node") == node), None
            )
            if node_data and node_data.get("status") != "online":
                self.warnings.append(
                    f"Node '{node}' status is '{node_data.get('status')}' (expected online)."
                )
        except Exception as exc:
            raise RuntimeError(f"Error checking node existence: {exc}") from exc

    def _check_cpu_type(self, sdk, node):
        """Check if the node satisfies the template's min_cpu_type requirement."""
        required = self.template.min_cpu_type
        if not required:
            return
        # For 'host' cpu_type, the real CPU is always >= any requirement
        # For others, check if required is in the satisfying set
        if required == "host":
            return  # always satisfies
        if required not in X86_64_V2_SATISFYING_CPU_TYPES:
            self.warnings.append(
                f"min_cpu_type '{required}' is not in the known satisfying set; "
                "cannot validate automatically."
            )
            return
        # We cannot introspect a remote node's physical CPU type without live data,
        # so emit a warning rather than an error when we cannot confirm
        self.warnings.append(
            f"min_cpu_type '{required}' requires x86-64-v2 support on node '{node}'. "
            "Ensure the node's physical CPU satisfies this requirement before building."
        )

    def _check_storage_pool(self, sdk, node):
        """Verify the template's storage_pool is available on the target node."""
        pool = self.template.storage_pool
        if not pool:
            return
        try:
            storage_response = sdk.nodes(node).storage.get()
            pool_names = {s.get("storage") for s in (storage_response or [])}
            if pool not in pool_names:
                self.errors.append(
                    f"Storage pool '{pool}' not found on node '{node}'. "
                    f"Available pools: {', '.join(sorted(pool_names)) or 'none'}"
                )
        except Exception as exc:
            raise RuntimeError(
                f"Error checking storage pool '{pool}' on node '{node}': {exc}"
            ) from exc
