package ru.veilway.android

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.URL
import java.io.ByteArrayOutputStream
import java.time.Instant
import javax.net.ssl.HttpsURLConnection

class ApiFailure(val status: Int, val code: String = "unavailable") : Exception("API unavailable")
data class Profile(val id: String, val name: String, val mode: String, val status: String, val expires: Instant) {
    val usable: Boolean get() = status == "active" && expires.isAfter(Instant.now())
}
data class DeviceLogin(val deviceCode: String, val userCode: String, val uri: String, val expires: Int, val interval: Int)

class MobileApi : MobileClient {
    private val base = "https://veilway.ru/api/v1/mobile/"
    private suspend fun request(path: String, token: String? = null, payload: JSONObject? = null): String = withContext(Dispatchers.IO) {
        val connection = URL(base + path).openConnection() as HttpsURLConnection
        try {
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 15000; connection.readTimeout = 15000; connection.useCaches = false
            connection.setRequestProperty("Accept", "application/json")
            token?.let { connection.setRequestProperty("Authorization", "Bearer $it") }
            if (payload != null) {
                connection.requestMethod = "POST"; connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(payload.toString().toByteArray()) }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val bytes = stream?.use { input ->
                val output = ByteArrayOutputStream()
                val buffer = ByteArray(8192)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    if (output.size() + count > 1048576) throw ApiFailure(502)
                    output.write(buffer, 0, count)
                }
                output.toByteArray()
            } ?: byteArrayOf()
            val body = bytes.toString(Charsets.UTF_8)
            if (status !in 200..299) {
                val code = runCatching { JSONObject(body).optString("error", "unavailable") }.getOrDefault("unavailable")
                throw ApiFailure(status, code)
            }
            body
        } finally { connection.disconnect() }
    }
    override suspend fun device(): DeviceLogin {
        val data = JSONObject(request("auth/device", payload = JSONObject()))
        val uri = data.getString("verification_uri_complete")
        require(uri.startsWith("https://veilway.ru/?mobile=1#code="))
        return DeviceLogin(data.getString("device_code"), data.getString("user_code"), uri, data.getInt("expires_in"), data.getInt("interval"))
    }
    override suspend fun exchange(code: String): String = JSONObject(request("auth/token", payload = JSONObject().put("device_code", code))).getString("access_token")
    override suspend fun profiles(token: String): List<Profile> {
        val result = mutableListOf<Profile>()
        var offset = 0
        do {
            val data = JSONArray(request("profiles?limit=100&offset=$offset", token))
            for (index in 0 until data.length()) {
                val p = data.getJSONObject(index)
                result += Profile(p.getString("id"), p.getString("device_name"), p.getString("mode"), p.getString("status"), Instant.parse(p.getString("expires_at")))
            }
            offset += data.length()
        } while (data.length() == 100 && offset < 10000)
        return result
    }
    override suspend fun download(token: String, id: String): String {
        require(id.matches(Regex("[a-f0-9-]{36}")))
        return request("profiles/$id/download", token, JSONObject())
    }
    override suspend fun logout(token: String) { request("auth/logout", token, JSONObject()) }
}
