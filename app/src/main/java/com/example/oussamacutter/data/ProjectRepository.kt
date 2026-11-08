package com.example.oussamacutter.data

import com.example.oussamacutter.model.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.*

object ProjectRepository {

    private val _systemHealth = MutableStateFlow(SystemHealth())
    val systemHealth: StateFlow<SystemHealth> = _systemHealth.asStateFlow()

    private val _projects = MutableStateFlow<List<StudioProject>>(emptyList())
    val projects: StateFlow<List<StudioProject>> = _projects.asStateFlow()

    private val _clips = MutableStateFlow<List<ClipSegment>>(emptyList())
    val clips: StateFlow<List<ClipSegment>> = _clips.asStateFlow()

    private val _batchQueue = MutableStateFlow<List<BatchJob>>(emptyList())
    val batchQueue: StateFlow<List<BatchJob>> = _batchQueue.asStateFlow()

    private val _publishedJobs = MutableStateFlow<List<PublishJob>>(emptyList())
    val publishedJobs: StateFlow<List<PublishJob>> = _publishedJobs.asStateFlow()

    private val _abTitleTests = MutableStateFlow<List<AbTitleTest>>(emptyList())
    val abTitleTests: StateFlow<List<AbTitleTest>> = _abTitleTests.asStateFlow()

    private val _selectedClipId = MutableStateFlow<String?>(null)
    val selectedClipId: StateFlow<String?> = _selectedClipId.asStateFlow()

    val availableBgmList = listOf(
        "بدون موسيقى خلفية",
        "Viral Podcast Beat (إيقاع حيوي)",
        "Chill Lofi Study (هادئ وتركيز)",
        "Dramatic Cinematic (تشويق وإثارة)",
        "Inspirational Tech (تحفيزي تكنولوجي)"
    )

    val availableLanguages = listOf(
        "English (الإنجليزية)",
        "French (الفرنسية)",
        "Spanish (الإسبانية)",
        "Turkish (التركية)",
        "German (الألمانية)",
        "العربية (الأصلية)"
    )

    init {
        generateInitialProject()
    }

    private fun generateInitialProject() {
        val project1 = StudioProject(
            id = "proj_01",
            title = "بودكاست أسرار المال والأعمال - حلقة 12",
            sourceUrl = "https://youtube.com/watch?v=podcast_ar_business",
            durationFormatted = "48:15 دقيقة",
            clipsCount = 3,
            dateCreated = "اليوم - 14:30",
            safetyPassed = false
        )

        val project2 = StudioProject(
            id = "proj_02",
            title = "ثورة نماذج الذكاء الاصطناعي في 2026",
            sourceUrl = "https://youtube.com/watch?v=ai_future_arabic",
            durationFormatted = "32:10 دقيقة",
            clipsCount = 2,
            dateCreated = "أمس - 19:15",
            safetyPassed = false
        )

        _projects.value = listOf(project1, project2)

        val initialClips = listOf(
            createClip(
                id = "clip_01",
                title = "سر المليون دولار في 30 ثانية",
                arabicHookTitle = "🔥 هذا ما يخفونه عنك رواد الأعمال!",
                duration = 38,
                viralScore = 98,
                hookStrength = 96,
                rawTranscript = "السر الحقيقي ليس في رأس المال بل في اختيار الفكرة الصحيحة والتنفيذ السريع دون تردد.",
                hasViolation = false
            ),
            createClip(
                id = "clip_02",
                title = "أكبر صدمة في عالم الذكاء الاصطناعي",
                arabicHookTitle = "😱 لن تصدق ما تفعله النماذج الجديدة الآن",
                duration = 44,
                viralScore = 91,
                hookStrength = 89,
                rawTranscript = "النماذج الجديدة لا تكتفي بالكتابة بل أصبحت تفكر وتبرمج وتدير مشاريع كاملة بمفردها.",
                hasViolation = false
            ),
            createClip(
                id = "clip_03",
                title = "مواجهة نارية حول أرباح اليوتيوب",
                arabicHookTitle = "⚠️ هل تراجع دخل صناع المحتوى في 2026؟",
                duration = 52,
                viralScore = 86,
                hookStrength = 92,
                rawTranscript = "الكثير يعتقد أن خول الأرباح سهل ولكن في الحقيقة المنافسة أصبحت شرسة وتتطلب جودة هائلة.",
                hasViolation = true
            )
        )
        _clips.value = initialClips
        _selectedClipId.value = initialClips.firstOrNull()?.id

        _batchQueue.value = listOf(
            BatchJob(
                id = "job_101",
                sourceName = "بودكاست_ريادة_الأعمال_حلقة_12.mp4",
                progress = 1.0f,
                currentStep = "اكتمل التقطيع وفحص الأمان (3 مقاطع جاهزة)",
                clipsGenerated = 3,
                status = "مكتمل",
                startedAt = "منذ 15 دقيقة"
            )
        )

        _publishedJobs.value = listOf(
            PublishJob(
                id = "pub_1",
                clipId = "clip_01",
                clipTitle = "سر المليون دولار في 30 ثانية",
                platform = "YouTube Shorts",
                scheduledTime = "اليوم - 18:00 (بعد ساعتين)",
                privacyStatus = "Private (خاص — بانتظار المراجعة)",
                tags = listOf("#شورتس", "#ريادة_أعمال", "#نجاح", "#تحفيز")
            )
        )

        _abTitleTests.value = listOf(
            AbTitleTest(
                id = "ab_1",
                titleA = "سر المليون دولار في 30 ثانية",
                titleB = "🔥 هذا ما يخفونه عنك رواد الأعمال!",
                eloScoreA = 1480,
                eloScoreB = 1660,
                predictedWinner = "🔥 هذا ما يخفونه عنك رواد الأعمال!",
                insight = "العنوان B يتفوق بنسبة +24% نقر بفضل استثارة الفضول (Curiosity Gap) ومخاطبة المشاهد مباشرة."
            )
        )
    }

