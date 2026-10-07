package software.zeasy.momir.sync

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import software.zeasy.momir.data.ArtPack
import software.zeasy.momir.data.Card
import software.zeasy.momir.data.CardRepository
import software.zeasy.momir.data.CardTypes
import software.zeasy.momir.print.EscPos
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.IOException
import java.io.InputStream
import java.util.zip.GZIPInputStream

/**
 * Pulls new cards and artwork straight from Scryfall. There is no server in the
 * middle - Scryfall *is* the backend, and the device speaks to it directly.
 *
 * ## Why streaming matters here
 *
 * The Oracle bulk file is about 23 MB gzipped and roughly 180 MB expanded. The
 * V2 has 909 MB of RAM with maybe 340 MB actually free. Buffering that file, or
 * parsing it as one JSON array, would kill the app.
 *
 * Both the JSONL and JSON array exports are streamed one card at a time.
 */
class ScryfallSync(
    private val repository: CardRepository,
    private val artPack: ArtPack,
    private val openConnection: (String, String) -> InputStream = SyncHttp()::open,
) {

    interface Progress {
        fun onStage(stage: String)
        fun onProgress(done: Int, total: Int)
        fun isCancelled(): Boolean
    }

    data class Outcome(
        val newCards: Int,
        val refreshedCards: Int,
        val newArtwork: Int,
        val failedArtwork: Int,
        val bulkTimestamp: String?,
        val error: String? = null,
    )

    fun run(progress: Progress, fetchArtwork: Boolean = true): Outcome {
        var cardResult = CardCounts(0, 0)
        val artwork = ArtworkCounts()
        var bulkTimestamp: String? = null
        var stage = "Checking Scryfall"
        return try {
            checkCancelled(progress)
            progress.onStage("Checking Scryfall")
            val bulk = fetchBulkEntry() ?: return Outcome(0, 0, 0, 0, null, "Scryfall has no oracle_cards export")

            val lastSeen = repository.meta(META_BULK_TIMESTAMP)
            stage = "Downloading card data"
            cardResult = if (bulk.updatedAt == lastSeen) {
                progress.onStage("Card data already current")
                CardCounts(0, 0)
            } else {
                streamCards(bulk, progress)
            }

            bulkTimestamp = bulk.updatedAt
            if (fetchArtwork && !progress.isCancelled()) {
                stage = "Fetching artwork"
                fetchMissingArtwork(progress, artwork)
            }

            checkCancelled(progress)
            if (artwork.failed == 0) {
                repository.setMeta(META_LAST_SYNC, System.currentTimeMillis().toString())
            }

            Outcome(cardResult.added, cardResult.refreshed, artwork.added, artwork.failed, bulk.updatedAt,
                if (artwork.failed > 0) "${artwork.failed} artworks failed; run sync again to retry missing artwork." else null)
        } catch (e: Exception) {
            Log.e(TAG, "Sync failed", e)
            Outcome(cardResult.added, cardResult.refreshed, artwork.added, artwork.failed, bulkTimestamp,
                "$stage: ${e.message ?: e.javaClass.simpleName}")
        }
    }

    // ------------------------------------------------------------------------

    private data class BulkEntry(val url: String, val updatedAt: String, val isJsonl: Boolean)
    private data class CardCounts(val added: Int, val refreshed: Int)

    private fun fetchBulkEntry(): BulkEntry? {
        openConnection(BULK_INDEX_URL, "application/json").use { stream ->
            val body = stream.bufferedReader().readText()
            val data = JSONObject(body).optJSONArray("data") ?: return null
            for (i in 0 until data.length()) {
                val entry = data.getJSONObject(i)
                if (entry.optString("type") != "oracle_cards") continue
                val jsonl = entry.optString("jsonl_download_uri", "")
                val legacy = entry.optString("download_uri", "")
                val url = if (jsonl.isNotEmpty()) jsonl else legacy
                if (url.isEmpty()) return null
                val timestamp = entry.optString("updated_at")
                if (timestamp.isBlank()) throw IOException("Missing bulk updated_at")
                return BulkEntry(url, timestamp, jsonl.isNotEmpty())
            }
        }
        return null
    }

    private fun streamCards(bulk: BulkEntry, progress: Progress): CardCounts {
        progress.onStage("Downloading card data")

        var added = 0
        var refreshed = 0
        var scanned = 0

        openConnection(bulk.url, "*/*").use { raw ->
            val stream = if (bulk.url.endsWith(".gz")) GZIPInputStream(raw, 1 shl 16) else raw
            BufferedReader(InputStreamReader(stream, Charsets.UTF_8), 1 shl 16).use { reader ->
                repository.beginTransaction()
                try {
                    BulkCards.read(reader, bulk.isJsonl) { json ->
                        checkCancelled(progress)
                        scanned++
                        if (scanned % 2000 == 0) {
                            progress.onStage("Scanned $scanned cards")
                        }

                        if (!isMomirLegal(json)) return@read
                        val card = toCard(json) ?: throw IOException("Bulk card has no oracle_id")

                        val artUri = artUriOf(json)
                        if (repository.updateExisting(card, artUri)) {
                            refreshed++
                        } else {
                            repository.insert(card, artUri)
                            added++
                        }
                    }
                    checkCancelled(progress)
                    if (scanned == 0) throw IOException("Empty bulk export")
                    repository.setMeta(META_BULK_TIMESTAMP, bulk.updatedAt)
                    repository.setTransactionSuccessful()
                } finally {
                    repository.endTransaction()
                }
            }
        }

        progress.onStage("$added new, $refreshed refreshed")
        return CardCounts(added, refreshed)
    }

    private data class ArtworkCounts(var added: Int = 0, var failed: Int = 0)

    private fun fetchMissingArtwork(progress: Progress, counts: ArtworkCounts) {
        val pending = repository.cardsMissingArt(MAX_ART_PER_SYNC)
        if (pending.isEmpty()) return

        progress.onStage("Fetching ${pending.size} artworks")
        if (!artPack.isOpen && !artPack.open()) {
            if (!artPack.createIfMissing(EscPos.PRINT_WIDTH_DOTS) || !artPack.open()) {
                throw IOException("Cannot open artwork pack")
            }
        }

        try {
            pending.forEachIndexed { index, (oracleId, artUri) ->
                checkCancelled(progress)
                progress.onProgress(index + 1, pending.size)

                try {
                    val bytes = openConnection(artUri, "image/*").use { it.readBytes() }
                    val dithered = Dither.fromJpeg(bytes)
                    if (dithered == null) {
                        counts.failed++
                    } else {
                        val offset = artPack.append(dithered.raster)
                        if (offset == null) {
                            counts.failed++
                        } else {
                            repository.recordArt(oracleId, offset, dithered.raster.size, dithered.height)
                            counts.added++
                        }
                    }
                } catch (e: SyncHttp.HttpFailure) {
                    if (e.status == 429 || e.status == 503) {
                        counts.failed++
                        throw e
                    }
                    counts.failed++
                } catch (e: Exception) {
                    Log.w(TAG, "Artwork failed for $oracleId: ${e.message}")
                    counts.failed++
                }

                // Scryfall asks for no more than ten requests a second.
                Thread.sleep(REQUEST_INTERVAL_MS)
            }
        } finally {
            artPack.sync()
        }
    }

    // ------------------------------------------------------------------------
    // Filtering - must stay in step with tools/momirdeck/momirdeck.py
    // ------------------------------------------------------------------------

    /**
     * See momirdeck.is_momir_legal for the reasoning, in particular why paper-ness
     * is decided by legality and not by the `digital` flag. Any change here needs
     * the same change there, or a resync would quietly disagree with the corpus
     * that was pushed over adb.
     */
    private fun isMomirLegal(json: JSONObject): Boolean {
        if (json.optString("layout") in EXCLUDED_LAYOUTS) return false
        if (json.optString("set_type") in EXCLUDED_SET_TYPES) return false
        if (!isPaperCard(json)) return false
        if (json.optString("name").startsWith("A-")) return false

        val typeLine = typeLineForFilter(json)
        if (typeLine.contains("Token")) return false
        // Anything whose front face names a type some category offers. That
        // leaves out plain lands, and the oddities with no real type line at all
        // - dungeons, conspiracies, planes - which nothing could ever roll.
        return (CardTypes.maskOf(typeLine) and CardTypes.ROLLABLE) != 0
    }

    private fun isPaperCard(json: JSONObject): Boolean {
        val legalities = json.optJSONObject("legalities") ?: return false
        return PAPER_FORMATS.any { legalities.optString(it, "not_legal") != "not_legal" }
    }

    private fun typeLineForFilter(json: JSONObject): String {
        if (json.optString("layout") in BOTH_HALVES_ON_FRONT) return json.optString("type_line")
        val face = frontFace(json)
        val faceType = face?.optString("type_line").orEmpty()
        return faceType.ifEmpty { json.optString("type_line") }
    }

    private fun frontFace(json: JSONObject): JSONObject? {
        val faces: JSONArray = json.optJSONArray("card_faces") ?: return null
        return if (faces.length() > 0) faces.optJSONObject(0) else null
    }

    private fun toCard(json: JSONObject): Card? {
        val oracleId = json.optString("oracle_id").ifEmpty { return null }
        val face = frontFace(json)

        var manaCost = face?.optString("mana_cost").orEmpty().ifEmpty { json.optString("mana_cost") }
        var oracleText = face?.optString("oracle_text").orEmpty().ifEmpty { json.optString("oracle_text") }

        if (json.optString("layout") in BOTH_HALVES_ON_FRONT) {
            val faces = json.optJSONArray("card_faces")
            if (faces != null) {
                val parts = ArrayList<String>(faces.length())
                for (i in 0 until faces.length()) {
                    val f = faces.getJSONObject(i)
                    parts.add(
                        "${f.optString("name")} ${f.optString("mana_cost")}\n${f.optString("oracle_text")}".trim()
                    )
                }
                oracleText = parts.joinToString("\n\n")
                manaCost = json.optString("mana_cost").ifEmpty { manaCost }
            }
        }

        return Card(
            oracleId = oracleId,
            name = json.optString("name"),
            manaCost = manaCost,
            manaValue = Math.round(json.optDouble("cmc", 0.0)).toInt(),
            typeLine = json.optString("type_line").ifEmpty { face?.optString("type_line").orEmpty() },
            oracleText = oracleText,
            power = face?.optString("power").orEmpty().ifEmpty { json.optString("power") }.ifEmpty { null },
            toughness = face?.optString("toughness").orEmpty().ifEmpty { json.optString("toughness") }.ifEmpty { null },
            loyalty = face?.optString("loyalty").orEmpty().ifEmpty { json.optString("loyalty") }.ifEmpty { null },
            // Colour identity is a property of the whole card, so it is always
            // top-level even for a two-faced one.
            colorIdentity = json.optJSONArray("color_identity")?.let { array ->
                buildString { for (i in 0 until array.length()) append(array.optString(i)) }
            }.orEmpty(),
            scryfallUri = json.optString("scryfall_uri").substringBefore('?'),
            artOffset = null,
            artLength = null,
            artHeight = null,
        )
    }

    private fun artUriOf(json: JSONObject): String {
        val images = json.optJSONObject("image_uris") ?: frontFace(json)?.optJSONObject("image_uris")
        return images?.optString("art_crop").orEmpty()
    }

    // ------------------------------------------------------------------------

    private fun checkCancelled(progress: Progress) {
        if (progress.isCancelled()) throw IOException("Sync cancelled")
    }

    companion object {
        private const val TAG = "ScryfallSync"
        private const val BULK_INDEX_URL = "https://api.scryfall.com/bulk-data"

        private const val REQUEST_INTERVAL_MS = 100L
        private const val MAX_ART_PER_SYNC = 4000

        const val META_BULK_TIMESTAMP = "bulk_updated_at"
        const val META_LAST_SYNC = "last_sync_ms"

        private val EXCLUDED_LAYOUTS = setOf(
            "token", "double_faced_token", "emblem", "art_series", "vanguard",
            "scheme", "planar", "augment", "host", "reversible_card",
        )
        private val EXCLUDED_SET_TYPES = setOf("funny", "memorabilia", "token", "minigame", "alchemy")
        private val BOTH_HALVES_ON_FRONT = setOf("split", "adventure", "flip")
        private val PAPER_FORMATS = listOf("vintage", "legacy", "commander")
    }
}
