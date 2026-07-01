/*
 * netbox-packer: OS version dropdown filter.
 *
 * Progressive enhancement for the Packer template form. The server already
 * renders os_version as a grouped <select> (optgroups by OS family), so the
 * dropdown works with JavaScript disabled. When enabled, this script narrows
 * the visible options to the OS family currently selected in os_family.
 *
 * It never destroys data: on the initial pass a stored version that is not in
 * the selected family's list is kept selectable so editing an older template
 * cannot fail. When the user actively changes the OS family, the version is
 * reset to blank so a stale value from the previous family can never linger
 * (e.g. OS family "Ubuntu" must not keep "Debian 13" selected). Options are
 * built with the DOM Option API, so untrusted strings are never interpolated
 * into raw markup.
 *
 * The os_version <select> is rendered with the ``no-ts`` class (see
 * PackerTemplateForm) so NetBox does not wrap it in Tom Select; that keeps this
 * native DOM manipulation authoritative over the visible dropdown.
 */
(function () {
  "use strict";

  function init() {
    var versionSelect = document.querySelector('select[name="os_version"]');
    var familySelect = document.querySelector('select[name="os_family"]');
    if (!versionSelect || !familySelect) {
      return;
    }

    var raw = versionSelect.getAttribute("data-os-version-map");
    if (!raw) {
      return;
    }

    var map;
    try {
      map = JSON.parse(raw);
    } catch (err) {
      return;
    }

    // preserveSelection=true keeps the current value selectable (initial/edit
    // pass); false clears it (the user switched OS family, so the previous
    // version no longer belongs to the new family).
    function populate(family, preserveSelection) {
      var current = preserveSelection ? versionSelect.value : "";
      var versions = (map && map[family]) || [];

      while (versionSelect.options.length) {
        versionSelect.remove(0);
      }

      // Blank placeholder so a required field still prompts a choice.
      versionSelect.add(new Option("---------", ""));

      var seen = Object.create(null);
      versions.forEach(function (pair) {
        var value = pair[0];
        var label = pair[1];
        versionSelect.add(new Option(label, value));
        seen[value] = true;
      });

      // Keep a previously stored / off-list value selectable (initial pass only).
      if (current && !seen[current]) {
        versionSelect.add(new Option(current + " (current)", current));
      }

      versionSelect.value = current || "";
    }

    // Initial pass preserves the server-rendered value (edit case).
    populate(familySelect.value, true);

    familySelect.addEventListener("change", function () {
      populate(familySelect.value, false);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
