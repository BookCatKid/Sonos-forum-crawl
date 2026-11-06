import unittest
from unittest.mock import patch
from scheduled_capture import scheduled
class ScheduledTests(unittest.TestCase):
 def test_one500batch_uses_selected_tip(self):
  calls=[]
  with patch('parser_migration.apply_attachment_shapes',return_value=[]),patch('parser_migration.apply_reply_shapes',return_value=[]),patch('recovery.recover_terminated',return_value=[]),patch('unattended.cycle_action',return_value='collect'),patch('scheduled_capture.current_generation',return_value='checkpoint-tip'),patch('scheduled_capture.restore'),patch('scheduled_capture.check_public'),patch('scheduled_capture.PublicStore') as store,patch('scheduled_capture.phase_tasks',return_value=[{'url':'https://en.community.sonos.com/contentNavigation/topics'}]):
   store.return_value.db.execute.return_value.fetchone.return_value=['listing']
   scheduled(object(),'unused','123',resume=lambda *a,**k:calls.append((a,k)))
  self.assertEqual(len(calls),1);self.assertEqual(calls[0][0][1],'checkpoint-tip');self.assertEqual(calls[0][0][4],500);self.assertTrue(calls[0][1]['allow_large_batch'])
 def test_bad_run_id_never_resumes(self):
  with patch('parser_migration.apply_attachment_shapes',return_value=[]),patch('parser_migration.apply_reply_shapes',return_value=[]),patch('recovery.recover_terminated',return_value=[]),patch('unattended.cycle_action',return_value='collect'),patch('scheduled_capture.current_generation',return_value='checkpoint-tip'),patch('scheduled_capture.restore'),patch('scheduled_capture.check_public'),patch('scheduled_capture.PublicStore') as store,patch('scheduled_capture.phase_tasks',return_value=[{'url':'https://en.community.sonos.com/contentNavigation/topics'}]):
   store.return_value.db.execute.return_value.fetchone.return_value=['listing']
   with self.assertRaises(ValueError):scheduled(object(),'unused','notnumeric')
if __name__=='__main__':unittest.main()
