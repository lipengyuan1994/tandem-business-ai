"""Voice changes only the same-origin microphone permission; no runtime/network needed."""

import unittest

from support import ROOT


class VoicePermissionsTests(unittest.TestCase):
    def test_same_origin_microphone_camera_and_location_disabled(self):
        config = (ROOT / "deploy/compose/Caddyfile").read_text()
        # Staff dictation belongs to the main site; the optional public clinic has no microphone feature.
        text = config.split("\n{$DOMAIN} {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('Permissions-Policy "camera=(), geolocation=(), microphone=(self)"', text)
        self.assertNotIn("microphone=()", text)

    def test_clinic_disables_microphone_camera_and_location(self):
        text = (ROOT / "deploy/compose/Caddyfile").read_text()
        clinic = text.split("\n{$DEMO_SITE_DOMAIN:(demo_site_disabled)} {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('Permissions-Policy "camera=(), geolocation=(), microphone=()"', clinic)
