package io.github.imattau.imanganation;

import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;

import androidx.activity.result.ActivityResult;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.ActivityCallback;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.concurrent.ConcurrentHashMap;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

@CapacitorPlugin(name = "ModelStore")
public class ModelStorePlugin extends Plugin {
    private final ExecutorService io = Executors.newCachedThreadPool();
    private volatile boolean importing = false;
    private final ConcurrentHashMap<String, AtomicBoolean> downloads = new ConcurrentHashMap<>();

    @PluginMethod
    public void importModel(PluginCall call) {
        String expectedSha256 = call.getString("expectedSha256");
        if (expectedSha256 != null && !expectedSha256.matches("(?i)[0-9a-f]{64}")) {
            call.reject("Expected SHA-256 must contain exactly 64 hexadecimal characters.");
            return;
        }
        if (importing) {
            call.reject("A model import is already running.");
            return;
        }
        importing = true;
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        try {
            startActivityForResult(call, intent, "onModelSelected");
        } catch (Exception error) {
            importing = false;
            call.reject("Could not open the Android file picker.", error);
        }
    }

    @ActivityCallback
    private void onModelSelected(PluginCall call, ActivityResult result) {
        if (call == null) return;
        if (result.getResultCode() != getActivity().RESULT_OK || result.getData() == null || result.getData().getData() == null) {
            importing = false;
            call.reject("Model selection was cancelled.", "CANCELLED");
            return;
        }
        Uri source = result.getData().getData();
        String expectedSha256 = call.getString("expectedSha256");
        io.execute(() -> copyModel(call, source, expectedSha256 == null ? null : expectedSha256.toLowerCase(java.util.Locale.ROOT)));
    }

