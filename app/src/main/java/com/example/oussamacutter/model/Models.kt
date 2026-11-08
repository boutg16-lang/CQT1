package com.example.oussamacutter.model

import kotlinx.serialization.Serializable

@Serializable
enum class AiModelOption(val displayName: String, val badge: String) {
    GEMINI_2_FLASH("Gemini 2.0 Flash (موصى به)", "سريع ومجاني"),
    GEMINI_1_5_PRO("Gemini 1.5 Pro", "تحليل عميق"),
    GPT_4O("OpenAI GPT-4o", "سحابي"),
    LOCAL_LLM("Local Offline LLM", "بدون إنترنت")
}

@Serializable
enum class FramingMode(val title: String, val desc: String) {
    AUTO_9_16("اقتصاص ذكي 9:16", "تتبع وجه المتحدث الفردي"),
    SPLIT_SCREEN_2_FACES("شاشة مقسومة (وجهين)", "مناسب للحوارات والبودكاست"),
    ACTIVE_SPEAKER("تتبع المتكلم النشط", "التبديل التلقائي لمن يتحدث")
}

@Serializable
enum class SafetyActionMode(val label: String) {
    AUTO_BLEEP("كتم الكلمة تلقائياً (Bleep + ***)"),
    BLOCK_CLIP("استبعاد المقطع بالكامل"),
    WARN_ONLY("تنبيه فقط دون تعديل")
}

@Serializable
enum class RiskLevel(val labelAr: String, val colorHex: Long) {
    SAFE("آمن 100%", 0xFF10B981),
    MODERATE("مخاطرة متوسطة", 0xFFF59E0B),
    CRITICAL("خطر إنذار يوتيوب", 0xFFEF4444)
}

@Serializable
data class SafetyViolation(
    val term: String,
    val category: String,
    val severity: String,
    val timestampFormatted: String,
    val actionTaken: String
)

@Serializable
data class SafetyReport(
    val riskScore: Int, // 0 - 100
    val riskLevel: RiskLevel,
    val violations: List<SafetyViolation> = emptyList(),
    val bleepedWordsCount: Int = 0,
    val passedCopyrightCheck: Boolean = false,
    val passedLoudnessQc: Boolean = false,
    val summary: String = ""
)

@Serializable
data class SubtitleWord(
    val id: String,
    val text: String,
    val startMs: Long,
    val endMs: Long,
    var isBleeped: Boolean = false,
    val isHookKeyword: Boolean = false
)

@Serializable
data class AudioSettings(
    val selectedBgm: String = "Viral Podcast Beat",
    val bgmVolume: Float = 0.18f,
    val autoDucking: Boolean = true,
    val whooshSfx: Boolean = true,
    val popSfx: Boolean = true,
    val noiseReduction: Boolean = true
)

@Serializable
data class TranslationOptions(
    val sourceLang: String = "العربية",
    val targetLang: String = "English",
    val translatedTitle: String = "The Secret That Multiplies Your Productivity",
    val isDualCaptions: Boolean = false,
    val aiDubbingVoice: String = "صوت المتحدث الأصلي (مطابق)"
)

@Serializable
data class VisualEffects(
    val punchZoomOnHook: Boolean = true,
    val topProgressBar: Boolean = true,
    val dynamicEmojis: Boolean = true,
    val aspectRatio: String = "9:16" // 9:16, 1:1, 16:9
)

@Serializable
data class BRollSuggestion(
    val id: String,
    val keyword: String,
    val timestampMs: Long,
    val clipTitle: String,
    val category: String, // Tech, Finance, Nature, Success
    val durationSeconds: Int = 3,
    var isApplied: Boolean = true
)

@Serializable
data class RetentionPoint(
    val second: Int,
    val retentionPercent: Int,
    val annotation: String = ""
)

@Serializable
data class ThumbnailVariant(
    val id: String,
    val frameTimestampSec: Int,
    val catchyText: String,
    val badgeEmoji: String,
    val ctrPredictionPercent: Float,
    val isSelected: Boolean = false
)

@Serializable
data class AbTitleTest(
    val id: String,
    val titleA: String,
    val titleB: String,
    val eloScoreA: Int,
    val eloScoreB: Int,
    val predictedWinner: String,
    val insight: String
)

@Serializable
data class ClipSegment(
    val id: String,
    val title: String,
    val arabicHookTitle: String,
    val durationSeconds: Int,
    val viralScore: Int, // 0 - 100
    val hookStrength: Int, // 0 - 100
    val words: List<SubtitleWord>,
    val safetyReport: SafetyReport,
    var isApproved: Boolean = false,
    val targetPlatform: String = "TikTok & Shorts (9:16)",
    val audioLoudnessLUFS: Double = -14.2,
    val exportXmlReady: Boolean = true,
    val audioSettings: AudioSettings = AudioSettings(),
    val visualEffects: VisualEffects = VisualEffects(),
    val translation: TranslationOptions = TranslationOptions(),
    val bRollSuggestions: List<BRollSuggestion> = emptyList(),
    val retentionCurve: List<RetentionPoint> = emptyList(),
    val thumbnails: List<ThumbnailVariant> = emptyList()
)

@Serializable
data class PublishJob(
    val id: String,
    val clipId: String,
    val clipTitle: String,
    val platform: String, // YouTube Shorts, TikTok, Instagram
    val scheduledTime: String,
    val privacyStatus: String, // Private, Unlisted, Public
    val tags: List<String>,
    val status: String = "مجدول"
)

@Serializable
data class BatchJob(
    val id: String,
    val sourceName: String,
    val progress: Float, // 0.0 to 1.0
    val currentStep: String,
    val clipsGenerated: Int,
    val status: String, // PROCESSING, COMPLETED, PAUSED
    val startedAt: String
)

@Serializable
data class StudioProject(
    val id: String,
    val title: String,
    val sourceUrl: String,
    val durationFormatted: String,
    val clipsCount: Int,
    val dateCreated: String,
    val safetyPassed: Boolean
)

@Serializable
data class SystemHealth(
    val engineName: String = "OUSSAMA Viral Engine v8.0",
    val isGpuAvailable: Boolean = false,
    val gpuModel: String = "غير مفحوص",
    val transcriptionBackend: String = "تحليل محلي محدود — التفريغ الكامل غير مثبت",
    val activeLexiconTermsCount: Int = 0,
    val geminiStatus: String = "غير متصل — fallback محلي",
    val ffmpegLoaded: Boolean = false
)
