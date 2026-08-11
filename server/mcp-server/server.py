#!/usr/bin/env python3
import http.server, json, os, urllib.request, urllib.parse, threading, uuid, time, hmac
from http.server import HTTPServer

NETEASE_COOKIE = os.environ.get("NETEASE_COOKIE", "")
MCP_SECRET = os.environ.get("MCP_SECRET", "")
NOW_PLAYING_REPORTER_SECRET = os.environ.get("NOW_PLAYING_REPORTER_SECRET", "")
PORT = int(os.environ.get("MCP_PORT", "3456"))
SESSION_ID = str(uuid.uuid4())
NOW_PLAYING_STALE_SECONDS = 90
MAX_REPORT_BYTES = 8192
ALLOWED_NETEASE_PACKAGES = {"com.netease.cloudmusic"}
NOW_PLAYING_STATE = None
NOW_PLAYING_LOCK = threading.Lock()

def is_authorized_mcp_path(path):
    """Allow MCP requests only at /mcp/<MCP_SECRET>."""
    if not MCP_SECRET:
        return False
    request_path = path.split("?", 1)[0].rstrip("/")
    expected_path = "/mcp/" + MCP_SECRET
    return hmac.compare_digest(request_path, expected_path)

def is_authorized_reporter_path(path):
    """Allow Android reports only at /now-playing/<NOW_PLAYING_REPORTER_SECRET>."""
    if not NOW_PLAYING_REPORTER_SECRET:
        return False
    request_path = path.split("?", 1)[0].rstrip("/")
    expected_path = "/now-playing/" + NOW_PLAYING_REPORTER_SECRET
    return hmac.compare_digest(request_path, expected_path)

def _clean_text(value, max_length):
    if value is None:
        return ""
    return str(value).strip()[:max_length]

