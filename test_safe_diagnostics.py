import unittest
from public_job import stop_code
from public_payload import PublicationError
class DiagnosticsTests(unittest.TestCase):
 def test_diagnostic_does_not_include_external_unknown_key(self):self.assertEqual(stop_code(PublicationError('unreviewed fields: secret-value-from-source')),'public-schema:unknown-field')
 def test_diagnostic_uses_known_field_only(self):self.assertEqual(stop_code(PublicationError('invalid field type: content')),'public-schema:field-type:content');self.assertEqual(stop_code(PublicationError('invalid field type: arbitrary')),'public-schema:other')
if __name__=='__main__':unittest.main()