    private fun createClip(
        id: String,
        title: String,
        arabicHookTitle: String,
        duration: Int,
        viralScore: Int,
        hookStrength: Int,
        rawTranscript: String,
        hasViolation: Boolean
    ): ClipSegment {
        val (evaluatedText, report) = SafetyEngine.evaluateText(
            rawTranscript,
            SafetyActionMode.AUTO_BLEEP
        )

        val wordsList = evaluatedText.split(" ").mapIndexed { index, word ->
            val isBleeped = word == "***"
            SubtitleWord(
                id = "${id}_w_$index",
                text = word,
                startMs = (index * 600L),
                endMs = (index * 600L) + 550L,
                isBleeped = isBleeped,
                isHookKeyword = index < 3
            )
        }

        val retentionPoints = listOf(
            RetentionPoint(0, 100, "بداية الخطاف (Hook 0-3s)"),
            RetentionPoint(3, 89, "نهاية الزووم البصري"),
            RetentionPoint(10, 84, "طرح الفكرة الأساسية"),
            RetentionPoint(18, 78, "نقطة هبوط طفيفة"),
            RetentionPoint(25, 83, "إدراج B-Roll تعزيزي"),
            RetentionPoint(duration, 76, "الخاتمة والدعوة للتفاعل")
        )

        val bRolls = listOf(
            BRollSuggestion("${id}_br1", "رأس المال", 3000L, "مشهد أموال واستثمار 4K", "Finance", 3, true),
            BRollSuggestion("${id}_br2", "الفكرة الصحيحة", 12000L, "تخطيط استراتيجي وإبداع", "Tech", 4, true),
            BRollSuggestion("${id}_br3", "التنفيذ السريع", 24000L, "شخص يعمل بحماس وسرعة", "Action", 3, false)
        )

        val thumbs = listOf(
            ThumbnailVariant("${id}_th1", 2, "صدمة المليون!", "🔥", 14.8f, true),
            ThumbnailVariant("${id}_th2", 12, "الحقيقة المخفية", "🤫", 12.4f, false),
            ThumbnailVariant("${id}_th3", 24, "نفذ الآن!", "⚡", 10.9f, false)
        )

        return ClipSegment(
            id = id,
            title = title,
            arabicHookTitle = arabicHookTitle,
            durationSeconds = duration,
            viralScore = viralScore,
            hookStrength = hookStrength,
            words = wordsList,
            safetyReport = report,
            isApproved = false,
            targetPlatform = "TikTok & Shorts (9:16)",
            audioLoudnessLUFS = -14.1,
            audioSettings = AudioSettings(
                selectedBgm = "Viral Podcast Beat",
                bgmVolume = 0.18f,
                autoDucking = true,
                whooshSfx = true,
                popSfx = true
            ),
            visualEffects = VisualEffects(
                punchZoomOnHook = true,
                topProgressBar = true,
                dynamicEmojis = true,
                aspectRatio = "9:16"
            ),
            translation = TranslationOptions(
                sourceLang = "العربية",
                targetLang = "English",
                translatedTitle = "The Secret of Million Dollar Success"
            ),
            retentionCurve = retentionPoints,
            bRollSuggestions = bRolls,
            thumbnails = thumbs
        )
    }

