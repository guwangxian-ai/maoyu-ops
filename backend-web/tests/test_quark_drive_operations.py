"""Regression checks for user confirmation, provider arguments and isolation."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.quark_drive_auth import QuarkAuthError
from app.services.quark_drive_operations import OperationRequest, build_arguments, validate_share_url
from app.services.quark_drive_jobs import private_path, public_data
from app.services.quark_drive_plans import RenameItem, validate_rename_items
from app.api.routes.quark_drive import safe_upload_name


class OperationBoundaryTests(unittest.TestCase):
    def test_mutation_requires_explicit_confirmation(self):
        for op in ('create-folder', 'move', 'download', 'organize', 'saveas', 'upload-delete'):
            with self.subTest(op=op), self.assertRaises(QuarkAuthError):
                build_arguments(op, OperationRequest())

    def test_rejects_host_commands_and_option_injection(self):
        for op, request in [('rm', OperationRequest(confirmed=True)),
                            ('move', OperationRequest(confirmed=True, fids=['--help'], target_fid='0')),
                            ('upload-delete', OperationRequest(confirmed=True, record_id='--help'))]:
            with self.subTest(op=op), self.assertRaises(QuarkAuthError):
                build_arguments(op, request)

    def test_update_all_cannot_change_target_or_narrow_scope(self):
        for extra in ({'target_fid': '0'}, {'fids': ['file-1']}):
            with self.assertRaises(QuarkAuthError):
                build_arguments('saveas-update', OperationRequest(confirmed=True, url='https://pan.quark.cn/s/abc', **extra))

    def test_default_save_target_is_omitted(self):
        _, args = build_arguments('saveas', OperationRequest(confirmed=True, url='https://pan.quark.cn/s/abc', fids=['file-1']))
        self.assertNotIn('--to-pdir-fid', args)
        self.assertIn('--fid-list', args)

    def test_search_root_marker_is_omitted_but_explicit_folder_is_preserved(self):
        _, root = build_arguments('search', OperationRequest(keyword='文件', parent_fid='0'))
        _, folder = build_arguments('search', OperationRequest(keyword='文件', parent_fid='folder-full-id'))
        self.assertNotIn('--parent-fid', root)
        self.assertEqual(folder[-2:], ['--parent-fid', 'folder-full-id'])

    def test_share_url_rejects_credentials_ports_other_hosts(self):
        for url in ('file:///tmp/a', 'https://evil.invalid/s/a', 'https://pan.quark.cn:bad/s/a',
                    'https://user@pan.quark.cn/s/a', 'https://pan.quark.cn:22/s/a', 'https://pan.quark.cn/other'):
            with self.subTest(url=url), self.assertRaises(QuarkAuthError):
                validate_share_url(url)

    def test_upload_paths_cannot_escape_or_alias_directory(self):
        for name in ('../private', '/private', 'a/../b', 'a//b', 'a/./b', 'a\\b', ''):
            with self.subTest(name=name), self.assertRaises(QuarkAuthError):
                safe_upload_name(name)
        self.assertEqual(str(safe_upload_name('文件夹/样本.txt')), '文件夹/样本.txt')

    def test_cross_user_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'QUARK_AUTH_DIR': directory}):
            with self.assertRaises(QuarkAuthError):
                private_path(17, Path(directory) / 'user-18/secret')

    def test_public_results_strip_credentials_and_private_paths_recursively(self):
        result = public_data({'files': [{'fid': 'ok', 'share_fid_token': 'secret'}],
                              'data': {'accessToken': 'secret', 'record_file': '/private/x', 'text_block': {'text': 'ok'}}})
        self.assertEqual(result, {'files': [{'fid': 'ok'}], 'data': {'text_block': {'text': 'ok'}}})


class RenameBoundaryTests(unittest.TestCase):
    def test_full_ids_with_same_provider_identity_are_duplicates(self):
        items = [RenameItem(fid='prefix|same', parent_fid='p', old_name='a', new_name='b'),
                 RenameItem(fid='other|same', parent_fid='p', old_name='c', new_name='d')]
        with self.assertRaises(QuarkAuthError):
            validate_rename_items(items)

    def test_provider_utf16_name_limit_is_enforced(self):
        with self.assertRaises(QuarkAuthError):
            validate_rename_items([RenameItem(fid='a', parent_fid='p', old_name='a', new_name='😀' * 128)])

    def test_cloud_names_are_not_interpreted_as_host_paths(self):
        validate_rename_items([RenameItem(fid='a', parent_fid='p', old_name='a', new_name='资料 / 课程.txt')])

    def test_stale_preview_is_rejected_before_job_creation(self):
        import asyncio
        from app.services import quark_drive_plans as plans
        with patch.object(plans, 'list_files', AsyncMock(return_value={'files': [{'fid': 'a', 'filename': 'changed'}]})), \
             patch.object(plans, 'create_job', AsyncMock()) as create:
            with self.assertRaises(QuarkAuthError):
                asyncio.run(plans.prepare_rename(17, plans.RenamePlanRequest(items=[RenameItem(fid='a', parent_fid='p', old_name='old', new_name='new')])))
            create.assert_not_called()


class DownloadRecoveryTests(unittest.TestCase):
    def test_resume_uses_original_file_directory_and_rejects_unbound_records(self):
        import asyncio
        from app.services import quark_drive_jobs as jobs
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'QUARK_AUTH_DIR': directory}):
            original = Path(directory) / 'user-17/jobs/original/downloads'
            original.mkdir(parents=True)
            row = SimpleNamespace(params={'_account_id': 'account-a', '_download_output_dir': str(original), '_download_records': ['rec-original']})
            session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [row])))
            context = MagicMock()
            context.__aenter__ = AsyncMock(return_value=session)
            context.__aexit__ = AsyncMock(return_value=None)
            with patch.object(jobs, 'async_session_maker', return_value=context), patch.object(jobs, '_account_key', return_value='account-a'):
                self.assertEqual(asyncio.run(jobs.original_download_output(17, 'rec-original')), original.resolve())
                with self.assertRaises(QuarkAuthError):
                    asyncio.run(jobs.original_download_output(17, 'rec-unknown'))

    def test_only_confirmed_complete_downloads_are_published(self):
        from app.services import quark_drive_jobs as jobs
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'QUARK_AUTH_DIR': directory}):
            output = Path(directory) / 'user-17/downloads'
            output.mkdir(parents=True)
            (output / 'complete.txt').write_text('complete')
            (output / 'partial.txt').write_text('partial')
            outside = Path(directory) / 'user-18-secret.txt'; outside.write_text('private')
            result = jobs._download_manifest(17, 'a' * 32, output, [str(output / 'complete.txt'), str(outside)])
            self.assertEqual([row['name'] for row in result], ['complete.txt'])


if __name__ == '__main__':
    unittest.main()