    private void copyModel(PluginCall call, Uri source, String expectedSha256) {
        String id = UUID.randomUUID().toString();
        File modelDir = new File(getContext().getFilesDir(), "models/" + id);
        File partial = null;
        try {
            String displayName = queryName(source);
            String safeName = new File(displayName).getName().replaceAll("[^A-Za-z0-9._-]", "_");
            if (safeName.isEmpty() || safeName.equals(".") || safeName.equals("..")) safeName = "imported-model.bin";
            long total = querySize(source);
            long usable = getContext().getFilesDir().getUsableSpace();
            long required = total > 0 ? total : 0;
            if (required > usable - 64L * 1024L * 1024L) throw new Exception("Not enough free device storage for this model file.");
            if (!modelDir.mkdirs() && !modelDir.isDirectory()) throw new Exception("Could not create the model directory.");
            partial = new File(modelDir, safeName + ".partial");
            File target = new File(modelDir, safeName);
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            long copied = 0;
            byte[] buffer = new byte[1024 * 1024];
            try (InputStream input = getContext().getContentResolver().openInputStream(source);
                 FileOutputStream output = new FileOutputStream(partial)) {
                if (input == null) throw new Exception("Could not open the selected model file.");
                int count;
                long reportedAt = 0;
                while ((count = input.read(buffer)) >= 0) {
                    if (count == 0) continue;
                    output.write(buffer, 0, count);
                    digest.update(buffer, 0, count);
                    copied += count;
                    if (copied - reportedAt >= 4L * 1024L * 1024L || (total > 0 && copied == total)) {
                        reportedAt = copied;
                        JSObject progress = new JSObject();
                        progress.put("id", id);
                        progress.put("name", displayName);
                        progress.put("bytes", copied);
                        progress.put("totalBytes", total);
                        notifyListeners("importProgress", progress);
                    }
                }
                output.getFD().sync();
            }
            if (!partial.renameTo(target)) throw new Exception("Could not finalize the imported model file.");
            String sha256 = hex(digest.digest());
            if (expectedSha256 != null && !expectedSha256.equals(sha256)) throw new Exception("SHA-256 mismatch. Expected " + expectedSha256 + " but computed " + sha256 + ".");
            JSONObject metadata = new JSONObject();
            metadata.put("id", id);
            metadata.put("name", displayName);
            metadata.put("fileName", safeName);
            metadata.put("path", target.getAbsolutePath());
            metadata.put("sizeBytes", copied);
            metadata.put("sha256", sha256);
            java.text.SimpleDateFormat dateFormat = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", java.util.Locale.US);
            dateFormat.setTimeZone(java.util.TimeZone.getTimeZone("UTC"));
            String importedAt = dateFormat.format(new java.util.Date());
            metadata.put("importedAt", importedAt);
            try (FileOutputStream output = new FileOutputStream(new File(modelDir, "metadata.json"))) {
                output.write(metadata.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));
                output.getFD().sync();
            }
            JSObject response = new JSObject();
            response.put("id", id);
            response.put("name", displayName);
            response.put("path", target.getAbsolutePath());
            response.put("sizeBytes", copied);
            response.put("sha256", sha256);
            response.put("importedAt", metadata.getString("importedAt"));
            getActivity().runOnUiThread(() -> call.resolve(response));
        } catch (Exception error) {
            if (partial != null && partial.exists()) partial.delete();
            deleteTree(modelDir);
            getActivity().runOnUiThread(() -> call.reject("Model import failed: " + error.getMessage(), error));
        } finally {
            importing = false;
        }
    }

    @PluginMethod
    public void downloadModel(PluginCall call) {
        String key = call.getString("downloadKey");
        String name = call.getString("name");
        String url = call.getString("url");
        String sha256 = call.getString("sha256");
        String licenseName = call.getString("licenseName");
        String licenseUrl = call.getString("licenseUrl");
        String sourceUrl = call.getString("sourceUrl");
        long size = call.getLong("sizeBytes", -1L);
        if (key == null || !key.matches("[a-z0-9-]{1,64}") || name == null || name.trim().isEmpty()) {
            call.reject("A model download key and name are required.");
            return;
        }
        if (url == null || !isAllowedModelUrl(url) || sha256 == null || !sha256.matches("(?i)[0-9a-f]{64}") || size <= 0) {
            call.reject("Model downloads require an approved HTTPS URL, a file size and a SHA-256 checksum.");
            return;
        }
        AtomicBoolean cancel = new AtomicBoolean(false);
        if (downloads.putIfAbsent(key, cancel) != null) {
            call.reject("This model is already downloading.");
            return;
        }
        JSObject response = new JSObject();
        response.put("downloadKey", key);
        response.put("state", "downloading");
        call.resolve(response);
        io.execute(() -> downloadModelFile(key, name, url, sha256.toLowerCase(java.util.Locale.ROOT), size, licenseName, licenseUrl, sourceUrl, cancel));
    }

    @PluginMethod
    public void cancelDownload(PluginCall call) {
        String key = call.getString("downloadKey");
        AtomicBoolean cancel = key == null ? null : downloads.get(key);
        if (cancel != null) cancel.set(true);
        call.resolve();
    }

    private void downloadModelFile(String key, String name, String downloadUrl, String expectedSha256, long expectedSize, String licenseName, String licenseUrl, String sourceUrl, AtomicBoolean cancel) {
        File downloadsDir = new File(getContext().getFilesDir(), "models/.downloads");
        File partial = new File(downloadsDir, key + ".partial");
        HttpURLConnection connection = null;
        try {
            long usable = getContext().getFilesDir().getUsableSpace();
            if (expectedSize > usable - 64L * 1024L * 1024L) throw new Exception("Not enough free device storage for this model file.");
            if (!downloadsDir.mkdirs() && !downloadsDir.isDirectory()) throw new Exception("Could not create the model download directory.");
            long offset = partial.isFile() ? partial.length() : 0;
            if (offset >= expectedSize) { partial.delete(); offset = 0; }
            URL url = new URL(downloadUrl);
            connection = (HttpURLConnection) url.openConnection();
            connection.setInstanceFollowRedirects(true);
            connection.setConnectTimeout(30000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("User-Agent", "Imanganation-Android");
            if (offset > 0) connection.setRequestProperty("Range", "bytes=" + offset + "-");
            int status = connection.getResponseCode();
            if (!isAllowedModelUrl(connection.getURL().toString())) throw new Exception("The model server redirected to an unapproved host.");
            boolean append = offset > 0 && status == HttpURLConnection.HTTP_PARTIAL;
            if (status == HttpURLConnection.HTTP_OK) { offset = 0; append = false; }
            else if (!append) throw new Exception("Model server returned HTTP " + status + ".");
            if (append) {
                String contentRange = connection.getHeaderField("Content-Range");
                if (contentRange == null || !contentRange.matches("bytes " + offset + "-\\d+/" + expectedSize)) throw new Exception("Model server did not honor the resume range.");
            }
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            if (append) updateDigestFromFile(digest, partial);
            long copied = offset;
            byte[] buffer = new byte[1024 * 1024];
            try (InputStream input = connection.getInputStream(); FileOutputStream output = new FileOutputStream(partial, append)) {
                int count;
                long reportedAt = offset;
                while ((count = input.read(buffer)) >= 0) {
                    if (cancel.get()) {
                        JSObject event = downloadEvent(key, name, copied, expectedSize, "cancelled");
                        notifyListeners("downloadProgress", event);
                        return;
                    }
                    if (count == 0) continue;
                    if (copied + count > expectedSize || copied + count > usable - 64L * 1024L * 1024L) throw new Exception("Downloaded data exceeds its declared size or available storage.");
                    output.write(buffer, 0, count);
                    digest.update(buffer, 0, count);
                    copied += count;
                    if (copied - reportedAt >= 4L * 1024L * 1024L || copied == expectedSize) {
                        reportedAt = copied;
                        notifyListeners("downloadProgress", downloadEvent(key, name, copied, expectedSize, "downloading"));
                    }
                }
                output.getFD().sync();
            }
            if (copied != expectedSize) throw new Exception("Downloaded size does not match the pinned model size.");
            notifyListeners("downloadProgress", downloadEvent(key, name, copied, expectedSize, "verifying"));
            String actualSha256 = hex(digest.digest());
            if (!expectedSha256.equals(actualSha256)) {
                partial.delete();
                throw new Exception("Downloaded model checksum does not match the pinned SHA-256.");
            }
            String id = UUID.randomUUID().toString();
            File modelDir = new File(getContext().getFilesDir(), "models/" + id);
            if (!modelDir.mkdirs() && !modelDir.isDirectory()) throw new Exception("Could not create the verified model directory.");
            String safeName = new File(name).getName().replaceAll("[^A-Za-z0-9._-]", "_");
            if (safeName.isEmpty()) safeName = "model.bin";
            File target = new File(modelDir, safeName);
            if (!partial.renameTo(target)) throw new Exception("Could not move the verified model into app storage.");
            JSONObject metadata = new JSONObject();
            metadata.put("id", id);
            metadata.put("name", name);
            metadata.put("fileName", safeName);
            metadata.put("path", target.getAbsolutePath());
            metadata.put("sizeBytes", copied);
            metadata.put("sha256", actualSha256);
            metadata.put("downloadKey", key);
            metadata.put("origin", "catalog-download");
            metadata.put("sourceUrl", sourceUrl == null ? downloadUrl : sourceUrl);
            if (licenseName != null) metadata.put("licenseName", licenseName);
            if (licenseUrl != null) metadata.put("licenseUrl", licenseUrl);
            String importedAt = utcNow();
            metadata.put("importedAt", importedAt);
            try (FileOutputStream output = new FileOutputStream(new File(modelDir, "metadata.json"))) {
                output.write(metadata.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));
                output.getFD().sync();
            }
            JSObject completed = new JSObject(metadata.toString());
            notifyListeners("downloadComplete", completed);
            notifyListeners("downloadProgress", downloadEvent(key, name, copied, expectedSize, "complete"));
        } catch (Exception error) {
            JSObject event = downloadEvent(key, name, partial.isFile() ? partial.length() : 0, expectedSize, "failed");
            event.put("error", error.getMessage());
            notifyListeners("downloadProgress", event);
        } finally {
            if (connection != null) connection.disconnect();
            downloads.remove(key);
        }
    }

    private static void updateDigestFromFile(MessageDigest digest, File file) throws Exception {
        byte[] buffer = new byte[1024 * 1024];
        try (InputStream input = new FileInputStream(file)) {
            int count;
            while ((count = input.read(buffer)) >= 0) if (count > 0) digest.update(buffer, 0, count);
        }
    }

    private static JSObject downloadEvent(String key, String name, long bytes, long total, String state) {
        JSObject event = new JSObject();
        event.put("downloadKey", key);
        event.put("name", name);
        event.put("bytes", bytes);
        event.put("totalBytes", total);
        event.put("state", state);
        return event;
    }

    private static boolean isAllowedModelUrl(String value) {
        try {
            URL url = new URL(value);
            String host = url.getHost().toLowerCase(java.util.Locale.ROOT);
            return "https".equals(url.getProtocol()) && (host.equals("huggingface.co") || host.endsWith(".huggingface.co") || host.equals("hf.co") || host.endsWith(".xethub.hf.co"));
        } catch (Exception ignored) { return false; }
    }

    private static String utcNow() {
        java.text.SimpleDateFormat dateFormat = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", java.util.Locale.US);
        dateFormat.setTimeZone(java.util.TimeZone.getTimeZone("UTC"));
        return dateFormat.format(new java.util.Date());
    }

    @PluginMethod
    public void listModels(PluginCall call) {
        File models = new File(getContext().getFilesDir(), "models");
        JSArray result = new JSArray();
        File[] entries = models.listFiles(File::isDirectory);
        if (entries != null) {
            for (File dir : entries) {
                File metadataFile = new File(dir, "metadata.json");
                try (FileInputStream input = new FileInputStream(metadataFile)) {
                    byte[] data = new byte[(int) Math.min(metadataFile.length(), 64 * 1024)];
                    int size = input.read(data);
                    if (size > 0) result.put(new JSObject(new String(data, 0, size, java.nio.charset.StandardCharsets.UTF_8)));
                } catch (Exception ignored) { }
            }
        }
        JSObject response = new JSObject();
        response.put("models", result);
        long usable = getContext().getFilesDir().getUsableSpace();
        response.put("availableBytes", usable);
        call.resolve(response);
    }

    @PluginMethod
    public void deleteModel(PluginCall call) {
        String id = call.getString("id");
        if (id == null || !id.matches("[A-Fa-f0-9-]{36}")) {
            call.reject("A valid model id is required.");
            return;
        }
        File root = new File(getContext().getFilesDir(), "models").getAbsoluteFile();
        File model = new File(root, id).getAbsoluteFile();
        if (!model.getParentFile().equals(root) || !model.exists()) {
            call.reject("The model was not found.");
            return;
        }
        deleteTree(model);
        call.resolve();
    }

    private String queryName(Uri uri) throws Exception {
        try (Cursor cursor = getContext().getContentResolver().query(uri, null, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int column = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (column >= 0) return cursor.getString(column);
            }
        }
        String segment = uri.getLastPathSegment();
        return segment == null ? "imported-model.bin" : segment;
    }

    private long querySize(Uri uri) {
        try (Cursor cursor = getContext().getContentResolver().query(uri, null, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int column = cursor.getColumnIndex(OpenableColumns.SIZE);
                if (column >= 0 && !cursor.isNull(column)) return cursor.getLong(column);
            }
        } catch (Exception ignored) { }
        return -1;
    }

    private static String hex(byte[] bytes) {
        StringBuilder value = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) value.append(String.format("%02x", b));
        return value.toString();
    }

    private static void deleteTree(File file) {
        File[] children = file.listFiles();
        if (children != null) for (File child : children) deleteTree(child);
        file.delete();
    }
}