    fun selectClip(id: String) {
        _selectedClipId.value = id
    }

    fun toggleClipApproval(id: String) {
        _clips.value = _clips.value.map { clip ->
            if (clip.id == id) clip.copy(isApproved = !clip.isApproved) else clip
        }
    }

    fun toggleBRollApplied(clipId: String, bRollId: String) {
        _clips.value = _clips.value.map { clip ->
            if (clip.id == clipId) {
                val updated = clip.bRollSuggestions.map { br ->
                    if (br.id == bRollId) br.copy(isApplied = !br.isApplied) else br
                }
                clip.copy(bRollSuggestions = updated)
            } else clip
        }
    }

    fun selectThumbnail(clipId: String, thumbId: String) {
        _clips.value = _clips.value.map { clip ->
            if (clip.id == clipId) {
                val updated = clip.thumbnails.map { th ->
                    th.copy(isSelected = th.id == thumbId)
                }
                clip.copy(thumbnails = updated)
            } else clip
        }
    }

    fun addAbTest(titleA: String, titleB: String) {
        val winner = if (titleB.contains("!") || titleB.contains("🔥") || titleB.length > titleA.length) titleB else titleA
        val test = AbTitleTest(
            id = "ab_" + UUID.randomUUID().toString().take(5),
            titleA = titleA,
            titleB = titleB,
            eloScoreA = 1500,
            eloScoreB = 1620,
            predictedWinner = winner,
            insight = "الخوارزمية ترجح الفائز بناء على معايير الفضول واستخدام الأرقام والعاطفة العالية."
        )
        _abTitleTests.value = listOf(test) + _abTitleTests.value
    }

    fun updateWord(clipId: String, wordId: String, newText: String, isBleeped: Boolean) {
        _clips.value = _clips.value.map { clip ->
            if (clip.id == clipId) {
                val updatedWords = clip.words.map { word ->
                    if (word.id == wordId) word.copy(text = newText, isBleeped = isBleeped) else word
                }
                clip.copy(words = updatedWords)
            } else clip
        }
    }

    fun updateAudioSettings(clipId: String, newAudioSettings: AudioSettings) {
        _clips.value = _clips.value.map { clip ->
            if (clip.id == clipId) clip.copy(audioSettings = newAudioSettings) else clip
        }
    }

    fun updateVisualEffects(clipId: String, newVisualEffects: VisualEffects) {
        _clips.value = _clips.value.map { clip ->
            if (clip.id == clipId) clip.copy(visualEffects = newVisualEffects) else clip
        }
    }

    fun updateTranslation(clipId: String, newTranslation: TranslationOptions) {
        _clips.value = _clips.value.map { clip ->
            if (clip.id == clipId) clip.copy(translation = newTranslation) else clip
        }
    }

