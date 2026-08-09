package com.lunaticfish.neteasenowplaying;

import android.app.Notification;
import android.content.ComponentName;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import org.json.JSONObject;

import java.util.List;

public class NowPlayingListenerService extends NotificationListenerService {
    private static final String NETEASE_PACKAGE = "com.netease.cloudmusic";
    private static final long HEARTBEAT_MS = 5000;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private MediaSessionManager sessionManager;
    private MediaSessionManager.OnActiveSessionsChangedListener sessionsChangedListener;
    private MediaController currentController;
    private MediaController.Callback currentCallback;

    private final Runnable heartbeat = new Runnable() {
        @Override
        public void run() {
            if (!refreshController()) {
                publishStopped();
            }
            handler.postDelayed(this, HEARTBEAT_MS);
        }
    };

    @Override
    public void onListenerConnected() {
        super.onListenerConnected();
        handler.removeCallbacks(heartbeat);
        registerSessionsListener();
        refreshController();
        handler.postDelayed(heartbeat, HEARTBEAT_MS);
    }

    @Override
    public void onListenerDisconnected() {
        unregisterController();
        unregisterSessionsListener();
        handler.removeCallbacks(heartbeat);
        super.onListenerDisconnected();
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        if (sbn == null || !NETEASE_PACKAGE.equals(sbn.getPackageName())) {
            return;
        }
        if (!refreshController()) {
            publishFromNotification(sbn.getNotification());
        }
    }

    @Override
    public void onNotificationRemoved(StatusBarNotification sbn) {
        if (sbn == null || !NETEASE_PACKAGE.equals(sbn.getPackageName())) {
            return;
        }
        handler.postDelayed(() -> {
            if (!refreshController()) {
                publishStopped();
            }
        }, 1000);
    }

    @Override
    public void onDestroy() {
        unregisterController();
        unregisterSessionsListener();
        handler.removeCallbacks(heartbeat);
        super.onDestroy();
    }

    private void registerSessionsListener() {
        unregisterSessionsListener();
        try {
            sessionManager = getSystemService(MediaSessionManager.class);
            if (sessionManager == null) {
                return;
            }
            ComponentName listener = new ComponentName(this, getClass());
            sessionsChangedListener = controllers -> {
                if (!publishFromControllers(controllers)) {
                    publishStopped();
                }
            };
            sessionManager.addOnActiveSessionsChangedListener(
                    sessionsChangedListener, listener, handler);
        } catch (SecurityException ignored) {
            sessionManager = null;
            sessionsChangedListener = null;
        }
    }

    private void unregisterSessionsListener() {
        if (sessionManager != null && sessionsChangedListener != null) {
            try {
                sessionManager.removeOnActiveSessionsChangedListener(
                        sessionsChangedListener);
            } catch (Exception ignored) {
                // The listener may already have been removed by Android.
            }
        }
        sessionsChangedListener = null;
        sessionManager = null;
    }

    private boolean refreshController() {
        try {
            MediaSessionManager manager = sessionManager != null
                    ? sessionManager : getSystemService(MediaSessionManager.class);
            if (manager == null) {
                return false;
            }
            ComponentName listener = new ComponentName(this, getClass());
            List<MediaController> controllers = manager.getActiveSessions(listener);
            return publishFromControllers(controllers);
        } catch (SecurityException ignored) {
            // Notification access has not been granted yet.
        }
        unregisterController();
        return false;
    }

    private boolean publishFromControllers(List<MediaController> controllers) {
        if (controllers != null) {
            for (MediaController controller : controllers) {
                if (NETEASE_PACKAGE.equals(controller.getPackageName())) {
                    attachController(controller);
                    publishFromController(controller);
                    return true;
                }
            }
        }
        unregisterController();
        return false;
    }

    private void attachController(MediaController controller) {
        if (currentController != null &&
                currentController.getSessionToken().equals(controller.getSessionToken())) {
            return;
        }
        unregisterController();
        currentController = controller;
        currentCallback = new MediaController.Callback() {
            @Override
            public void onMetadataChanged(MediaMetadata metadata) {
                publishFromController(currentController);
            }

            @Override
            public void onPlaybackStateChanged(PlaybackState state) {
                publishFromController(currentController);
            }

            @Override
            public void onSessionDestroyed() {
                unregisterController();
                publishStopped();
            }
        };
        currentController.registerCallback(currentCallback, handler);
    }

