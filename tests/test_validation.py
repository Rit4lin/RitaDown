import socket
import unittest
from unittest.mock import patch

from app.validation import URLValidationError, validate_media_url


PUBLIC_DNS_RESULT = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
]


class URLValidationTests(unittest.TestCase):
    @patch("app.validation.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT)
    def test_accepts_allowed_public_domains(self, _getaddrinfo) -> None:
        cases = (
            ("https://www.instagram.com/reel/example/", "www.instagram.com"),
            ("https://www.tiktok.com/@example/video/123", "www.tiktok.com"),
            ("https://vm.tiktok.com/example/", "vm.tiktok.com"),
            ("https://m.facebook.com/watch/?v=123", "m.facebook.com"),
            ("https://fb.watch/example", "fb.watch"),
            ("https://x.com/example/status/123", "x.com"),
            ("https://www.youtube.com/watch?v=example", "www.youtube.com"),
            ("https://www.reddit.com/r/videos/comments/abc/example/", "www.reddit.com"),
            ("https://redd.it/abc", "redd.it"),
            ("https://player.vimeo.com/video/123", "player.vimeo.com"),
            ("https://www.dailymotion.com/video/x123", "www.dailymotion.com"),
            ("https://dai.ly/x123", "dai.ly"),
            ("https://www.pinterest.com/pin/123/", "www.pinterest.com"),
            ("https://pin.it/example", "pin.it"),
            ("https://bsky.app/profile/example/post/abc", "bsky.app"),
            ("https://clips.twitch.tv/example", "clips.twitch.tv"),
        )
        for url, expected_host in cases:
            with self.subTest(url=url):
                self.assertEqual(validate_media_url(url).hostname, expected_host)

    def test_rejects_localhost_and_loopback(self) -> None:
        for url in (
            "http://localhost/video",
            "http://127.0.0.1/video",
            "http://[::1]/video",
        ):
            with self.subTest(url=url), self.assertRaises(URLValidationError):
                validate_media_url(url, resolve_dns=False)

    @patch(
        "app.validation.socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 0))
        ],
    )
    def test_rejects_private_dns_destination(self, _getaddrinfo) -> None:
        with self.assertRaisesRegex(URLValidationError, "red privada"):
            validate_media_url("https://www.instagram.com/reel/example/")

    @patch("app.validation.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT)
    def test_rejects_credentials(self, _getaddrinfo) -> None:
        with self.assertRaisesRegex(URLValidationError, "usuario"):
            validate_media_url("https://user:secret@x.com/example/status/123")

    def test_rejects_domains_outside_allowlist(self) -> None:
        for url in (
            "https://example.org/video",
            "https://instagram.com.evil.example/video",
            "https://notfacebook.com/video",
            "https://youtube.com.evil.example/video",
            "https://twitch.tv.evil.example/video",
        ):
            with self.subTest(url=url), self.assertRaises(URLValidationError):
                validate_media_url(url, resolve_dns=False)

    def test_rejects_non_http_schemes(self) -> None:
        for url in ("ftp://x.com/video", "file:///etc/passwd", "javascript:alert(1)"):
            with self.subTest(url=url), self.assertRaises(URLValidationError):
                validate_media_url(url, resolve_dns=False)

    @patch("app.validation.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT)
    def test_rejects_nonstandard_ports(self, _getaddrinfo) -> None:
        with self.assertRaisesRegex(URLValidationError, "puerto"):
            validate_media_url("https://x.com:8443/example/status/123")

    @patch(
        "app.validation.socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0)),
        ],
    )
    def test_rejects_any_non_public_dns_answer(self, _getaddrinfo) -> None:
        with self.assertRaisesRegex(URLValidationError, "red privada"):
            validate_media_url("https://facebook.com/watch/?v=123")


if __name__ == "__main__":
    unittest.main()