def _clean_non_negative_number(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number

def update_now_playing_state(payload, now=None):
    """Validate and store a privacy-scoped Android NetEase playback snapshot."""
    if not isinstance(payload, dict):
        raise ValueError("JSON object required")
    package_name = _clean_text(payload.get("source_package"), 128)
    if package_name not in ALLOWED_NETEASE_PACKAGES:
        raise ValueError("Unsupported source package")
    status = _clean_text(payload.get("status"), 16).lower()
    if status not in {"playing", "paused", "stopped", "idle", "unknown"}:
        raise ValueError("Invalid playback status")
    received_at = float(now if now is not None else time.time())
    state = {
        "source_package": package_name,
        "title": _clean_text(payload.get("title"), 300),
        "artist": _clean_text(payload.get("artist"), 300),
        "album": _clean_text(payload.get("album"), 300),
        "status": status,
        "position_ms": _clean_non_negative_number(payload.get("position_ms")),
        "duration_ms": _clean_non_negative_number(payload.get("duration_ms")),
        "playback_speed": _clean_non_negative_number(payload.get("playback_speed")),
        "captured_at": _clean_non_negative_number(payload.get("captured_at")),
        "received_at": received_at,
    }
    global NOW_PLAYING_STATE
    with NOW_PLAYING_LOCK:
        NOW_PLAYING_STATE = state
    return state

def _format_milliseconds(value):
    if value is None:
        return "未知"
    total_seconds = max(0, int(value / 1000))
    return "%02d:%02d" % (total_seconds // 60, total_seconds % 60)

def get_current_track(now=None):
    """Return the freshest Android playback snapshot to the MCP client."""
    with NOW_PLAYING_LOCK:
        state = dict(NOW_PLAYING_STATE) if NOW_PLAYING_STATE else None
    if not state:
        return "手机尚未上报网易云播放状态。请确认 Android 采集器已运行并获得通知使用权。"
    current_time = float(now if now is not None else time.time())
    age = max(0.0, current_time - state["received_at"])
    if age > NOW_PLAYING_STALE_SECONDS:
        return "手机播放状态已离线（最后更新于 %d 秒前）。" % int(age)
    status = state["status"]
    if status in {"stopped", "idle", "unknown"}:
        return "手机网易云当前没有正在播放的歌曲（状态更新于 %d 秒前）。" % int(age)
    position_ms = state.get("position_ms")
    if status == "playing" and position_ms is not None:
        speed = state.get("playback_speed")
        if speed is None:
            speed = 1.0
        position_ms += age * 1000 * speed
    duration_ms = state.get("duration_ms")
    if position_ms is not None and duration_ms is not None:
        position_ms = min(position_ms, duration_ms)
    status_text = "播放中" if status == "playing" else "已暂停"
    title = state.get("title") or "未知歌曲"
    artist = state.get("artist") or "未知歌手"
    lines = [
        "当前播放：%s - %s" % (title, artist),
        "状态：%s" % status_text,
        "进度：%s / %s" % (_format_milliseconds(position_ms), _format_milliseconds(duration_ms)),
        "状态更新时间：%d 秒前" % int(age),
    ]
    if state.get("album"):
        lines.insert(1, "专辑：%s" % state["album"])
    return "\n".join(lines)

def netease_request(url, data=None):
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://music.163.com/', 'Cookie': NETEASE_COOKIE, 'Content-Type': 'application/x-www-form-urlencoded' if data else 'application/json'}
    if data and isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    elif data and isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"code": -1, "error": str(e)}

def get_uid():
    resp = netease_request('https://music.163.com/api/nuser/account/get')
    try:
        return resp.get('profile', {}).get('userId') or resp.get('account', {}).get('id')
    except:
        return None

def get_csrf():
    for part in NETEASE_COOKIE.split(';'):
        part = part.strip()
        if part.startswith('__csrf='):
            return part.split('=', 1)[1]
    return ''

def play_music(query, note=None):
    url = 'https://music.163.com/api/search/get?s=' + urllib.parse.quote(query) + '&type=1&limit=5'
    resp = netease_request(url)
    songs = resp.get('result', {}).get('songs', [])
    if not songs:
        return "No results for '" + query + "'"
    s = songs[0]
    song_id = s.get('id')
    try:
        dd = netease_request('https://music.163.com/api/song/detail?ids=[' + str(song_id) + ']')
        pic_url = dd['songs'][0]['album'].get('picUrl', '')
    except:
        pic_url = ''
    name = s.get('name', '').replace(':', '\uff1a')
    artist = ', '.join([a.get('name', '') for a in s.get('artists', [])]).replace(':', '\uff1a')
    link = "https://music.163.com/song?id=" + str(song_id)
    return "[music:" + str(song_id) + ":" + name + ":" + artist + ":" + pic_url + "]" + (note or '') + "\n" + link

def create_playlist(name, description='', privacy=0):
    csrf = get_csrf()
    url = 'https://music.163.com/api/playlist/create?csrf_token=' + csrf
    data = {'name': name, 'privacy': str(privacy), 'type': 'NORMAL'}
    if description:
        data['description'] = description
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        pl = resp.get('playlist', {})
        return "Created playlist '" + name + "' (ID: " + str(pl.get('id')) + ")"
    return "Failed: " + resp.get('message', resp.get('error', 'unknown'))

def add_to_playlist(playlist_id, song_ids):
    csrf = get_csrf()
    if isinstance(song_ids, str):
        ids = [s.strip() for s in song_ids.split(',')]
    else:
        ids = [str(song_ids)]
    url = 'https://music.163.com/api/playlist/manipulate/tracks?csrf_token=' + csrf
    data = {'op': 'add', 'pid': str(playlist_id), 'trackIds': json.dumps([int(i) for i in ids])}
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        return "Added " + str(len(ids)) + " song(s) to playlist " + str(playlist_id)
    if resp.get('code') == 502:
        return "Song already in playlist"
    return "Failed: " + resp.get('message', resp.get('error', 'unknown'))

def remove_from_playlist(playlist_id, song_ids):
    csrf = get_csrf()
    if isinstance(song_ids, str):
        ids = [s.strip() for s in song_ids.split(',')]
    else:
        ids = [str(song_ids)]
    url = 'https://music.163.com/api/playlist/manipulate/tracks?csrf_token=' + csrf
    data = {'op': 'del', 'pid': str(playlist_id), 'trackIds': json.dumps([int(i) for i in ids])}
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        return "Removed " + str(len(ids)) + " song(s) from playlist " + str(playlist_id)
    return "Failed: " + resp.get('message', resp.get('error', 'unknown'))

def list_my_playlists():
    uid = get_uid()
    if not uid:
        return "Failed to get user ID. Cookie may be expired."
    url = 'https://music.163.com/api/user/playlist?uid=' + str(uid) + '&limit=50&offset=0'
    resp = netease_request(url)
    playlists = resp.get('playlist', [])
    if not playlists:
        return "No playlists found"
    lines = []
    for pl in playlists:
        own = '(mine)' if pl.get('creator', {}).get('userId') == uid else '(collected)'
        lines.append("ID:" + str(pl['id']) + " | " + pl['name'] + " | " + str(pl.get('trackCount', 0)) + " songs " + own)
    return "\n".join(lines)

def get_owned_playlist(playlist_id):
    """Return a playlist only when it belongs to the logged-in user."""
    try:
        playlist_id = int(playlist_id)
    except (TypeError, ValueError):
        return None, "Failed: playlist_id must be a positive integer."
    if playlist_id <= 0:
        return None, "Failed: playlist_id must be a positive integer."
    uid = get_uid()
    if not uid:
        return None, "Failed to get user ID. Cookie may be expired."
    resp = netease_request('https://music.163.com/api/v6/playlist/detail?id=' + str(playlist_id))
    playlist = resp.get('playlist') or {}
    if not playlist:
        return None, "Failed: playlist not found or not accessible."
    creator_id = playlist.get('creator', {}).get('userId')
    if str(creator_id) != str(uid):
        return None, "Refused: only playlists created by this account can be edited."
    return playlist, None

def update_playlist_info(playlist_id, name=None, description=None, confirm=False):
    """Preview or update the name and description of an owned playlist."""
    if name is None and description is None:
        return "Failed: provide a new name and/or description."
    if name is not None:
        name = str(name).strip()
        if not name:
            return "Failed: playlist name cannot be empty."
        if len(name) > 100:
            return "Failed: playlist name must be at most 100 characters."
    if description is not None:
        description = str(description).strip()
        if len(description) > 1000:
            return "Failed: playlist description must be at most 1000 characters."

    playlist, error = get_owned_playlist(playlist_id)
    if error:
        return error
    new_name = name if name is not None else str(playlist.get('name', '')).strip()
    new_description = (description if description is not None
                       else str(playlist.get('description') or '').strip())
    playlist_id = int(playlist['id'])
    if not confirm:
        return ("Preview only — playlist ID " + str(playlist_id) + " will become: "
                "name='" + new_name + "', description='" + new_description +
                "'. Ask the user to confirm, then call again with confirm=true.")

    csrf = get_csrf()
    if not csrf:
        return "Failed: __csrf is missing from NETEASE_COOKIE."
    url = 'https://music.163.com/api/playlist/update?csrf_token=' + csrf
    data = {
        'id': str(playlist_id),
        'name': new_name,
        'desc': new_description,
        'tags': json.dumps(playlist.get('tags') or [], ensure_ascii=False),
    }
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        description_status = "cleared" if description == "" else "updated"
        return ("Updated playlist ID " + str(playlist_id) + ": name='" +
                new_name + "', description " + description_status + ".")
    return "Failed: " + resp.get('message', resp.get('error', 'unknown'))

def get_playlist_songs(playlist_id):
    url = 'https://music.163.com/api/v6/playlist/detail?id=' + str(playlist_id)
    resp = netease_request(url)
    playlist = resp.get('playlist', {})
    tracks = playlist.get('tracks', [])
    if not tracks:
        track_ids = playlist.get('trackIds', [])
        if track_ids:
            ids = [t['id'] for t in track_ids[:50]]
            detail = netease_request('https://music.163.com/api/song/detail?ids=' + json.dumps(ids))
            tracks = detail.get('songs', [])
    if not tracks:
        return "Playlist " + str(playlist_id) + " is empty"
    lines = ["Playlist: " + playlist.get('name', '') + " (" + str(len(tracks)) + " songs)"]
    for i, t in enumerate(tracks[:50], 1):
        artist = ', '.join([a.get('name', '') for a in t.get('ar', t.get('artists', []))])
        lines.append(str(i) + ". " + t.get('name', '') + " - " + artist + " (ID:" + str(t.get('id', '')) + ")")
    return "\n".join(lines)

def get_play_history(limit=30, all_time=False):
    uid = get_uid()
    if not uid:
        return "Failed to get user ID."
    record_type = '0' if all_time else '1'
    url = 'https://music.163.com/api/v1/play/record?uid=' + str(uid) + '&type=' + record_type + '&limit=' + str(limit)
    resp = netease_request(url)
    records = resp.get('weekData') or resp.get('allData') or []
    if not records:
        return "No play history found"
    lines = ["Recent play history:"]
    for i, r in enumerate(records[:limit], 1):
        song = r.get('song', {})
        name = song.get('name', '')
        artist = ', '.join([a.get('name', '') for a in song.get('ar', song.get('artists', []))])
        pc = r.get('playCount', r.get('score', ''))
        lines.append(str(i) + ". " + name + " - " + artist + " (plays:" + str(pc) + ", ID:" + str(song.get('id', '')) + ")")
    return "\n".join(lines)

def like_song(song_id, like=True):
    csrf = get_csrf()
    action = 'true' if like else 'false'
    url = 'https://music.163.com/api/radio/like?alg=itembased&trackId=' + str(song_id) + '&like=' + action + '&time=25&csrf_token=' + csrf
    resp = netease_request(url)
    if resp.get('code') == 200:
        return "Liked song " + str(song_id) if like else "Unliked song " + str(song_id)
    return "Failed: " + resp.get('message', resp.get('error', 'unknown'))

def daily_recommend():
    csrf = get_csrf()
    url = 'https://music.163.com/api/v3/discovery/recommend/songs?csrf_token=' + csrf
    resp = netease_request(url, data='{}')
    songs = resp.get('data', {}).get('dailySongs', [])
    if not songs:
        return "Could not fetch daily recommendations."
    lines = ["Today's recommendations:"]
    for i, s in enumerate(songs[:30], 1):
        name = s.get('name', '')
        artist = ', '.join([a.get('name', '') for a in s.get('ar', s.get('artists', []))])
        reason = s.get('reason', '')
        line = str(i) + ". " + name + " - " + artist + " (ID:" + str(s.get('id', '')) + ")"
        if reason:
            line += " [" + reason + "]"
        lines.append(line)
    return "\n".join(lines)

TOOLS = [
    {"name": "play_music", "description": "Search and play a song from NetEase Cloud Music.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}, "note": {"type": "string", "description": "Optional note"}}, "required": ["query"]}},
    {"name": "create_playlist", "description": "Create a new playlist in NetEase account.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Playlist name"}, "description": {"type": "string", "description": "Description"}, "privacy": {"type": "integer", "description": "0=public, 10=private"}}, "required": ["name"]}},
    {"name": "add_to_playlist", "description": "Add song(s) to a playlist.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "Song ID(s), comma-separated"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "remove_from_playlist", "description": "Remove song(s) from a playlist.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "Song ID(s) to remove"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "list_my_playlists", "description": "List all playlists of the logged-in user.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_playlist_songs", "description": "Get all songs in a playlist.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}}, "required": ["playlist_id"]}},
    {"name": "update_playlist_info", "description": "Preview or update the name and description of a playlist created by the logged-in user. Use confirm=true only after the user confirms the exact preview.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "ID of a playlist created by the logged-in user"}, "name": {"type": "string", "description": "Optional replacement playlist name"}, "description": {"type": "string", "description": "Optional replacement description; an empty string clears it"}, "confirm": {"type": "boolean", "description": "Set true only after the user confirms the preview"}}, "required": ["playlist_id"]}},
    {"name": "get_play_history", "description": "Get recent play history.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Number of records, default 30"}, "all_time": {"type": "boolean", "description": "true=all time, false=this week (default)"}}}},
    {"name": "get_current_track", "description": "Get the current NetEase Cloud Music track reported by the user's Android phone.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "like_song", "description": "Like or unlike a song.", "inputSchema": {"type": "object", "properties": {"song_id": {"type": "integer", "description": "Song ID"}, "like": {"type": "boolean", "description": "true=like, false=unlike"}}, "required": ["song_id"]}},
    {"name": "daily_recommend", "description": "Get today's personalized recommendations.", "inputSchema": {"type": "object", "properties": {}}}
]

