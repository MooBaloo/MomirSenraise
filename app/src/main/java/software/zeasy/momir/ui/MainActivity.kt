package software.zeasy.momir.ui

import android.animation.ObjectAnimator
import android.app.Activity
import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.HapticFeedbackConstants
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator
import android.widget.SeekBar
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import software.zeasy.momir.R
import software.zeasy.momir.Settings
import software.zeasy.momir.data.ArtPack
import software.zeasy.momir.data.Card
import software.zeasy.momir.data.CardCategory
import software.zeasy.momir.data.CardRepository
import software.zeasy.momir.data.ManaValueBucket
import software.zeasy.momir.data.Token
import software.zeasy.momir.databinding.ActivityMainBinding
import software.zeasy.momir.databinding.DialogSettingsBinding
import software.zeasy.momir.print.EscPos
import software.zeasy.momir.print.PrintMode
import software.zeasy.momir.print.PrintResult
import software.zeasy.momir.print.Raster
import software.zeasy.momir.print.SlipContent
import software.zeasy.momir.print.SlipRenderer
import software.zeasy.momir.print.H10sPrinter
import software.zeasy.momir.sync.SyncService
import java.text.NumberFormat
import java.util.Locale
import kotlin.math.abs

/**
 * One screen: pick what to roll and at what mana value, hit the button, take the
 * slip.
 *
 * Everything expensive - the database query, the layout pass, the dither blit,
 * the UART operation - happens off the main thread. On a 909 MB armeabi-v7a
 * device that is the difference between a dial that spins and one that stutters
 * every time you print.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var settings: Settings
    private lateinit var repository: CardRepository
    private lateinit var artPack: ArtPack
    private lateinit var printer: H10sPrinter

    private val renderer = SlipRenderer()

    /**
     * Pinned to US rather than the device locale. The app is English-only, and on
     * a German-configured V2 the default formatter renders 17,497 as "17.497",
     * which an English reader takes for a decimal.
     */
    private val numberFormat: NumberFormat = NumberFormat.getIntegerInstance(Locale.US)

    /** The card currently on the result panel, so its tokens stay reachable. */
    private var lastCard: Card? = null

    /**
     * That card's tokens, read once on the IO thread when it arrives. The panel
     * is written twice per print - once the moment the card is rolled, once when
     * the slip length is known - and the join behind this used to run on the main
     * thread both times, in the same frame the glow starts.
     */
    private var lastTokens: List<Token> = emptyList()

    private val categoryChips = ArrayList<TextView>(CardCategory.values().size)

    /**
     * The current category's mana value buckets, as of the last corpus read. The
     * dial reports a selection on every haptic step of a fling, and going to the
     * database for a count on each of those would put a query on the main thread
     * dozens of times a second.
     */
    private var buckets: List<ManaValueBucket> = emptyList()

    /**
     * The result panel is a receipt, not a status bar. It has said its piece
     * within a few seconds of the slip landing in your hand, and a name left
     * sitting there through the next three rolls just makes the screen look
     * stale. [hideResult] retires it; any new result cancels the timer.
     */
    private val resultHandler = Handler(Looper.getMainLooper())
    private val hideResult = Runnable { retireResult() }

    private var syncSpin: ObjectAnimator? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        settings = Settings(this)
        repository = CardRepository(this)
        artPack = ArtPack(repository.artPackFile)
        printer = H10sPrinter()

        setUpCategorySelector()
        // Not written here: the dial reports every value a fling passes through,
        // and committing each one is a dozen SharedPreferences writes for a
        // gesture with a single outcome. onPause stores where it came to rest.
        binding.manaWheel.onSelectionChanged = { manaValue -> updateCountLabel(manaValue) }

        // The seal carries a planeswalker symbol, not a word, so the label a
        // screen reader would have read off the face has to be stated here.
        binding.printButton.contentDescription = getString(R.string.print)
        binding.printButton.onPress = { rollAndPrint() }
        binding.printButton.onLongPress = { previewSlip() }
        binding.settingsButton.setOnClickListener { showSettings() }
        binding.syncButton.setOnClickListener { startResync() }
        binding.scanButton.setOnClickListener { startScan() }
        binding.searchButton.setOnClickListener { startSearch() }
        binding.resultTokens.setOnClickListener { lastCard?.let { showTokens(it, fromScan = false) } }
        binding.resultText.setOnClickListener { lastCard?.let { showCard(it) } }

        lifecycleScope.launch {
            withContext(Dispatchers.IO) {
                if (repository.open()) artPack.open()
            }
            refreshCorpus()
        }
    }

    override fun onStart() {
        super.onStart()
        SyncService.listener = { status -> onSyncStatus(status) }
        // A resync started before the app went to the background is still going,
        // and installing a listener does not replay what it missed. Only the
        // running case is applied: a finished one would re-toast its outcome and
        // reopen the corpus every time the activity came back.
        (SyncService.lastStatus as? SyncService.Status.Running)?.let { onSyncStatus(it) }
    }

    override fun onPause() {
        super.onPause()
        binding.manaWheel.selectedValue?.let { settings.lastManaValue = it }
    }

    override fun onStop() {
        super.onStop()
        SyncService.listener = null
    }

    override fun onDestroy() {
        super.onDestroy()
        resultHandler.removeCallbacks(hideResult)
        spinSyncButton(false)
        artPack.close()
        repository.close()
    }

    // ------------------------------------------------------------------------
    // Setup
    // ------------------------------------------------------------------------

    private fun setUpCategorySelector() {
        CardCategory.values().forEach { category ->
            val chip = layoutInflater
                .inflate(R.layout.item_category, binding.categoryRow, false) as TextView
            chip.text = getString(category.label)
            chip.tag = category
            chip.setOnClickListener { view ->
                if (settings.cardCategory == category) return@setOnClickListener
                settings.cardCategory = category
                view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                highlightCategory()
                revealCategory(smooth = true)
                // The dial belongs to the category: planeswalkers start at mana
                // value 2, sorceries stop long before creatures do.
                refreshCorpus()
            }
            binding.categoryRow.addView(chip)
            categoryChips += chip
        }
        highlightCategory()
        // Only once the row has been measured is there a width to scroll by.
        binding.categoryScroll.post { revealCategory(smooth = false) }
    }

    private fun highlightCategory() {
        val active = settings.cardCategory
        categoryChips.forEach { it.isSelected = it.tag == active }
    }

    /**
     * Puts the selected chip in the middle of the row. Six chips are twice the
     * width of the screen, and a choice you cannot see is a choice you will
     * assume was lost.
     */
    private fun revealCategory(smooth: Boolean) {
        val chip = categoryChips.firstOrNull { it.isSelected } ?: return
        val target = chip.left - (binding.categoryScroll.width - chip.width) / 2
        if (smooth) binding.categoryScroll.smoothScrollTo(target, 0)
        else binding.categoryScroll.scrollTo(target, 0)
    }

    private data class Corpus(
        val buckets: List<ManaValueBucket>,
        /** The whole corpus, not this category. It is what decides the empty state. */
        val total: Int,
    )

    /** Re-reads everything the category decides, off the main thread, and shows it. */
    private fun refreshCorpus() {
        val category = settings.cardCategory
        // Read the saved value before touching the wheel: assigning values resets
        // the wheel's selection, and settings.lastManaValue is written from the
        // wheel's own callback.
        val restore = settings.lastManaValue
        lifecycleScope.launch {
            val corpus = withContext(Dispatchers.IO) {
                Corpus(
                    buckets = repository.manaValueCounts(category),
                    total = repository.cardCount(),
                )
            }
            showCorpus(corpus, restore)
        }
    }

    private fun showCorpus(corpus: Corpus, restore: Int) {
        buckets = corpus.buckets

        val hasCards = corpus.total > 0
        val visibility = if (hasCards) View.VISIBLE else View.GONE
        binding.emptyState.visibility = if (hasCards) View.GONE else View.VISIBLE
        binding.manaWheel.visibility = visibility
        binding.printButton.visibility = visibility
        binding.countLabel.visibility = visibility
        binding.scanButton.visibility = visibility
        // Nothing to choose between on a device with no cards on it.
        binding.categoryScroll.visibility = visibility

        // Invisible rather than gone: the panel is what the print button is
        // anchored to, so letting it collapse would drag the button down the
        // screen the first time anything prints. Anything already on it stays -
        // a resync finishing is no reason to clear the last roll.
        if (lastCard == null) {
            binding.resultCard.visibility = if (hasCards) View.INVISIBLE else View.GONE
        }

        if (!hasCards) return

        binding.manaWheel.values = corpus.buckets.map { it.manaValue }
        val landing = nearestManaValue(restore)
        if (landing != null) binding.manaWheel.setSelectedValue(landing)
        // A category this corpus cannot serve - planeswalkers in one built before
        // types were stored - gets an empty dial and a dead button. A live button
        // that can only fail is worse than one that says it cannot.
        binding.printButton.isEnabled = landing != null
        updateCountLabel(landing)
    }

    /**
     * The nearest mana value this category actually has, or null if it has none.
     *
     * Switching to planeswalkers with the dial on 1 should land on 2, the
     * cheapest planeswalker Magic has printed - not snap to the bottom of a dial
     * that no longer starts where it did.
     */
    private fun nearestManaValue(desired: Int): Int? =
        buckets.minByOrNull { abs(it.manaValue - desired) }?.manaValue

    /**
     * [manaValue] is null when the category is empty and the dial has nothing to
     * show - which is the one case that survives the counts being switched off,
     * because then the line is not a count. It is the reason the button is dead.
     */
    private fun updateCountLabel(manaValue: Int?) {
        val empty = manaValue == null
        binding.countLabel.visibility =
            if (settings.showCounts || empty) View.VISIBLE else View.INVISIBLE
        if (!settings.showCounts && !empty) return

        val tally = settings.cardCategory.tally
        binding.countLabel.text = if (empty) {
            getString(
                R.string.category_empty,
                resources.getQuantityString(tally, 0, numberFormat.format(0)),
            )
        } else {
            val count = buckets.firstOrNull { it.manaValue == manaValue }?.count ?: 0
            getString(
                R.string.count_at_mv,
                resources.getQuantityString(tally, count, numberFormat.format(count)),
                manaValue,
            )
        }
    }

    // ------------------------------------------------------------------------
    // Printing
    // ------------------------------------------------------------------------

    // Observational only: hardware is acquired by the serialized print job.
    private fun printerReady(): Boolean = printer.isAvailable()

    private fun rollAndPrint() {
        val manaValue = binding.manaWheel.selectedValue ?: return
        val category = settings.cardCategory
        binding.printButton.isBusy = true

        lifecycleScope.launch {
            // Ask the printer first. Everything below this line - the flash, the
            // colours on the rim, the band sweeping out of the top edge, the
            // card's name on the panel - says "that came out of the slot". Play
            // all of it and *then* admit the printer was unavailable and the
            // screen has told a lie for a second and a half.
            if (!printerReady()) {
                binding.printButton.isBusy = false
                binding.printButton.flash(ContextCompat.getColor(this@MainActivity, R.color.danger))
                showNotice(
                    getString(R.string.printer_not_connected),
                    getString(R.string.printer_not_connected_detail),
                    danger = true,
                )
                return@launch
            }

            val card = withContext(Dispatchers.IO) { repository.randomCard(manaValue, category) }
            if (card == null) {
                binding.printButton.isBusy = false
                showNotice(
                    getString(R.string.nothing_to_roll),
                    getString(category.noneAtManaValue, manaValue),
                    danger = true,
                )
                return@launch
            }

            playPrintGlow(card.colorIdentity)
            // Name it now, not after printing. The glow already shows this card's
            // colours, and leaving the previous card's name underneath for the
            // second and a half the printer takes just looks like a stale screen.
            showResult(card)

            printSlip(SlipContent.of(card))
            binding.printButton.isBusy = false
        }
    }

    /**
     * Runs the colour-identity glow: the button takes the card's colours, then a
     * band of them sweeps up the screen and out of the top edge, which is where
     * the V2's paper actually emerges.
     */
    private fun playPrintGlow(colorIdentity: String) {
        val colors = ManaColors.forIdentity(colorIdentity)
        // The whole identity, not just its first colour: the button's rim and its
        // five gems carry a Golgari creature as black *and* green.
        binding.printButton.flash(colors)

        // Anchor the band on the button, in overlay coordinates.
        val button = binding.printButton
        val originY = button.y + button.height / 2f
        binding.glowOverlay.play(colors, originY)
    }

    /**
     * Renders and prints one slip, [times] over. Returns the raster so the caller
     * can report how long the thing actually came out, or null if anything went
     * wrong.
     *
     * The layout is rendered once no matter how many slips come out of it: a
     * player asking for eight Zombies pays for one dither pass, not eight.
     */
    private suspend fun printSlip(content: SlipContent, times: Int = 1): Raster? {
        if (!printerReady()) {
            showNotice(
                getString(R.string.printer_not_connected),
                getString(R.string.printer_not_connected_detail),
                danger = true,
            )
            return null
        }

        val mode = settings.printMode
        val budget = settings.contentBudgetDots

        val raster = withContext(Dispatchers.IO) {
            val art = if (mode == PrintMode.ARTWORK && content.hasArt) {
                artPack.read(content.artOffset!!, content.artLength!!)
            } else null

            // Fall back to the QR layout rather than printing a card-shaped blank
            // when this particular subject has no artwork yet.
            val effectiveMode = if (mode == PrintMode.ARTWORK && art == null) PrintMode.QR else mode

            renderer.render(
                content = content,
                mode = effectiveMode,
                art = art,
                artHeight = content.artHeight ?: 0,
                budgetDots = budget,
            )
        }

        // Stop at the first failure. Paper running out on copy one of five is no
        // reason to push four more rasters at a printer that cannot take them.
        var failure: String? = null
        for (copy in 0 until settings.copies * times.coerceAtLeast(1)) {
            val result = printer.print(raster, settings.printFeedDots)
            if (result is PrintResult.Failure) {
                failure = result.message + if (result.jobStarted) {
                    " A partial slip may have printed; check the paper before trying again."
                } else ""
                break
            }
        }

        failure?.let {
            showNotice(getString(R.string.print_failed), it, danger = true)
            return null
        }
        return raster
    }

    /**
     * Long press: roll a card and write what would have been printed to
     * preview.png instead of printing it. Checking a layout change costs an
     * `adb pull` rather than half a metre of paper.
     */
    private fun previewSlip() {
        val manaValue = binding.manaWheel.selectedValue ?: return
        val category = settings.cardCategory
        lifecycleScope.launch {
            val card = withContext(Dispatchers.IO) { repository.randomCard(manaValue, category) }
            if (card == null) {
                showNotice(
                    getString(R.string.nothing_to_roll),
                    getString(category.noneAtManaValue, manaValue),
                    danger = true,
                )
                return@launch
            }

            // Preview runs the glow too, so the animation can be worked on
            // without feeding paper through the printer for every tweak.
            playPrintGlow(card.colorIdentity)

            withContext(Dispatchers.IO) {
                val content = SlipContent.of(card)
                val mode = settings.printMode
                val art = if (mode == PrintMode.ARTWORK && content.hasArt) {
                    artPack.read(content.artOffset!!, content.artLength!!)
                } else null
                val effectiveMode = if (mode == PrintMode.ARTWORK && art == null) PrintMode.QR else mode

                // The whole torn-off slip, margins and all, rather than the
                // layout on its own: a preview that leaves out the 12 mm of
                // white above the card name is not a preview of the object.
                val bitmap = renderer.composePaper(
                    content, effectiveMode, art, content.artHeight ?: 0,
                    budgetDots = settings.contentBudgetDots,
                    headMarginDots = settings.headMarginDots,
                    bottomMarginDots = settings.bottomMarginDots,
                )
                java.io.FileOutputStream(java.io.File(getExternalFilesDir(null), "preview.png")).use { out ->
                    bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, out)
                }
                bitmap.recycle()
            }

            showResult(card)
        }
    }

    private suspend fun showResult(card: Card) {
        if (card.oracleId != lastCard?.oracleId) {
            lastTokens = withContext(Dispatchers.IO) { repository.tokensFor(card.oracleId) }
        }
        lastCard = card
        binding.resultName.text = card.name
        binding.resultText.isClickable = true
        binding.resultDetail.visibility = View.VISIBLE
        // The type line and what is in the corner, and nothing else. It used
        // to end with the slip's length in millimetres, which is a fact about
        // the printer rather than about the card, and it was the same number
        // every time: every slip is 88 mm. The calibration slip still reports
        // it, which is the one place it means something.
        binding.resultDetail.text = listOfNotNull(
            card.typeLine.takeIf { it.isNotBlank() },
            // Labelled, for the same reason it is labelled on the slip: a bare 4
            // on a planeswalker would read as half a power and toughness.
            card.powerToughness ?: card.loyalty?.let { getString(R.string.result_loyalty, it) },
        ).joinToString(" · ")
        binding.resultStripe.background = stripeFor(card.colorIdentity)

        val tokens = lastTokens
        if (tokens.isEmpty()) {
            binding.resultTokens.visibility = View.GONE
        } else {
            binding.resultTokens.visibility = View.VISIBLE
            binding.resultTokens.text = resources.getQuantityString(R.plurals.tokens_button, tokens.size, tokens.size)
        }

        revealResult(hasTokens = tokens.isNotEmpty())
    }

    /**
     * Brings the panel up and starts its clock.
     *
     * A card with tokens gets far longer, because the panel is not just a report
     * then - it is the only way back to the token sheet, and having it vanish
     * while someone is still reading the creature they just printed would be a
     * worse bug than leaving it up too long.
     */
    private fun revealResult(hasTokens: Boolean) {
        resultHandler.removeCallbacks(hideResult)

        val panel = binding.resultCard
        if (panel.visibility != View.VISIBLE || panel.alpha < 1f) {
            panel.animate().cancel()
            panel.alpha = 0f
            panel.translationY = RESULT_RISE_DP * resources.displayMetrics.density
            panel.visibility = View.VISIBLE
            panel.animate().alpha(1f).translationY(0f).setDuration(220).start()
        }

        val linger = if (hasTokens) RESULT_LINGER_TOKENS_MS else RESULT_LINGER_MS
        resultHandler.postDelayed(hideResult, linger)
    }

    private fun retireResult() {
        val panel = binding.resultCard
        if (panel.visibility != View.VISIBLE) return
        // The button is wearing this card's colours; they go with it.
        binding.printButton.discharge()
        panel.animate()
            .alpha(0f)
            // Same beat and same curve as the rim giving up its colours, so the
            // last roll leaves the screen as one gesture rather than two.
            .setDuration(PrintButton.DISCHARGE_MS)
            .setInterpolator(DecelerateInterpolator())
            // Invisible, not gone: the print button is anchored to this panel.
            .withEndAction { panel.visibility = View.INVISIBLE }
            .start()
    }

    /** A vertical ramp through a colour identity, for the panel's 4 dp stripe. */
    private fun stripeFor(identity: String): GradientDrawable =
        ManaColors.stripe(ManaColors.forIdentity(identity), resources.displayMetrics.density)

    // ------------------------------------------------------------------------
    // The card itself
    // ------------------------------------------------------------------------

    /**
     * Opens the card the panel is naming.
     *
     * The slip is 48 mm wide and is usually across the table by the time someone
     * wants to re-read an ability off it. Everything needed to show the card is
     * already here - the artwork included - so the panel's name is a way in
     * rather than a label.
     */
    private fun showCard(card: Card) {
        // The panel must not time out from under an open sheet; the sheet's own
        // dismiss restarts the clock.
        resultHandler.removeCallbacks(hideResult)
        val tokens = lastTokens

        lifecycleScope.launch {
            // Off the main thread: this is a seek and a 13 KB read on eMMC that
            // is slow enough to drop frames if it happens during the fade-in.
            val art = withContext(Dispatchers.IO) {
                if (card.hasArt) artPack.read(card.artOffset!!, card.artLength!!) else null
            }

            CardSheet(
                activity = this@MainActivity,
                card = card,
                tokenCount = tokens.size,
                art = art,
                onPrint = { printCard(card) },
                onTokens = { showTokens(card, fromScan = false) },
                onDismiss = { revealResult(hasTokens = tokens.isNotEmpty()) },
            ).show()
        }
    }

    /**
     * Prints one named card, rather than one the dial rolled.
     *
     * The same glow, the same panel and the same printer as a roll - the only
     * thing that differs is where the card came from, and by the time it gets
     * here that is nobody's business.
     */
    private fun printCard(card: Card) {
        binding.printButton.isBusy = true
        lifecycleScope.launch {
            playPrintGlow(card.colorIdentity)
            showResult(card)
            printSlip(SlipContent.of(card))
            binding.printButton.isBusy = false
        }
    }

    // ------------------------------------------------------------------------
    // Search
    // ------------------------------------------------------------------------

    private fun startSearch() {
        startActivityForResult(
            Intent(this, SearchActivity::class.java),
            SearchActivity.REQUEST_SEARCH,
        )
    }

    // ------------------------------------------------------------------------
    // Tokens
    // ------------------------------------------------------------------------

    private fun showTokens(card: Card, fromScan: Boolean) {
        // Read when the card arrived, on the IO thread; this is only ever asked
        // about the card the panel is currently showing.
        val tokens = lastTokens
        if (tokens.isEmpty()) {
            showNotice(getString(R.string.no_tokens), getString(R.string.tokens_none, card.name))
            return
        }

        // The panel must not time out from under an open sheet; the sheet's own
        // dismiss restarts the clock.
        resultHandler.removeCallbacks(hideResult)

        TokenSheet(
            activity = this,
            cardName = card.name,
            tokens = tokens,
            fromScan = fromScan,
            onPrint = { chosen, each -> printTokens(chosen, each) },
            onDismiss = { revealResult(hasTokens = true) },
        ).show()
    }

    /** Prints [each] copies of every token in [tokens]. */
    private fun printTokens(tokens: List<Token>, each: Int) {
        binding.printButton.isBusy = true
        val slips = tokens.size * each
        showNotice(resources.getQuantityString(R.plurals.tokens_queued, slips, slips))

        lifecycleScope.launch {
            for (token in tokens) {
                // Tokens carry plain colours rather than a colour identity, but
                // they read the same way on screen.
                playPrintGlow(token.colors)
                if (printSlip(SlipContent.of(token), times = each) == null) break
            }
            binding.printButton.isBusy = false
        }
    }

    // ------------------------------------------------------------------------
    // Scanning
    // ------------------------------------------------------------------------

    private fun startScan() {
        startActivityForResult(
            Intent(this, ScannerActivity::class.java),
            ScannerActivity.REQUEST_SCAN,
        )
    }

    @Deprecated("startActivityForResult is the right size of tool for one result on API 25")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (resultCode != Activity.RESULT_OK) return

        if (requestCode == SearchActivity.REQUEST_SEARCH) {
            val oracleId = data?.getStringExtra(SearchActivity.EXTRA_ORACLE_ID) ?: return
            lifecycleScope.launch {
                val card = withContext(Dispatchers.IO) { repository.cardByOracleId(oracleId) }
                // Shown rather than printed: the search hands over a card, and
                // the press that spends paper should be one somebody meant.
                if (card != null) {
                    showResult(card)
                    showCard(card)
                }
            }
            return
        }
        if (requestCode != ScannerActivity.REQUEST_SCAN) return

        val text = data?.getStringExtra(ScannerActivity.EXTRA_RESULT) ?: return
        if (!text.contains("scryfall.com")) {
            showNotice(
                getString(R.string.scan_failed),
                getString(R.string.scan_not_a_card, text.take(60)),
                danger = true,
            )
            return
        }

        lifecycleScope.launch {
            val card = withContext(Dispatchers.IO) { repository.cardByScryfallUri(text) }
            if (card == null) {
                showNotice(getString(R.string.scan_failed), getString(R.string.scan_not_found), danger = true)
                return@launch
            }

            // Same colour flare a print gets. Coming back from the camera to a
            // sheet that simply appeared is disorienting; coming back to the
            // screen taking the scanned card's colours is not.
            playPrintGlow(card.colorIdentity)
            showResult(card)
            showTokens(card, fromScan = true)
        }
    }

    // ------------------------------------------------------------------------
    // Settings
    // ------------------------------------------------------------------------

    private fun showSettings() {
        val view = DialogSettingsBinding.inflate(layoutInflater)

        // Written as it is flipped rather than on Close, unlike the seek bars: a
        // switch is a decision the moment it moves, and the test print below it
        // should come out in the mode you just chose.
        view.artworkSwitch.isChecked = settings.printMode == PrintMode.ARTWORK
        view.artworkSwitch.setOnCheckedChangeListener { _, checked ->
            settings.printMode = if (checked) PrintMode.ARTWORK else PrintMode.QR
        }

        view.countsSwitch.isChecked = settings.showCounts
        view.countsSwitch.setOnCheckedChangeListener { _, checked ->
            settings.showCounts = checked
            updateCountLabel(binding.manaWheel.selectedValue)
        }

        view.copiesSeek.progress = settings.copies - 1
        view.slipSeek.progress = (settings.slipLengthMm - SLIP_MIN_MM).toInt()
        view.feedSeek.progress = settings.tearGapMm.toInt()
        view.bottomMarginSeek.progress = settings.bottomMarginMm.toInt()

        fun refreshLabels() {
            view.copiesLabel.text = "${getString(R.string.settings_copies)}: ${view.copiesSeek.progress + 1}"
            view.slipLabel.text =
                "${getString(R.string.settings_slip_length)}: ${view.slipSeek.progress + SLIP_MIN_MM.toInt()} mm"
            view.feedLabel.text = "${getString(R.string.settings_tear_gap)}: ${view.feedSeek.progress} mm"
            view.bottomMarginLabel.text =
                "${getString(R.string.settings_bottom_margin)}: ${view.bottomMarginSeek.progress} mm"
        }
        refreshLabels()

        val watcher = object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) = refreshLabels()
            override fun onStartTrackingTouch(seekBar: SeekBar?) = Unit
            override fun onStopTrackingTouch(seekBar: SeekBar?) = Unit
        }
        view.copiesSeek.setOnSeekBarChangeListener(watcher)
        view.slipSeek.setOnSeekBarChangeListener(watcher)
        view.feedSeek.setOnSeekBarChangeListener(watcher)
        view.bottomMarginSeek.setOnSeekBarChangeListener(watcher)

        view.diagnostics.text = buildDiagnostics()
        view.testPrintButton.setOnClickListener { printTestPattern() }

        // The heading lives in the layout, in the same serif the rest of the app
        // uses; an AlertDialog title would come out in the platform sans.
        AlertDialog.Builder(this)
            .setView(view.root)
            .setPositiveButton(R.string.close) { _, _ ->
                settings.copies = view.copiesSeek.progress + 1
                settings.slipLengthMm = view.slipSeek.progress + SLIP_MIN_MM
                settings.tearGapMm = view.feedSeek.progress.toFloat()
                settings.bottomMarginMm = view.bottomMarginSeek.progress.toFloat()
            }
            .show()
    }

    private fun buildDiagnostics(): String {
        val printerLines = printer.diagnostics().map { (key, value) -> "$key  $value" }
        val corpusLines = if (repository.isReady) {
            listOf(
                "cards   ${numberFormat.format(repository.cardCount())}",
                "art     ${numberFormat.format(repository.artCount())}",
                "tokens  ${numberFormat.format(repository.tokenCount())}",
                "bulk    ${repository.meta("bulk_updated_at") ?: "?"}",
            )
        } else listOf("corpus  not loaded")
        return (printerLines + corpusLines).joinToString("\n")
    }

    /**
     * Prints a calibration slip so the tear-feed setting can be dialled in
     * without guessing: print it, tear it, hold it against a ruler and compare
     * with the length the app reports.
     */
    private fun printTestPattern() {
        lifecycleScope.launch {
            val content = SlipContent(
                title = "Test Slip",
                badge = "8",
                cost = "4 W U",
                typeLine = "Calibration — Tear here and measure",
                powerToughness = null,
                loyalty = null,
                rulesText = "Tear this off and hold it against a ruler. The white above this " +
                    "text is the gap between the print head and the tear bar; set \"head to " +
                    "tear bar\" to what you measure there.\nThen check the whole slip against " +
                    "the length the app reports, and that it slides into a sleeve. A Magic " +
                    "card is 88 mm long.",
                linkUri = "https://scryfall.com/card/test/1/test-slip",
                artOffset = null, artLength = null, artHeight = null,
            )
            val raster = printSlip(content)
            if (raster != null) {
                showNotice(
                    getString(R.string.settings_test_print),
                    getString(R.string.slip_length_toast, settings.printedLengthMm(raster.height)),
                )
            }
        }
    }

    // ------------------------------------------------------------------------
    // Sync
    // ------------------------------------------------------------------------

    private fun startResync() {
        if (SyncService.lastStatus is SyncService.Status.Running) {
            showNotice(getString(R.string.sync_title), getString(R.string.sync_already_running))
            return
        }
        SyncService.start(this)
        showNotice(getString(R.string.sync_title), getString(R.string.sync_starting))
    }

    /**
     * The only sign a resync is running, now that nothing on this screen reports
     * a stage: the icon it was started from turns. One animator on one 44 dp
     * view, and the notification carries the detail for anyone who wants it.
     */
    private fun spinSyncButton(spinning: Boolean) {
        if (spinning) {
            if (syncSpin != null) return
            syncSpin = ObjectAnimator.ofFloat(binding.syncButton, View.ROTATION, 0f, 360f).apply {
                duration = SYNC_SPIN_MS
                repeatCount = ObjectAnimator.INFINITE
                repeatMode = ObjectAnimator.RESTART
                interpolator = LinearInterpolator()
                start()
            }
        } else {
            syncSpin?.cancel()
            syncSpin = null
            binding.syncButton.rotation = 0f
        }
    }

    private fun onSyncStatus(status: SyncService.Status) {
        when (status) {
            is SyncService.Status.Running -> spinSyncButton(true)

            is SyncService.Status.Finished -> {
                spinSyncButton(false)
                val outcome = status.outcome
                if (outcome.error != null) {
                    showNotice(getString(R.string.sync_title), outcome.error, danger = true)
                } else {
                    showNotice(
                        getString(R.string.sync_title),
                        getString(R.string.sync_done, outcome.newCards, outcome.newArtwork),
                    )
                }
                lifecycleScope.launch {
                    withContext(Dispatchers.IO) {
                        repository.close()
                        artPack.close()
                        repository.open()
                        artPack.open()
                    }
                    refreshCorpus()
                }
            }

            SyncService.Status.Idle -> spinSyncButton(false)
        }
    }

    /**
     * Says something on the result panel instead of over it.
     *
     * Every failure this app can have used to arrive as a stock Android toast:
     * a grey lozenge in the system font, floating over a screen that had just
     * been designed as a card. The panel is already anchored, already animated
     * and already retires itself, and a printer that is not connected is at
     * least as much of a result as a creature that printed.
     *
     * [danger] swaps the colour-identity stripe for a solid red one, which is
     * the same trick the panel plays with a card's colours.
     */
    private fun showNotice(title: String, detail: String? = null, danger: Boolean = false) {
        binding.resultName.text = title
        binding.resultDetail.text = detail.orEmpty()
        binding.resultDetail.visibility = if (detail.isNullOrBlank()) View.GONE else View.VISIBLE
        // The chip and the way into the card both belong to a card, and this is
        // not one. Tapping "Printer not connected" should not open whatever was
        // rolled before it.
        binding.resultTokens.visibility = View.GONE
        binding.resultText.isClickable = false
        binding.resultStripe.background = ManaColors.stripe(
            listOf(ContextCompat.getColor(this, if (danger) R.color.danger else R.color.accent)),
            resources.displayMetrics.density,
        )
        revealResult(hasTokens = false)
    }


    companion object {
        private const val SLIP_MIN_MM = 50f

        /** How long the result panel stays up before it fades out. */
        private const val RESULT_LINGER_MS = 15_000L

        /** Longer when the panel is also the way into the token sheet. */
        private const val RESULT_LINGER_TOKENS_MS = 45_000L

        /** How far the panel rises as it fades in. */
        private const val RESULT_RISE_DP = 10f

        /** One turn of the sync icon. Slow enough to read as working, not as spinning. */
        private const val SYNC_SPIN_MS = 1400L
    }
}
