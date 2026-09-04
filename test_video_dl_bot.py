import json
import unittest

from video_dl_bot import (
    DEFAULT_SETTINGS,
    build_audio_command,
    build_video_command,
    extract_substack_note_video_ids,
    format_command_for_log,
    is_substack_note_url,
)


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
