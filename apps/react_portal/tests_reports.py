import tempfile
from pathlib import Path
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

class ExcelReportTests(TestCase):
 def setUp(self):
  self.user=get_user_model().objects.create_user(username='report-test',password='report-test')
 def test_authentication_required(self):
  self.assertEqual(self.client.get('/api/v1/reports/excel/').status_code,401)
 def test_download_is_authenticated_and_fixed(self):
  self.client.force_login(self.user)
  with tempfile.TemporaryDirectory() as folder,override_settings(ID3_EXCEL_REPORT_DIR=folder):
   path=Path(folder)/'Carteira_Ana_Acompanhamento.xlsx';path.write_bytes(b'example-private-file')
   metadata=self.client.get('/api/v1/reports/excel/?account=ana').json()
   self.assertTrue(metadata['available']);self.assertIn('updatedAt',metadata)
   response=self.client.get('/api/v1/reports/excel/?account=ana&download=1')
   self.assertEqual(response.status_code,200);self.assertIn('attachment',response['Content-Disposition']);self.assertIn('no-store',response['Cache-Control'])
   self.assertEqual(b''.join(response.streaming_content),b'example-private-file')
   self.assertFalse(self.client.get('/api/v1/reports/excel/?account=bruno').json()['available'])
   self.assertEqual(self.client.get('/api/v1/reports/excel/?account=bruno&download=1').status_code,404)
   self.assertEqual(self.client.get('/api/v1/reports/excel/?account=../../').status_code,400)
