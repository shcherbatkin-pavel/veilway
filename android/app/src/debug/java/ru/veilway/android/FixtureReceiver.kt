package ru.veilway.android

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Base64

class FixtureActivity : android.app.Activity()

/** Debug APK only: accepts synthetic fixtures from the local adb test harness. */
class FixtureReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val fixture = intent.getStringExtra("fixture") ?: return
        val profile = Base64.decode(fixture, Base64.NO_WRAP).toString(Charsets.UTF_8)
        require(profile.contains("verify-x509-name synthetic-server name"))
        ProfilePolicy.validate(profile)
        SecretStore(context).put("synthetic-profile", profile)
    }
}
