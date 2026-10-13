package ru.veilway.android

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class SecretStore(context: Context) {
    companion object { private val lock = Any() }
    private val directory = File(context.noBackupFilesDir, "vault").apply { mkdirs() }
    private val alias = "veilway-vault-v1"
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    private fun file(name: String): AtomicFile {
        require(name.matches(Regex("[a-z0-9-]{1,80}")))
        return AtomicFile(File(directory, name))
    }
    fun put(name: String, value: String): Unit = synchronized(lock) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key()); cipher.updateAAD(name.toByteArray())
        val plain = value.toByteArray()
        val encrypted = try { cipher.doFinal(plain) } finally { plain.fill(0) }
        val target = file(name)
        val stream = target.startWrite()
        try { stream.write(cipher.iv); stream.write(encrypted); target.finishWrite(stream) }
        catch (error: Exception) { target.failWrite(stream); throw error }
    }
    fun get(name: String): String? = synchronized(lock) {
        val target = file(name)
        if (!target.baseFile.exists()) return@synchronized null
        val bytes = target.readFully()
        require(bytes.size >= 28)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
        cipher.updateAAD(name.toByteArray())
        val plain = cipher.doFinal(bytes.copyOfRange(12, bytes.size))
        try { plain.toString(Charsets.UTF_8) } finally { plain.fill(0) }
    }
    fun clear(): Unit = synchronized(lock) { directory.listFiles()?.forEach { it.delete() }; Unit }
    fun remove(name: String): Unit = synchronized(lock) { file(name).delete() }
    fun putAuthenticatedProfile(name: String, value: String, expectedToken: String): Boolean = synchronized(lock) {
        if (get("token") != expectedToken) false else { put(name, value); true }
    }
}
