package ru.veilway.android

import android.graphics.Bitmap
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.graphics.drawable.toBitmap

data class InstalledApp(val id: String, val name: String, val icon: Bitmap)

class InstalledApps(private val context: Context) {
    fun load(): List<InstalledApp> {
        val pm = context.packageManager
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return pm.queryIntentActivities(intent, 0).mapNotNull { info ->
            val id = info.activityInfo.packageName
            if (id == context.packageName) return@mapNotNull null
            runCatching {
                val permissions = pm.getPackageInfo(id, PackageManager.GET_PERMISSIONS).requestedPermissions.orEmpty()
                if ("android.permission.INTERNET" !in permissions) null
                else InstalledApp(id, info.loadLabel(pm).toString(), info.loadIcon(pm).toBitmap(64, 64))
            }.getOrNull()
        }.distinctBy { it.id }.sortedBy { it.name.lowercase() }
    }
}
