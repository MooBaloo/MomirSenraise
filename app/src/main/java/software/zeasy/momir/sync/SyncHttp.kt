package software.zeasy.momir.sync

import java.io.FilterInputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL

internal class SyncHttp(
    private val connect: (URL) -> HttpURLConnection = { it.openConnection() as HttpURLConnection },
) {
    class HttpFailure(val status: Int, retryAfterSeconds: Long?) : IOException(
        "HTTP $status" + when (status) {
            206 -> " (partial response rejected). "
            429 -> " (rate limited). "
            503 -> " (service unavailable). "
            else -> ". "
        } + (retryAfterSeconds?.let { "Retry after $it seconds." } ?: "Try syncing again later.")
    )

    fun open(url: String, accept: String): InputStream {
        val connection = connect(URL(url))
        try {
            connection.setRequestProperty("User-Agent", "Momir/1.0 (+https://github.com/MooBaloo/MomirSenraise)")
            connection.setRequestProperty("Accept", accept)
            connection.connectTimeout = 20_000
            connection.readTimeout = 60_000
            connection.instanceFollowRedirects = true
            val status = connection.responseCode
            // These are full-resource GETs, never range or conditional requests.
            // A valid JSONL prefix from a 206 must not advance the bulk checkpoint.
            if (status != HttpURLConnection.HTTP_OK) {
                val retry = connection.getHeaderField("Retry-After")?.toLongOrNull()?.takeIf { it >= 0 }
                throw HttpFailure(status, retry)
            }
            return object : FilterInputStream(connection.inputStream) {
                override fun close() {
                    try { super.close() } finally { connection.disconnect() }
                }
            }
        } catch (e: Exception) {
            connection.disconnect()
            throw e
        }
    }
}