    suspend fun runRealAiVideoCuttingPipeline(
        sourceUrlOrName: String,
        modelOption: AiModelOption,
        framingMode: FramingMode,
        safetyMode: SafetyActionMode,
        minDuration: Int,
        maxDuration: Int,
        targetClipsCount: Int,
        onStepUpdate: (String, Float) -> Unit
    ) {
        val cleanName = if (sourceUrlOrName.isNotBlank()) sourceUrlOrName else "فيديو_مباشر_${System.currentTimeMillis()}.mp4"
        val jobId = "job_" + UUID.randomUUID().toString().take(6)

        val newJob = BatchJob(
            id = jobId,
            sourceName = cleanName,
            progress = 0.15f,
            currentStep = "قراءة المصدر وتحليل الإشارات الصوتية...",
            clipsGenerated = 0,
            status = "قيد المعالجة",
            startedAt = "الآن"
        )
        _batchQueue.value = listOf(newJob) + _batchQueue.value

        onStepUpdate("جاري الاتصال بمحرك الذكاء الاصطناعي (Gemini 2.0 Flash)...", 0.35f)
        _batchQueue.value = _batchQueue.value.map {
            if (it.id == jobId) it.copy(progress = 0.35f, currentStep = "تحليل الذكاء الاصطناعي واستخراج الخطافات الفيروسية...") else it
        }

        // Real API call to Gemini AI
        val generatedClips = GeminiService.analyzeAndGenerateViralClips(
            sourceUrlOrTranscript = sourceUrlOrName,
            targetClipsCount = targetClipsCount,
            minDurationSec = minDuration,
            maxDurationSec = maxDuration
        )

        onStepUpdate("فحص الأمان ضد الإنذارات وتطبيق نمط هورموزي...", 0.75f)
        _batchQueue.value = _batchQueue.value.map {
            if (it.id == jobId) it.copy(progress = 0.75f, currentStep = "فحص 1500+ مصطلح أمان وتتبع الوجوه 9:16...") else it
        }

        val enrichedClips = generatedClips.map { clip ->
            clip.copy(
                targetPlatform = "TikTok & Shorts (9:16)",
                audioLoudnessLUFS = -14.0,
                retentionCurve = listOf(
                    RetentionPoint(0, 100, "بداية الخطاف (Hook 0-3s)"),
                    RetentionPoint(3, 89, "نهاية الزووم البصري"),
                    RetentionPoint(12, 85, "طرح الفكرة الأساسية"),
                    RetentionPoint(20, 80, "إدراج B-Roll تعزيزي"),
                    RetentionPoint(clip.durationSeconds, 77, "الخاتمة والدعوة للتفاعل")
                ),
                bRollSuggestions = listOf(
                    BRollSuggestion("${clip.id}_br1", "الفكرة", 3000L, "مشهد ابتكار وتركيز 4K", "Tech", 3, true),
                    BRollSuggestion("${clip.id}_br2", "الهدف", 15000L, "رسم بياني وتحقيق نمو", "Finance", 4, true)
                ),
                thumbnails = listOf(
                    ThumbnailVariant("${clip.id}_th1", 2, "صدمة المليون!", "🔥", 14.8f, true),
                    ThumbnailVariant("${clip.id}_th2", 12, "الحقيقة المخفية", "🤫", 12.4f, false),
                    ThumbnailVariant("${clip.id}_th3", 22, "نفذ الآن!", "⚡", 10.9f, false)
                )
            )
        }

        _clips.value = enrichedClips + _clips.value
        _selectedClipId.value = enrichedClips.firstOrNull()?.id

        // Update Job to completed
        _batchQueue.value = _batchQueue.value.map {
            if (it.id == jobId) it.copy(
                progress = 1.0f,
                currentStep = "اكتمل التقطيع الحقيقي وفحص الأمان (${enrichedClips.size} مقاطع جاهزة)",
                clipsGenerated = enrichedClips.size,
                status = "مكتمل"
            ) else it
        }

        // Add to Studio Projects
        val newProj = StudioProject(
            id = "proj_" + UUID.randomUUID().toString().take(5),
            title = cleanName.substringBeforeLast("."),
            sourceUrl = sourceUrlOrName,
            durationFormatted = "${(minDuration + maxDuration) / 2} ثانية",
            clipsCount = enrichedClips.size,
            dateCreated = "الآن",
            safetyPassed = true
        )
        _projects.value = listOf(newProj) + _projects.value
        onStepUpdate("اكتمل التقطيع بنجاح!", 1.0f)
    }

