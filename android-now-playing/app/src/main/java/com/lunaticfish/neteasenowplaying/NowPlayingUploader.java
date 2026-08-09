package com.lunaticfish.neteasenowplaying;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class NowPlayingUploader {
    static final String PREFS = "now_playing_settings";
    static final String KEY_BASE_URL = "base_url";
    static final String KEY_REPORTER_SECRET = "reporter_secret";

    interface Callback {
        void onComplete(boolean success, String message);
    }

    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor();
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private NowPlayingUploader() {}

    static void upload(Context context, JSONObject payload, Callback callback) {
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String baseUrl = preferences.getString(KEY_BASE_URL, "").trim();
        String secret = preferences.getString(KEY_REPORTER_SECRET, "").trim();
        if (!baseUrl.startsWith("https://") || secret.isEmpty()) {
            finish(callback, false, "请先保存 HTTPS 服务器地址和上报密钥");
            return;
        }
        while (baseUrl.endsWith("/")) {
            baseUrl = baseUrl.substring(0, baseUrl.length() - 1);
        }
        final String encodedSecret;
        try {
            encodedSecret = URLEncoder.encode(secret, "UTF-8");
        } catch (Exception exception) {
            finish(callback, false, "无法编码上报密钥");
            return;
        }
        final String endpoint = baseUrl + "/now-playing/" + encodedSecret;
        EXECUTOR.execute(() -> postJson(endpoint, payload, callback));
    }

    private static void postJson(String endpoint, JSONObject payload, Callback callback) {
        HttpURLConnection connection = null;
        try {
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
            connection = (HttpURLConnection) new URL(endpoint).openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(10000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setFixedLengthStreamingMode(body.length);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(body);
            }
            int status = connection.getResponseCode();
            InputStream responseStream = status >= 400
                    ? connection.getErrorStream() : connection.getInputStream();
            String response = readSmallResponse(responseStream);
            if (status >= 200 && status < 300) {
                finish(callback, true, "上报成功");
            } else {
                finish(callback, false, "服务器返回 " + status + ": " + response);
            }
        } catch (Exception exception) {
            finish(callback, false, "上报失败：" + exception.getClass().getSimpleName());
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    private static String readSmallResponse(InputStream input) {
        if (input == null) {
            return "";
        }
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[512];
            int total = 0;
            int read;
            while ((read = stream.read(buffer)) != -1 && total < 2048) {
                int allowed = Math.min(read, 2048 - total);
                output.write(buffer, 0, allowed);
                total += allowed;
            }
            return output.toString(StandardCharsets.UTF_8.name());
        } catch (Exception ignored) {
            return "";
        }
    }

    private static void finish(Callback callback, boolean success, String message) {
        if (callback != null) {
            MAIN.post(() -> callback.onComplete(success, message));
        }
    }
}