    private void unregisterController() {
        if (currentController != null && currentCallback != null) {
            try {
                currentController.unregisterCallback(currentCallback);
            } catch (Exception ignored) {
                // The media session may already be gone.
            }
        }
        currentController = null;
        currentCallback = null;
    }

    private void publishFromController(MediaController controller) {
        if (controller == null) {
            return;
        }
        try {
            MediaMetadata metadata = controller.getMetadata();
            PlaybackState playbackState = controller.getPlaybackState();
            String title = metadataText(metadata,
                    MediaMetadata.METADATA_KEY_DISPLAY_TITLE,
                    MediaMetadata.METADATA_KEY_TITLE);
            String artist = metadataText(metadata,
                    MediaMetadata.METADATA_KEY_ARTIST,
                    MediaMetadata.METADATA_KEY_ALBUM_ARTIST,
                    MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE);
            String album = metadataText(metadata, MediaMetadata.METADATA_KEY_ALBUM);
            long duration = metadata == null ? -1
                    : metadata.getLong(MediaMetadata.METADATA_KEY_DURATION);
            long position = playbackState == null ? -1 : playbackState.getPosition();
            float speed = playbackState == null ? 0f : playbackState.getPlaybackSpeed();
            if (playbackState != null &&
                    playbackState.getState() == PlaybackState.STATE_PLAYING &&
                    playbackState.getLastPositionUpdateTime() > 0 && position >= 0) {
                long elapsed = SystemClock.elapsedRealtime()
                        - playbackState.getLastPositionUpdateTime();
                position += Math.max(0, Math.round(elapsed * speed));
            }
            JSONObject payload = basePayload(playbackStatus(playbackState));
            payload.put("title", title);
            payload.put("artist", artist);
            payload.put("album", album);
            if (position >= 0) {
                payload.put("position_ms", position);
            }
            if (duration >= 0) {
                payload.put("duration_ms", duration);
            }
            payload.put("playback_speed", Math.max(0f, speed));
            NowPlayingUploader.upload(this, payload, null);
        } catch (Exception ignored) {
            // A later callback or heartbeat will retry with fresh metadata.
        }
    }

    private void publishFromNotification(Notification notification) {
        if (notification == null || notification.extras == null) {
            return;
        }
        try {
            CharSequence title = notification.extras.getCharSequence(Notification.EXTRA_TITLE);
            CharSequence text = notification.extras.getCharSequence(Notification.EXTRA_TEXT);
            JSONObject payload = basePayload("playing");
            payload.put("title", title == null ? "" : title.toString());
            payload.put("artist", text == null ? "" : text.toString());
            payload.put("album", "");
            NowPlayingUploader.upload(this, payload, null);
        } catch (Exception ignored) {
            // Ignore malformed notification metadata from the source app.
        }
    }

    private void publishStopped() {
        try {
            JSONObject payload = basePayload("stopped");
            payload.put("title", "");
            payload.put("artist", "");
            payload.put("album", "");
            NowPlayingUploader.upload(this, payload, null);
        } catch (Exception ignored) {
            // Settings may not be configured yet.
        }
    }

    private JSONObject basePayload(String status) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("source_package", NETEASE_PACKAGE);
        payload.put("status", status);
        payload.put("captured_at", System.currentTimeMillis() / 1000.0);
        return payload;
    }

    private String playbackStatus(PlaybackState state) {
        if (state == null) {
            return "unknown";
        }
        if (state.getState() == PlaybackState.STATE_PLAYING) {
            return "playing";
        }
        if (state.getState() == PlaybackState.STATE_PAUSED ||
                state.getState() == PlaybackState.STATE_BUFFERING) {
            return "paused";
        }
        if (state.getState() == PlaybackState.STATE_STOPPED ||
                state.getState() == PlaybackState.STATE_NONE) {
            return "stopped";
        }
        return "unknown";
    }

    private String metadataText(MediaMetadata metadata, String... keys) {
        if (metadata == null) {
            return "";
        }
        for (String key : keys) {
            String value = metadata.getString(key);
            if (value != null && !value.trim().isEmpty()) {
                return value.trim();
            }
        }
        return "";
    }
}
