/*
 * netbox-packer: OS version dropdown filter.
 *
 * Progressive enhancement for the Packer template form. The server already
 * renders os_version as a grouped <select> (optgroups by OS family), so the
 * dropdown works with JavaScript disabled. When enabled, this script narrows
 * the visible options to the OS family currently selected in os_family.
 *
 * It never destroys data: a stored version that is not in the selected
 * family's list is kept selectable so editing an older template cannot fail.
 * Options are built with the DOM Option API, so untrusted strings are never
 * interpolated into raw markup.
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

    function populate(family) {
      var current = versionSelect.value;
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

      // Keep a previously stored / off-list value selectable.
      if (current && !seen[current]) {
        versionSelect.add(new Option(current + " (current)", current));
      }

      versionSelect.value = current || "";
    }

    // Initial pass preserves the server-rendered value (edit case).
    populate(familySelect.value);

    familySelect.addEventListener("change", function () {
      populate(familySelect.value);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
