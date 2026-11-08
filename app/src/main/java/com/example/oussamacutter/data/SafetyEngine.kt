package com.example.oussamacutter.data

import android.content.Context
import android.util.Log
import com.example.oussamacutter.model.*
import kotlinx.serialization.json.*

object SafetyEngine {

    private const val TAG = "SafetyEngine"

    // Default Arabic & Dialect Sensitive Lexicon (Derived from safety_terms_arabic.json & stream_guard)
    private val memoryBlocklist = mutableSetOf(
        "خول", "خولات", "خنيث", "قحبة", "شرموطة", "عرص", "ديوث", "زنديق", "كلب",
        "ابن الحرام", "يا نجس", "تفجير", "اغتيال", "دموي", "انتحار", "ابادة", "ارهاب",
        "لعنة", "حقير", "سافل", "وسخ", "منحط", "ابن الكلب", "حيوان",
        "hate", "scam", "kill", "die", "bastard", "nazi", "terrorist"
    )

    fun initializeFromAssets(context: Context) {
        try {
            val termsArabicStream = context.assets.open("safety_terms_arabic.json")
            val jsonText = termsArabicStream.bufferedReader().use { it.readText() }
            val root = Json.parseToJsonElement(jsonText).jsonObject
            val termsArray = root["terms"]?.jsonArray
            termsArray?.forEach { elem ->
                val term = elem.jsonObject["term"]?.jsonPrimitive?.content
                if (!term.isNullOrBlank()) {
                    memoryBlocklist.add(term.trim())
                }
            }
            Log.d(TAG, "Successfully loaded ${memoryBlocklist.size} safety terms from assets.")
        } catch (e: Exception) {
            Log.e(TAG, "Error loading terms from assets, using built-in lexicon", e)
        }
    }

    fun addCustomTerm(term: String) {
        if (term.isNotBlank()) {
            memoryBlocklist.add(term.trim())
        }
    }

    val defaultBlocklist: List<String>
        get() = memoryBlocklist.toList()

    fun normalizeArabic(text: String): String {
        var str = text
        // Remove diacritics / harakat
        str = str.replace("[\\u064B-\\u065F\\u0670]".toRegex(), "")
        // Remove tatweel / kashida
        str = str.replace("\\u0640".toRegex(), "")
        // Normalize Alef
        str = str.replace("[إأآا]".toRegex(), "ا")
        // Normalize Teh Marbuta
        str = str.replace("ة", "ه")
        // Normalize Yeh / Alef Maksura
        str = str.replace("ى", "ي")
        return str.lowercase().trim()
    }

    fun evaluateText(
        inputText: String,
        actionMode: SafetyActionMode = SafetyActionMode.AUTO_BLEEP
    ): Pair<String, SafetyReport> {
        val violations = mutableListOf<SafetyViolation>()
        val words = inputText.split("\\s+".toRegex())
        val censoredWords = mutableListOf<String>()
        var bleepCount = 0

        for (word in words) {
            val cleanWord = word.trim('.', ',', '!', '?', ':', ';', '،', '"', '\'')
            val normalizedWord = normalizeArabic(cleanWord)

            val matchedTerm = memoryBlocklist.find { term ->
                val normalizedTerm = normalizeArabic(term)
                normalizedWord == normalizedTerm || (normalizedWord.length >= 4 && normalizedWord.contains(normalizedTerm))
            }

            if (matchedTerm != null) {
                bleepCount++
                val category = when {
                    matchedTerm in listOf("تفجير", "اغتيال", "دموي", "انتحار", "ارهاب", "kill", "terrorist") -> "عنف وتهديد"
                    matchedTerm in listOf("خول", "خنيث", "قحبة", "شرموطة", "عرص", "ديوث", "سافل", "حقير") -> "إساءة وألفاظ نابية"
                    else -> "مخالفة سياسة المحتوى والـ Strikes"
                }
                violations.add(
                    SafetyViolation(
                        term = matchedTerm,
                        category = category,
                        severity = if (category == "عنف وتهديد") "مرتفعة جداً" else "متوسطة",
                        timestampFormatted = "00:${(bleepCount * 6).toString().padStart(2, '0')}",
                        actionTaken = when (actionMode) {
                            SafetyActionMode.AUTO_BLEEP -> "تم كتم الصوت واستبدال الكلمة بـ ***"
                            SafetyActionMode.BLOCK_CLIP -> "المقطع محظور بالكامل"
                            SafetyActionMode.WARN_ONLY -> "تم رصد الكلمة وتوثيقها"
                        }
                    )
                )
                if (actionMode == SafetyActionMode.AUTO_BLEEP) {
                    censoredWords.add("***")
                } else {
                    censoredWords.add(word)
                }
            } else {
                censoredWords.add(word)
            }
        }

        val riskScore = when {
            violations.isEmpty() -> 0
            violations.any { it.severity == "مرتفعة جداً" } -> 85
            violations.size >= 3 -> 65
            else -> 35
        }

        val riskLevel = when {
            riskScore >= 70 -> RiskLevel.CRITICAL
            riskScore >= 30 -> RiskLevel.MODERATE
            else -> RiskLevel.SAFE
        }

        val report = SafetyReport(
            riskScore = riskScore,
            riskLevel = riskLevel,
            violations = violations,
            bleepedWordsCount = bleepCount,
            // These checks are not implemented in the Android client yet.
            // Keep them false so publishing cannot be mistaken for validated output.
            passedCopyrightCheck = false,
            passedLoudnessQc = false,
            summary = if (violations.isEmpty()) {
                "لم تُرصد كلمات محظورة في الفحص المحلي؛ فحص الحقوق وLoudness ما زال مطلوباً."
            } else {
                "تم رصد ${violations.size} كلمات مشبوهة وفق قاموس الأمان وتطبيق $actionMode؛ يلزم فحص بشري قبل النشر."
            }
        )

        return Pair(censoredWords.joinToString(" "), report)
    }
}
