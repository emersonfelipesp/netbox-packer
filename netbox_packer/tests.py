from django.test import SimpleTestCase

from netbox_packer import config


class PackerPluginIntegrationTests(SimpleTestCase):
    def test_plugin_config_is_loaded_by_netbox(self) -> None:
        self.assertEqual(config.name, "netbox_packer")
        self.assertEqual(config.min_version, "4.5.8")
        self.assertEqual(config.max_version, "4.7.0")

    def test_plugin_api_routes_are_registered(self) -> None:
        from netbox_packer.api.urls import router

        route_names = {prefix for prefix, _viewset, _kwargs in router.registry}
        self.assertSetEqual(
            route_names,
            {"templates", "builds", "installer-configs", "build-targets"},
        )
