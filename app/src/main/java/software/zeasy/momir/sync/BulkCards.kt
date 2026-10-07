package software.zeasy.momir.sync

import android.util.JsonReader
import android.util.JsonToken
import org.json.JSONArray
import org.json.JSONObject
import java.io.FilterReader
import java.io.IOException
import java.io.Reader

/** Streams either export format, retaining only one card. Invalid input aborts the transaction. */
internal object BulkCards {
    fun read(source: Reader, jsonl: Boolean, consume: (JSONObject) -> Unit) {
        // Android JsonReader treats EOFException as end-of-document in peek().
        // Preserve transport/decompression failures (including a missing gzip trailer).
        var readFailure: IOException? = null
        val checked = object : FilterReader(source) {
            override fun read(buffer: CharArray, offset: Int, length: Int): Int = try {
                super.read(buffer, offset, length)
            } catch (e: IOException) {
                readFailure = e
                throw e
            }
        }
        try {
            if (jsonl) {
                checked.buffered().forEachLine { line ->
                    if (line.isNotBlank()) JsonReader(line.reader()).use { reader ->
                        consume(readCard(reader))
                        requireEnd(reader)
                    }
                }
            } else {
                JsonReader(checked).use { reader ->
                    reader.beginArray()
                    while (reader.hasNext()) consume(readCard(reader))
                    reader.endArray()
                    requireEnd(reader)
                }
            }
        } finally {
            readFailure?.let { throw it }
        }
    }

    private fun requireEnd(reader: JsonReader) {
        if (reader.peek() != JsonToken.END_DOCUMENT) throw IOException("Trailing bulk data")
    }

    private fun readCard(reader: JsonReader): JSONObject {
        if (reader.peek() != JsonToken.BEGIN_OBJECT) throw IOException("Expected a bulk card object")
        return value(reader) as JSONObject
    }

    private fun value(reader: JsonReader): Any = when (reader.peek()) {
        JsonToken.BEGIN_OBJECT -> JSONObject().apply {
            reader.beginObject()
            while (reader.hasNext()) put(reader.nextName(), value(reader))
            reader.endObject()
        }
        JsonToken.BEGIN_ARRAY -> JSONArray().apply {
            reader.beginArray()
            while (reader.hasNext()) put(value(reader))
            reader.endArray()
        }
        JsonToken.STRING -> reader.nextString()
        JsonToken.NUMBER -> reader.nextString().let { it.toLongOrNull() ?: it.toDouble() }
        JsonToken.BOOLEAN -> reader.nextBoolean()
        JsonToken.NULL -> { reader.nextNull(); JSONObject.NULL }
        else -> throw IOException("Invalid bulk JSON value")
    }
}
