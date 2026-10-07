package software.zeasy.momir.sync

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.net.HttpURLConnection
import java.net.URL

class SyncHttpTest {
    private class Connection(private val status: Int, private val retry: String? = null) :
        HttpURLConnection(URL("https://example.invalid/export")) {
        var disconnected = false
        var streamOpened = false
        override fun connect() = Unit
        override fun disconnect() { disconnected = true }
        override fun usingProxy() = false
        override fun getResponseCode() = status
        override fun getHeaderField(name: String) = if (name == "Retry-After") retry else null
        override fun getInputStream() = ByteArrayInputStream(byteArrayOf(1)).also { streamOpened = true }
    }

    @Test fun successClosesConnectionAndSendsHeaders() {
        val connection = Connection(200)
        SyncHttp { connection }.open("https://example.invalid/export", "application/json").use {
            assertEquals(1, it.read())
            assertFalse(connection.disconnected)
        }
        assertTrue(connection.disconnected)
        assertEquals("application/json", connection.getRequestProperty("Accept"))
        assertTrue(connection.getRequestProperty("User-Agent").contains("MooBaloo/MomirSenraise"))
        assertEquals(60_000, connection.readTimeout)
    }

    @Test fun failuresRetainStatusAndDisconnectWithoutReadingBodyOrRetrying() {
        for (status in listOf(429, 503, 404)) {
            val connection = Connection(status, "120")
            var calls = 0
            try {
                SyncHttp { calls++; connection }.open("https://example.invalid/secret", "*/*")
                fail("Expected HTTP failure")
            } catch (e: SyncHttp.HttpFailure) {
                assertEquals(status, e.status)
                assertTrue(e.message!!.contains("120 seconds"))
                assertFalse(e.message!!.contains("secret"))
            }
            assertEquals(1, calls)
            assertTrue(connection.disconnected)
            assertFalse(connection.streamOpened)
        }
    }

    @Test fun untrustedRetryHeaderIsNotDisplayed() {
        try {
            SyncHttp { Connection(429, "<private response>") }.open("https://example.invalid", "*/*")
            fail("Expected HTTP failure")
        } catch (e: SyncHttp.HttpFailure) {
            assertFalse(e.message!!.contains("private"))
            assertTrue(e.message!!.contains("Try syncing again later"))
        }
    }
}
