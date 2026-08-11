import importlib.util
import json
import pathlib
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch


SERVER_PATH = pathlib.Path(__file__).with_name("server.py")
SPEC = importlib.util.spec_from_file_location("netease_mcp_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class NowPlayingTests(unittest.TestCase):
    def setUp(self):
        server.MCP_SECRET = "mcp-test-secret"
        server.NOW_PLAYING_REPORTER_SECRET = "report-test-secret"
        with server.NOW_PLAYING_LOCK:
            server.NOW_PLAYING_STATE = None

    def sample_payload(self, **overrides):
        payload = {
            "source_package": "com.netease.cloudmusic",
            "title": "Test Song",
            "artist": "Test Artist",
            "album": "Test Album",
            "status": "playing",
            "position_ms": 30000,
            "duration_ms": 180000,
            "playback_speed": 1.0,
            "captured_at": 1000,
        }
        payload.update(overrides)
        return payload

    def test_secret_paths_are_exact(self):
        self.assertTrue(server.is_authorized_mcp_path("/mcp/mcp-test-secret"))
        self.assertFalse(server.is_authorized_mcp_path("/mcp/mcp-test-secret-extra"))
        self.assertTrue(server.is_authorized_reporter_path("/now-playing/report-test-secret/?x=1"))
        self.assertFalse(server.is_authorized_reporter_path("/now-playing/wrong"))

    def test_only_netease_package_is_accepted(self):
        with self.assertRaisesRegex(ValueError, "Unsupported source package"):
            server.update_now_playing_state(
                self.sample_payload(source_package="com.example.messaging"), now=1000
            )

    def test_playing_position_is_extrapolated(self):
        server.update_now_playing_state(self.sample_payload(), now=1000)
        text = server.get_current_track(now=1005)
        self.assertIn("Test Song - Test Artist", text)
        self.assertIn("状态：播放中", text)
        self.assertIn("进度：00:35 / 03:00", text)

    def test_paused_position_does_not_advance(self):
        server.update_now_playing_state(
            self.sample_payload(status="paused"), now=1000
        )
        text = server.get_current_track(now=1010)
        self.assertIn("状态：已暂停", text)
        self.assertIn("进度：00:30 / 03:00", text)

    def test_stale_state_is_reported_offline(self):
        server.update_now_playing_state(self.sample_payload(), now=1000)
        self.assertIn("已离线", server.get_current_track(now=1091))

    def test_mcp_exposes_current_tools(self):
        response = server.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(11, len(names))
        self.assertIn("get_current_track", names)


class PlaylistUpdateTests(unittest.TestCase):
    def setUp(self):
        server.MCP_SECRET = "mcp-test-secret"
        server.NOW_PLAYING_REPORTER_SECRET = "report-test-secret"
        with server.NOW_PLAYING_LOCK:
            server.NOW_PLAYING_STATE = None

    def sample_payload(self, **overrides):
        payload = {
            "source_package": "com.netease.cloudmusic",
            "title": "Test Song",
            "artist": "Test Artist",
            "album": "Test Album",
            "status": "playing",
            "position_ms": 30000,
            "duration_ms": 180000,
            "playback_speed": 1.0,
            "captured_at": 1000,
        }
        payload.update(overrides)
        return payload

    def owned_playlist(self):
        return {
            "id": 123,
            "name": "Original name",
            "description": "Original description",
            "tags": ["rock"],
            "creator": {"userId": 7},
        }

    def test_update_playlist_rejects_collected_playlist(self):
        collected = self.owned_playlist()
        collected["creator"] = {"userId": 8}
        with patch.object(server, "get_uid", return_value=7), \
                patch.object(server, "netease_request", return_value={"playlist": collected}) as request:
            result = server.update_playlist_info(123, name="New name", confirm=True)
        self.assertIn("Refused", result)
        self.assertEqual(1, request.call_count)

    def test_update_playlist_preview_does_not_write(self):
        with patch.object(server, "get_uid", return_value=7), \
                patch.object(server, "netease_request", return_value={"playlist": self.owned_playlist()}) as request:
            result = server.update_playlist_info(123, name="New name")
        self.assertIn("Preview only", result)
        self.assertIn("New name", result)
        self.assertEqual(1, request.call_count)

    def test_update_playlist_preserves_unset_description(self):
        calls = []

        def fake_request(url, data=None):
            calls.append((url, data))
            if "playlist/detail" in url:
                return {"playlist": self.owned_playlist()}
            return {"code": 200}

        with patch.object(server, "get_uid", return_value=7), \
                patch.object(server, "get_csrf", return_value="csrf-token"), \
                patch.object(server, "netease_request", side_effect=fake_request):
            result = server.update_playlist_info(123, name="New name", confirm=True)
        self.assertIn("Updated playlist ID 123", result)
        self.assertEqual(2, len(calls))
        self.assertEqual("New name", calls[1][1]["name"])
        self.assertEqual("Original description", calls[1][1]["desc"])
        self.assertEqual('["rock"]', calls[1][1]["tags"])

    def test_mcp_exposes_playlist_update_tool(self):
        response = server.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(11, len(names))
        self.assertIn("update_playlist_info", names)

    def test_report_endpoint_and_mcp_tool_work_end_to_end(self):
        httpd = server.ThreadedHTTPServer(("127.0.0.1", 0), server.MCPHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base_url = "http://127.0.0.1:%d" % httpd.server_address[1]
        try:
            report_request = urllib.request.Request(
                base_url + "/now-playing/report-test-secret",
                data=json.dumps(self.sample_payload()).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(report_request, timeout=3) as response:
                self.assertEqual({"ok": True}, json.load(response))

            probe_request = urllib.request.Request(
                base_url + "/now-playing/report-test-secret",
                data=json.dumps({"test_only": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(probe_request, timeout=3) as response:
                self.assertEqual({"ok": True}, json.load(response))

            mcp_body = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_current_track", "arguments": {}},
            }
            mcp_request = urllib.request.Request(
                base_url + "/mcp/mcp-test-secret",
                data=json.dumps(mcp_body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(mcp_request, timeout=3) as response:
                result = json.load(response)
            self.assertIn("Test Song", result["result"]["content"][0]["text"])

            wrong_secret = urllib.request.Request(
                base_url + "/now-playing/wrong",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(wrong_secret, timeout=3)
                self.fail("Wrong reporter secret should return 404")
            except urllib.error.HTTPError as error:
                self.assertEqual(404, error.code)
                error.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
