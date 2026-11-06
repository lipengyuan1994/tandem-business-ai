"""Voice changes only the same-origin microphone permission; no runtime/network needed."""

import unittest

from support import ROOT


class VoicePermissionsTests(unittest.TestCase):
    def test_same_origin_microphone_camera_and_location_disabled(self):
        text = (ROOT / "deploy/compose/Caddyfile").read_text()
        self.assertIn('Permissions-Policy "camera=(), geolocation=(), microphone=(self)"', text)
        self.assertNotIn("microphone=()", text)
