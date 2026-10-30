package com.nuovogoat.doctorcode.data

import android.content.Context
import android.os.Build

/** Non-secret connection preferences plus the Keystore-backed token. */
class SessionStore(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
    private val vault = TokenVault(appContext)

    var baseUrl: String
        get() = prefs.getString(KEY_BASE_URL, "").orEmpty()
        set(value) {
            prefs.edit().putString(KEY_BASE_URL, value.trim()).apply()
        }

    var deviceName: String
        get() = prefs.getString(KEY_DEVICE_NAME, null)
            ?.takeIf { it.isNotBlank() }
            ?: defaultDeviceName()
        set(value) {
            prefs.edit().putString(KEY_DEVICE_NAME, value.trim().take(80)).apply()
        }

    var token: String
        get() = vault.load()
        set(value) = vault.save(value)

    fun clear() {
        prefs.edit().remove(KEY_BASE_URL).apply()
        vault.clear()
    }

    private fun defaultDeviceName(): String {
        val model = Build.MODEL.orEmpty().trim()
        return if (model.isBlank()) "Android" else "Android $model".take(80)
    }

    private companion object {
        const val PREFERENCES = "doctorcode_session"
        const val KEY_BASE_URL = "base_url"
        const val KEY_DEVICE_NAME = "device_name"
    }
}