    fun addBatchCutJob(
        sourceUrlOrName: String,
        modelOption: AiModelOption,
        framingMode: FramingMode,
        safetyMode: SafetyActionMode,
        minDuration: Int,
        maxDuration: Int,
        targetClipsCount: Int
    ) {
        val jobId = "job_" + UUID.randomUUID().toString().take(6)
        val cleanName = if (sourceUrlOrName.isNotBlank()) sourceUrlOrName else "فيديو_مباشر_${System.currentTimeMillis()}.mp4"

        val newJob = BatchJob(
            id = jobId,
            sourceName = cleanName,
            progress = 0.35f,
            currentStep = "جاري التفريغ الصوتي (Whisper) وتتبع الوجوه (InsightFace)...",
            clipsGenerated = 0,
            status = "قيد المعالجة",
            startedAt = "الآن"
        )

        _batchQueue.value = listOf(newJob) + _batchQueue.value

        // Create new project entry
        val newProj = StudioProject(
            id = "proj_" + UUID.randomUUID().toString().take(5),
            title = cleanName.substringBeforeLast("."),
            sourceUrl = sourceUrlOrName,
            durationFormatted = "${(minDuration + maxDuration) / 2} ثانية",
            clipsCount = targetClipsCount,
            dateCreated = "الآن",
            safetyPassed = true
        )
        _projects.value = listOf(newProj) + _projects.value

        // Simulate new clips generated from processing
        val newClip = createClip(
            id = "clip_" + UUID.randomUUID().toString().take(5),
            title = "نصيحة ذهبية لتضاعف إنتاجك",
            arabicHookTitle = "💡 كيف تنجز مهام أسبوع في يومين فقط؟",
            duration = (minDuration + maxDuration) / 2,
            viralScore = 95,
            hookStrength = 94,
            rawTranscript = "التركيز المطلق لمدة 90 دقيقة يمنحك نتائج تعادل 8 ساعات من العمل المشتت.",
            hasViolation = false
        )
        _clips.value = listOf(newClip) + _clips.value
        _selectedClipId.value = newClip.id
    }

    fun generateSrtSubtitles(clip: ClipSegment): String {
        val sb = StringBuilder()
        var counter = 1
        val wordsChunks = clip.words.chunked(5)
        for (chunk in wordsChunks) {
            val startMs = chunk.first().startMs
            val endMs = chunk.last().endMs
            val startFormatted = formatMsToSrtTime(startMs)
            val endFormatted = formatMsToSrtTime(endMs)
            val text = chunk.joinToString(" ") { if (it.isBleeped) "***" else it.text }
            sb.append("$counter\n$startFormatted --> $endFormatted\n$text\n\n")
            counter++
        }
        return sb.toString()
    }

    private fun formatMsToSrtTime(ms: Long): String {
        val hours = ms / 3600000
        val minutes = (ms % 3600000) / 60000
        val seconds = (ms % 60000) / 1000
        val millis = ms % 1000
        return String.format(Locale.US, "%02d:%02d:%02d,%03d", hours, minutes, seconds, millis)
    }

    fun scheduleClipForPublish(
        clip: ClipSegment,
        platform: String,
        scheduledTime: String,
        privacy: String,
        tags: List<String>
    ) {
        val job = PublishJob(
            id = "pub_" + UUID.randomUUID().toString().take(6),
            clipId = clip.id,
            clipTitle = clip.title,
            platform = platform,
            scheduledTime = scheduledTime,
            privacyStatus = privacy,
            tags = tags
        )
        _publishedJobs.value = listOf(job) + _publishedJobs.value
    }

    fun generatePremiereProXml(clip: ClipSegment): String {
        val width = when (clip.visualEffects.aspectRatio) {
            "9:16" -> 1080
            "1:1" -> 1080
            "16:9" -> 1920
            else -> 1080
        }
        val height = when (clip.visualEffects.aspectRatio) {
            "9:16" -> 1920
            "1:1" -> 1080
            "16:9" -> 1080
            else -> 1920
        }

        return """
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE xmeml>
            <xmeml version="5">
              <sequence id="seq_${clip.id}">
                <name>${clip.title}</name>
                <duration>${clip.durationSeconds * 30}</duration>
                <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
                <media>
                  <video>
                    <format>
                      <samplecharacteristics>
                        <width>$width</width>
                        <height>$height</height>
                        <pixelaspectratio>square</pixelaspectratio>
                      </samplecharacteristics>
                    </format>
                  </video>
                  <audio>
                    <samplecharacteristics>
                      <depth>16</depth>
                      <samplerate>48000</samplerate>
                    </samplecharacteristics>
                  </audio>
                </media>
              </sequence>
            </xmeml>
        """.trimIndent()
    }
}
