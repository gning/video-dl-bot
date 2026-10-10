import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Bot
from telegram.error import NetworkError
from telegram.ext import Application
from telegram.request import BaseRequest

import video_dl_bot

from video_dl_bot import (
    DEFAULT_SETTINGS,
    build_audio_command,
    build_video_command,
    extract_substack_note_video_ids,
    format_command_for_log,
    is_substack_note_url,
    send_audio_file,
    send_video,
)


class LocalUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='video-dl-upload-test-')
        self.addCleanup(self.temp_dir.cleanup)
        self.media_path = Path(self.temp_dir.name) / 'large video.mp4'
        # A sparse file reproduces the failed upload's size without allocating
        # its contents in RAM or using that much disk space.
        with self.media_path.open('wb') as media:
            media.truncate(1223223466)

        self.request = MagicMock(spec=BaseRequest)
        self.request.post = AsyncMock(return_value={
            'message_id': 1,
            'date': 0,
            'chat': {'id': 123, 'type': 'private'},
        })
        self.bot = Bot(
            token='123456:local-upload-test',
            base_url='http://127.0.0.1:8081/bot',
            request=self.request,
            get_updates_request=self.request,
            local_mode=True,
        )
        self.context = SimpleNamespace(bot=self.bot)
        self.update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=123),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )

    def assert_path_request(self, parameter):
        self.request.post.assert_awaited_once()
        kwargs = self.request.post.call_args.kwargs
        data = kwargs['request_data']
        self.assertEqual(data.parameters[parameter], self.media_path.as_uri())
        self.assertEqual(data.multipart_data, {})
        self.assertLess(len(data.json_payload), 2048)
        self.assertGreaterEqual(kwargs['read_timeout'], 1800)
        self.assertGreaterEqual(kwargs['write_timeout'], 1800)

    async def test_large_video_is_serialized_as_path_without_reading_contents(self):
        with patch('builtins.open', side_effect=AssertionError('Video must not be read')), \
             patch.object(Path, 'open', side_effect=AssertionError('Video must not be read')):
            await send_video(self.update, self.context, str(self.media_path))
        self.assert_path_request('document')
        self.assertTrue(self.media_path.exists())

    async def test_audio_uses_same_path_transport_and_preserves_caption(self):
        with patch('builtins.open', side_effect=AssertionError('Audio must not be read')), \
             patch.object(Path, 'open', side_effect=AssertionError('Audio must not be read')):
            success = await send_audio_file(
                self.update, self.context, str(self.media_path), 'Audio caption'
            )
        self.assertTrue(success)
        self.assert_path_request('audio')
        data = self.request.post.call_args.kwargs['request_data']
        self.assertEqual(data.parameters['caption'], 'Audio caption')

    async def test_failed_video_upload_propagates_and_keeps_file(self):
        self.request.post.side_effect = NetworkError('Upload failed')
        with self.assertRaisesRegex(NetworkError, 'Upload failed'):
            await send_video(self.update, self.context, str(self.media_path))
        self.assertTrue(self.media_path.exists())

    async def test_missing_video_is_rejected_before_request(self):
        with self.assertRaises(FileNotFoundError):
            await send_video(self.update, self.context, str(self.media_path.parent / 'missing.mp4'))
        self.request.post.assert_not_awaited()

    async def test_startup_enables_local_paths_on_configured_api_server(self):
        builder = Application.builder()
        with patch.object(video_dl_bot.Application, 'builder', return_value=builder), \
             patch.object(video_dl_bot.Application, 'run_polling', autospec=True) as poll, \
             patch.object(video_dl_bot, 'BOT_TOKEN', '123456:local-upload-test'), \
             patch.object(video_dl_bot, 'LOCAL_BOT_API_BASE', 'http://127.0.0.1:18081'), \
             patch.object(video_dl_bot, 'SUBDIR', self.temp_dir.name), \
             patch.object(video_dl_bot, 'load_settings'):
            video_dl_bot.main()

        poll.assert_called_once()
        app = poll.call_args.args[0]
        self.assertTrue(app.bot.local_mode)
        self.assertEqual(app.bot.base_url,
                         'http://127.0.0.1:18081/bot123456:local-upload-test')
        self.assertEqual(app.bot.base_file_url,
                         'http://127.0.0.1:18081/file/bot123456:local-upload-test')


def make_substack_page(attachments):
    preloads = {
        'feedData': {
            'feedItem': {
                'comment': {
                    'attachments': attachments,
                },
            },
        },
    }
    encoded_preloads = json.dumps(json.dumps(preloads))
    return f'<script>window._preloads = JSON.parse({encoded_preloads});</script>'


class SubstackNoteTests(unittest.TestCase):
    def test_recognizes_substack_note_urls(self):
        self.assertTrue(is_substack_note_url(
            'https://substack.com/@lunarresearcher/note/c-316846868'
        ))
        self.assertTrue(is_substack_note_url(
            'https://example.substack.com/note/c-123?utm_source=share'
        ))
        self.assertFalse(is_substack_note_url(
            'https://example.substack.com/p/an-article'
        ))
        self.assertFalse(is_substack_note_url(
            'https://evilsubstack.com/note/c-123'
        ))

    def test_extracts_all_native_video_upload_ids(self):
        webpage = make_substack_page([
            {'type': 'post', 'post': {'id': 1}},
            {
                'type': 'video',
                'mediaUpload': {'id': 'video-one', 'media_type': 'video'},
            },
            {
                'type': 'image',
                'mediaUpload': {'id': 'image-one', 'media_type': 'image'},
            },
            {
                'type': 'video',
                'videoUpload': {'id': 'video-two'},
            },
            {
                'type': 'video',
                'mediaUpload': {'id': 'video-one', 'media_type': 'video'},
            },
            {
                'type': 'video',
                'mediaUpload': {'media_type': 'video'},
                'videoUpload': {'id': 'video-three'},
            },
        ])

        self.assertEqual(
            extract_substack_note_video_ids(webpage),
            ['video-one', 'video-two', 'video-three']
        )

    def test_rejects_note_without_video(self):
        with self.assertRaisesRegex(RuntimeError, 'no downloadable videos'):
            extract_substack_note_video_ids(make_substack_page([]))

    def test_uses_unique_names_for_multiple_videos(self):
        media_urls = ['https://example.com/one.m3u8', 'https://example.com/two.m3u8']
        command = build_video_command(
            'https://substack.com/@author/note/c-123',
            'downloads/c-123',
            DEFAULT_SETTINGS,
            media_urls=media_urls
        )

        output_index = command.index('-o')
        self.assertEqual(
            command[output_index + 1],
            'downloads/c-123_%(autonumber)03d.%(ext)s'
        )
        self.assertEqual(command[-2:], media_urls)

        audio_command = build_audio_command(
            'https://substack.com/@author/note/c-123',
            'downloads/c-123-audio',
            DEFAULT_SETTINGS,
            media_urls=media_urls
        )
        output_index = audio_command.index('-o')
        self.assertEqual(
            audio_command[output_index + 1],
            'downloads/c-123-audio_%(autonumber)03d.%(ext)s'
        )
        self.assertEqual(audio_command[-2:], media_urls)

    def test_redacts_proxy_credentials_from_command_log(self):
        logged = format_command_for_log([
            'yt-dlp', '--proxy', 'http://user:secret@example.com:8080',
            'https://example.com/video'
        ])
        self.assertNotIn('secret', logged)
        self.assertIn('--proxy [REDACTED]', logged)


if __name__ == '__main__':
    unittest.main()
