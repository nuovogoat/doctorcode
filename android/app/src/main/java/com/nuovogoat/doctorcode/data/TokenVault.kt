package com.nuovogoat.doctorcode.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Encrypts the bearer token with a hardware-backed Android Keystore key.
 *
 * The token is not an API key, but it grants control of the desktop agent, so it
 * gets the same local-at-rest treatment.  Ciphertext and IV live in private
 * SharedPreferences; the key never leaves the Keystore.
 */
class TokenVault(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun save(token: String) {
        if (token.isBlank()) {
            clear()
            return
        }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val payload = cipher.iv + cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        prefs.edit()
            .putString(KEY_TOKEN, Base64.encodeToString(payload, Base64.NO_WRAP))
            .apply()
    }

    fun load(): String {
        val stored = prefs.getString(KEY_TOKEN, null) ?: return ""
        return try {
            val payload = Base64.decode(stored, Base64.NO_WRAP)
            if (payload.size <= IV_BYTES) {
                clear()
                return ""
            }
            val iv = payload.copyOfRange(0, IV_BYTES)
            val ciphertext = payload.copyOfRange(IV_BYTES, payload.size)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(ciphertext), Charsets.UTF_8)
        } catch (_: Exception) {
            clear()
            ""
        }
    }

    fun clear() {
        prefs.edit().remove(KEY_TOKEN).apply()
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        val existing = store.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry
        if (existing != null) return existing.secretKey

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "doctorcode.bearer.v1"
        const val PREFERENCES = "doctorcode_vault"
        const val KEY_TOKEN = "token"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
    }
}
