package ru.veilway.android

import org.json.JSONObject

/** Preserve the existing encrypted keys and JSON format across application upgrades. */
class StoredLogin(private val store: SecretStore) : LoginStore {
    override fun token(): String? = store.get("token")
    override fun saveToken(token: String) = store.put("token", token)
    override fun pending(): PendingLogin? = store.get("login")?.let {
        val data = JSONObject(it)
        PendingLogin(data.getString("device"), data.getString("code"), data.getString("uri"),
            data.getLong("deadline"), data.getInt("interval"))
    }
    override fun savePending(login: PendingLogin) = store.put("login", JSONObject()
        .put("device", login.device).put("code", login.code).put("uri", login.uri)
        .put("deadline", login.deadline).put("interval", login.interval).toString())
    override fun clearPending() = store.remove("login")
}
