import importlib.util, os
from django.test import SimpleTestCase

class UILayoutAuditTest(SimpleTestCase):
    """Fails the test run if a template has a stray closing tag or a known layout-breaking CSS pattern."""
    def test_no_layout_errors(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'ui_audit.py')
        spec = importlib.util.spec_from_file_location('ui_audit', path)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        errors, _ = mod.run()
        self.assertEqual(errors, [], '\n'.join(errors))
