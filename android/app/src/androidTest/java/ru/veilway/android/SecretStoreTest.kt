package ru.veilway.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class SecretStoreTest {
    @Test fun keystoreCiphertextIsAuthenticatedAndNotPlaintext() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = SecretStore(context)
        val synthetic = "synthetic-secret-store-fixture-123456789"
        try {
            store.put("test-secret", synthetic)
            assertEquals(synthetic, store.get("test-secret"))
            val file = File(context.noBackupFilesDir, "vault/test-secret")
            val bytes = file.readBytes()
            assertFalse(bytes.toString(Charsets.ISO_8859_1).contains(synthetic))
            bytes[bytes.lastIndex] = (bytes.last().toInt() xor 1).toByte()
            file.writeBytes(bytes)
            assertThrows(Exception::class.java) { store.get("test-secret") }
            store.put("token", "synthetic-token")
            val otherInstance = SecretStore(context)
            otherInstance.clear()
            assertFalse(store.putAuthenticatedProfile("test-profile", synthetic, "synthetic-token"))
            assertNull(otherInstance.get("test-profile"))
        } finally { store.remove("test-secret") }
    }
}