def handle_jsonrpc(body):
    method = body.get('method', '')
    req_id = body.get('id')
    if method == 'initialize':
        return {"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "netease-music-mcp", "version": "2.2.0"}}}
    elif method == 'tools/list':
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == 'tools/call':
        name = body.get('params', {}).get('name', '')
        args = body.get('params', {}).get('arguments', {})
        if name == 'play_music':
            text = play_music(args.get('query', ''), args.get('note'))
        elif name == 'create_playlist':
            text = create_playlist(args.get('name', ''), args.get('description', ''), args.get('privacy', 0))
        elif name == 'add_to_playlist':
            text = add_to_playlist(args.get('playlist_id'), args.get('song_ids', ''))
        elif name == 'remove_from_playlist':
            text = remove_from_playlist(args.get('playlist_id'), args.get('song_ids', ''))
        elif name == 'list_my_playlists':
            text = list_my_playlists()
        elif name == 'get_playlist_songs':
            text = get_playlist_songs(args.get('playlist_id'))
        elif name == 'update_playlist_info':
            text = update_playlist_info(args.get('playlist_id'), args.get('name'),
                                        args.get('description'), args.get('confirm', False))
        elif name == 'get_play_history':
            text = get_play_history(args.get('limit', 30), args.get('all_time', False))
        elif name == 'get_current_track':
            text = get_current_track()
        elif name == 'like_song':
            text = like_song(args.get('song_id'), args.get('like', True))
        elif name == 'daily_recommend':
            text = daily_recommend()
        else:
            text = "Unknown tool: " + name
        return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}
    elif method.startswith('notifications/'):
        return None
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Unknown method: " + method}}

class MCPHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()
    def do_GET(self):
        if self.path == '/health':
            self._json_response({"status": "ok", "tools": len(TOOLS)})
        else:
            self.send_error(404)
    def do_POST(self):
        if is_authorized_mcp_path(self.path):
            self._handle_mcp()
        elif is_authorized_reporter_path(self.path):
            self._handle_now_playing_report()
        else:
            self.send_error(404)
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', '*')
    def _json_response(self, data, status=200):
        self.send_response(status)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Mcp-Session-Id', SESSION_ID)
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
    def _handle_now_playing_report(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            self._json_response({"ok": False, "error": "Invalid Content-Length"}, 400)
            return
        if length <= 0 or length > MAX_REPORT_BYTES:
            self._json_response({"ok": False, "error": "Invalid report size"}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            if payload.get("test_only") is not True:
                update_now_playing_state(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._json_response({"ok": False, "error": str(exc)}, 400)
            return
        self._json_response({"ok": True})
    def _handle_mcp(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        method = body.get('method', '')
        if method.startswith('notifications/') or body.get('id') is None:
            self.send_response(204)
            self._cors()
            self.send_header('Mcp-Session-Id', SESSION_ID)
            self.end_headers()
            return
        result = handle_jsonrpc(body)
        if result is None:
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        self._json_response(result)
    def _handle_sse(self):
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(b"event: endpoint\ndata: /message\n\n")
        self.wfile.flush()
        try:
            while True:
                time.sleep(30)
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except:
            pass
    def log_message(self, format, *args):
        pass

class ThreadedHTTPServer(HTTPServer):
    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()
    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except:
            pass
        finally:
            self.shutdown_request(request)

if __name__ == '__main__':
    print("NetEase Music MCP v2 on port " + str(PORT))
    print("Tools: " + str(len(TOOLS)))
    server = ThreadedHTTPServer(('0.0.0.0', PORT), MCPHandler)
    server.serve_forever()
