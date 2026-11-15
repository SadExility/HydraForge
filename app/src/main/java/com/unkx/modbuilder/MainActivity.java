package com.unkx.modbuilder;

import android.app.Activity;
import android.content.ContentValues;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** Starts the Python build server and displays HydraForge in a WebView. */
public class MainActivity extends Activity {
    private static final int REQ_FILE = 1;

    private WebView web;
    private ValueCallback<Uri[]> chooser;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        web = new WebView(this);
        setContentView(web);
        web.setKeepScreenOn(true);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true); // page uses localStorage

        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient() {
            // Sem isto o <input type=file> do WebView simplesmente nao faz nada.
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (chooser != null) chooser.onReceiveValue(null);
                chooser = cb;
                try {
                    Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                    i.addCategory(Intent.CATEGORY_OPENABLE);
                    i.setType("*/*");
                    startActivityForResult(i, REQ_FILE);
                } catch (Exception e) {
                    chooser = null;
                    cb.onReceiveValue(null);
                }
                return true;
            }
        });
        // Sem isto o botao "Baixar .jar" nao faz nada no WebView.
        web.setDownloadListener((url, ua, cd, mime, len) -> saveDownload(url, cd, mime));

        startServer();
    }

    private void startServer() {
        new Thread(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                File dest = getExternalFilesDir(null);
                if (dest == null) dest = getFilesDir();
                final int port = Python.getInstance()
                    .getModule("build_on_github_web")
                    .callAttr("start_background", dest.getAbsolutePath())
                    .toInt();
                runOnUiThread(() -> web.loadUrl("http://127.0.0.1:" + port + "/"));
            } catch (Throwable t) {
                final String msg = String.valueOf(t).replace("<", "&lt;");
                runOnUiThread(() -> web.loadDataWithBaseURL(null,
                    "<body style='font:16px sans-serif;padding:20px'><h3>Erro ao iniciar o Python</h3><pre>"
                        + msg + "</pre>", "text/html", "utf-8", null));
            }
        }).start();
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req != REQ_FILE || chooser == null) return;
        Uri[] r = (res == RESULT_OK && data != null)
            ? WebChromeClient.FileChooserParams.parseResult(res, data) : null;
        chooser.onReceiveValue(r); // sempre responder, mesmo com null (cancelou)
        chooser = null;
    }

    private void saveDownload(final String url, final String cd, final String mime) {
        new Thread(() -> {
            try {
                String name = URLUtil.guessFileName(url, cd, mime);
                HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                ByteArrayOutputStream bo = new ByteArrayOutputStream();
                try (InputStream in = c.getInputStream()) {
                    byte[] buf = new byte[16384];
                    int n;
                    while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
                }
                ContentValues v = new ContentValues();
                v.put(MediaStore.Downloads.DISPLAY_NAME, name);
                v.put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream");
                v.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
                Uri u = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                if (u == null) throw new Exception("Nao consegui criar o arquivo");
                try (OutputStream o = getContentResolver().openOutputStream(u)) { o.write(bo.toByteArray()); }
                toast("Salvo em Downloads: " + name);
            } catch (Exception e) {
                toast("Erro ao salvar: " + e.getMessage());
            }
        }).start();
    }

    private void toast(final String m) {
        runOnUiThread(() -> Toast.makeText(this, m, Toast.LENGTH_LONG).show());
    }

    @Override
    public void onBackPressed() {
        moveTaskToBack(true); // nao fecha o app (e o build) sem querer
    }
}
