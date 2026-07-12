import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logic_ctl


SAMPLE_AUVAL = """
    AU Validation Tool
    Copyright 2003-2019, Apple Inc. All Rights Reserved.

aumu dls  appl  -  Apple: DLSMusicDevice
aumu samp appl  -  Apple: AUSampler
aumu Dexd DGSB  -  Digital Suburban: Dexed
aumu SgXT VmbA  -  Surge Synth Team: Surge XT
aufx SFXT VmbA  -  Surge Synth Team: Surge XT Effects
augn ttsp appl  -  Apple: AUSpeechSynthesis
aumf mrof appl  -  Apple: AURoundTripAAC
some garbage line that should be ignored
aufx bpas appl  -  Apple: AUBandpass
"""


class TestParseAuval(unittest.TestCase):
    def test_parses_types_and_names(self):
        aus = logic_ctl._parse_auval(SAMPLE_AUVAL)
        self.assertEqual(len(aus), 8)
        surge = next(a for a in aus if a["name"] == "Surge XT")
        self.assertEqual(surge["type"], "instrument")
        self.assertEqual(surge["manufacturer"], "Surge Synth Team")
        fx = next(a for a in aus if a["name"] == "Surge XT Effects")
        self.assertEqual(fx["type"], "effect")

    def test_empty_input(self):
        self.assertEqual(logic_ctl._parse_auval(""), [])

    def test_garbage_ignored(self):
        names = [a["name"] for a in logic_ctl._parse_auval(SAMPLE_AUVAL)]
        self.assertNotIn("some garbage line that should be ignored", names)


class TestPureHelpers(unittest.TestCase):
    def test_unknown_transport_command(self):
        with self.assertRaises(logic_ctl.LogicError):
            logic_ctl.transport("moonwalk")

    def test_app_name_detection(self):
        import os.path as op
        real, logic_ctl._app_name = logic_ctl._app_name, None
        real_isdir = op.isdir
        try:
            os.path.isdir = lambda p: p == "/Applications/Logic Pro.app"
            self.assertEqual(logic_ctl.app_name(), "Logic Pro")
            logic_ctl._app_name = None
            os.path.isdir = lambda p: False
            self.assertEqual(logic_ctl.app_name(), "Logic Pro")  # sane default
        finally:
            os.path.isdir = real_isdir
            logic_ctl._app_name = real

    def test_select_track_range_check(self):
        # invalid index must fail before any UI is touched
        with self.assertRaises((logic_ctl.LogicError, ValueError, TypeError)):
            logic_ctl.select_track("not-a-number")


if __name__ == "__main__":
    unittest.main()
