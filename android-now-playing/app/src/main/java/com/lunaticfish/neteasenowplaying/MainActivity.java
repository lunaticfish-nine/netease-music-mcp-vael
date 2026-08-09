package com.lunaticfish.neteasenowplaying;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

public class MainActivity extends Activity {
    private EditText baseUrlInput;
    private EditText reporterSecretInput;
    private TextView accessStatus;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildContent());
        loadSettings();
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateAccessStatus();
        ComponentName component = new ComponentName(this, NowPlayingListenerService.class);
        android.service.notification.NotificationListenerService.requestRebind(component);
    }

    private ScrollView buildContent() {
        int padding = dp(20);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(padding, padding, padding, padding);

        TextView title = new TextView(this);
        title.setText("网易云正在播放上报器");
        title.setTextSize(24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        layout.addView(title, matchWrap());

        TextView privacy = new TextView(this);
        privacy.setText("仅读取网易云音乐的媒体会话或播放通知，不上传其他通知内容。\n先在 Zeabur 配置独立的 NOW_PLAYING_REPORTER_SECRET。");
        privacy.setTextSize(15);
        privacy.setPadding(0, dp(12), 0, dp(16));
        layout.addView(privacy, matchWrap());

        baseUrlInput = new EditText(this);
        baseUrlInput.setHint("服务器地址，例如 https://example.zeabur.app");
        baseUrlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        layout.addView(baseUrlInput, matchWrap());

        reporterSecretInput = new EditText(this);
        reporterSecretInput.setHint("NOW_PLAYING_REPORTER_SECRET");
        reporterSecretInput.setInputType(
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        layout.addView(reporterSecretInput, matchWrap());

        Button save = new Button(this);
        save.setText("保存设置");
        save.setOnClickListener(view -> saveSettings());
        layout.addView(save, matchWrap());

        accessStatus = new TextView(this);
        accessStatus.setTextSize(16);
        accessStatus.setPadding(0, dp(16), 0, dp(8));
        layout.addView(accessStatus, matchWrap());

        Button openAccess = new Button(this);
        openAccess.setText("打开通知使用权设置");
        openAccess.setOnClickListener(view ->
                startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));
        layout.addView(openAccess, matchWrap());

        Button test = new Button(this);
        test.setText("测试上报连接");
        test.setOnClickListener(view -> sendTestReport());
        layout.addView(test, matchWrap());

        TextView help = new TextView(this);
        help.setText("使用方法：\n1. 保存地址和上报密钥。\n2. 授予本 App 通知使用权。\n3. 打开网易云并播放歌曲。\n4. 在 ChatGPT 中调用 get_current_track。");
        help.setTextSize(15);
        help.setPadding(0, dp(16), 0, 0);
        layout.addView(help, matchWrap());

        ScrollView scrollView = new ScrollView(this);
        scrollView.addView(layout);
        return scrollView;
    }

    private void loadSettings() {
        SharedPreferences preferences = getSharedPreferences(
                NowPlayingUploader.PREFS, MODE_PRIVATE);
        baseUrlInput.setText(preferences.getString(NowPlayingUploader.KEY_BASE_URL, ""));
        reporterSecretInput.setText(
                preferences.getString(NowPlayingUploader.KEY_REPORTER_SECRET, ""));
    }

    private void saveSettings() {
        String baseUrl = baseUrlInput.getText().toString().trim();
        String secret = reporterSecretInput.getText().toString().trim();
        if (!baseUrl.startsWith("https://")) {
            toast("服务器地址必须以 https:// 开头");
            return;
        }
        if (secret.length() < 32) {
            toast("上报密钥至少需要 32 个字符");
            return;
        }
        getSharedPreferences(NowPlayingUploader.PREFS, MODE_PRIVATE)
                .edit()
                .putString(NowPlayingUploader.KEY_BASE_URL, baseUrl)
                .putString(NowPlayingUploader.KEY_REPORTER_SECRET, secret)
                .apply();
        toast("设置已保存");
        ComponentName component = new ComponentName(this, NowPlayingListenerService.class);
        android.service.notification.NotificationListenerService.requestRebind(component);
    }

    private void sendTestReport() {
        try {
            JSONObject payload = new JSONObject();
            payload.put("source_package", "com.netease.cloudmusic");
            payload.put("title", "");
            payload.put("artist", "");
            payload.put("album", "");
            payload.put("status", "idle");
            payload.put("captured_at", System.currentTimeMillis() / 1000.0);
            NowPlayingUploader.upload(this, payload,
                    (success, message) -> toast(message));
        } catch (Exception exception) {
            toast("无法创建测试数据");
        }
    }

    private void updateAccessStatus() {
        ComponentName component = new ComponentName(this, NowPlayingListenerService.class);
        String enabled = Settings.Secure.getString(
                getContentResolver(), "enabled_notification_listeners");
        boolean granted = enabled != null &&
                (enabled.contains(component.flattenToString()) ||
                        enabled.contains(component.flattenToShortString()));
        accessStatus.setText(granted
                ? "通知使用权：已授权"
                : "通知使用权：未授权");
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
